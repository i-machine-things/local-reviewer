"""Turn raw review threads into bug cases and matching presumed-clean controls."""
import collections
import hashlib
import re

from . import diffs, gh, signals
from .store import UNAVAILABLE

LANGUAGES = {
    ".py": "python", ".rs": "rust", ".go": "go", ".cs": "csharp", ".c": "c", ".h": "c", ".cc": "cpp", ".cpp": "cpp",
    ".cxx": "cpp", ".hpp": "cpp", ".ts": "typescript", ".tsx": "typescript", ".js": "javascript", ".java": "java",
}
EXCLUDED_PATH = re.compile(
    r"(^|/)(tests?|__tests__|testing|testdata|fixtures|vendor|vendored|third_party|external|generated)(/|$)|"
    r"(^|/)test_[^/]*$|_test\.[^/.]+$|\.test\.[^/.]+$|tests?\.cs$|\.min\.js$|\.g\.cs$|\.pb\.(go|cc|h)$",
    re.IGNORECASE,
)
MAX_CHANGED_LINES = 150
MAX_BUG_CASES_PER_PR = 3
CONTROL_MARGIN = 10  # review-thread line numbers drift between commits, so keep controls well clear of them


def language_of(path):
    match = re.search(r"\.[^./]+$", path)
    return LANGUAGES.get(match.group(0).lower()) if match else None


def is_reviewable(path):
    return language_of(path) is not None and not EXCLUDED_PATH.search(path)


def split_for(repo, number, holdout):
    """Holdout repos are test-only. The rest split by PR, stably, so rebuilding never moves a case between splits."""
    if holdout:
        return "test"
    digest = hashlib.sha256(f"{repo}#{number}".encode()).digest()
    return "dev" if digest[0] % 5 == 0 else "train"


def cached_compare(store, repo, base, head, fetch):
    cached = store.get_compare(repo, base, head)
    if cached is not None:
        return cached
    try:
        result = fetch(repo, base, head)
    except gh.NotAvailable:
        store.put_compare(repo, base, head, UNAVAILABLE, [])
        return {"status": UNAVAILABLE, "files": []}
    store.put_compare(repo, base, head, result["status"], result["files"])
    return result


def patch_for(compare, path):
    return next((f.get("patch") for f in compare["files"] if f["filename"] == path), None)


def _case_id(repo, number, path, hunk, kind):
    return hashlib.sha256(f"{kind}|{repo}#{number}|{path}|{hunk.text}".encode()).hexdigest()[:16]


def _find_bug(store, pr, thread, fetch, funnel):
    """Return (hunk, truth, later_compare, compare) if the thread is a confirmed defect, counting why others fail."""
    repo, path, line = pr["repo"], thread["path"], thread["original_line"]
    if signals.is_bot(thread["author"], thread["author_type"]) or thread["author"] == pr["author"]:
        return funnel.update(["not_independent_human"])
    if thread["diff_side"] != "RIGHT" or not line or not thread["original_commit"]:
        return funnel.update(["not_on_new_code"])
    if not is_reviewable(path):
        return funnel.update(["not_reviewable_file"])
    categories = signals.categorize(thread["body"])
    if not categories:
        return funnel.update(["no_defect_signal"])
    funnel.update(["defect_signal"])
    commit = thread["original_commit"]
    if commit == pr["head_oid"]:
        return funnel.update(["no_later_commit"])

    later = cached_compare(store, repo, commit, pr["head_oid"], fetch)
    if later["status"] not in ("ahead", "identical"):
        # After a rebase this comparison spans the whole PR, so it would "confirm" nearly every comment.
        return funnel.update(["commit_not_ancestor_of_head"])
    start = thread["original_start_line"] or line
    fix_hunks = diffs.parse_patch(patch_for(later, path))
    if not diffs.touches_range(fix_hunks, start, line):
        return funnel.update(["line_unchanged_before_merge"])

    compare = cached_compare(store, repo, pr["base_oid"], commit, fetch)
    hunk = diffs.hunk_at_new_line(diffs.parse_patch(patch_for(compare, path)), line)
    if hunk is None:
        return funnel.update(["hunk_not_found"])
    if not hunk.added_new_lines() & set(range(start, line + 1)):
        return funnel.update(["comment_not_on_added_line"])
    if hunk.changed_lines > MAX_CHANGED_LINES:
        return funnel.update(["hunk_too_large"])
    funnel.update(["confirmed_bug"])
    truth = {
        "comment_id": thread["root_id"],
        "start_line": start,
        "line": line,
        "categories": categories,
        "comment": thread["body"],
        "fix_patch": "\n".join(h.text for h in diffs.overlapping_hunks(fix_hunks, start, line)),
    }
    return hunk, truth, later, compare


def _is_quiet(hunk, comment_lines, later_hunks):
    """True if no review thread sits near this hunk and nothing in it changed before merge.

    A thread with no line number could be anywhere in the file, so it disqualifies every hunk there.
    """
    near = range(hunk.new_start - CONTROL_MARGIN, hunk.new_end + CONTROL_MARGIN + 1)
    if any(line is None or line in near for line in comment_lines):
        return False
    return not diffs.touches_range(later_hunks, hunk.new_start, hunk.new_end)


def _pick_control(bug_hunk, later, compare, threads, used):
    """Pick the quiet hunk closest in size to the bug hunk."""
    comment_lines = collections.defaultdict(list)
    for thread in threads:
        comment_lines[thread["path"]].append(thread["original_line"])
    later_patches = {f["filename"]: f.get("patch") for f in later["files"]}
    candidates = []
    for file in compare["files"]:
        path = file["filename"]
        if not is_reviewable(path) or (path in later_patches and not later_patches[path]):
            continue  # a changed file with no patch is too big or binary, so what changed in it is unknown
        later_hunks = diffs.parse_patch(later_patches.get(path))
        for hunk in diffs.parse_patch(file.get("patch")):
            key = (path, hunk.new_start)
            if key in used or not hunk.added_new_lines() or hunk.changed_lines > MAX_CHANGED_LINES:
                continue
            if _is_quiet(hunk, comment_lines[path], later_hunks):
                candidates.append((abs(hunk.changed_lines - bug_hunk.changed_lines), path, hunk.new_start, hunk))
    if not candidates:
        return None
    _, path, _, hunk = min(candidates, key=lambda c: c[:3])
    used.add((path, hunk.new_start))
    return path, hunk


def _record(pr, path, hunk, kind, split, commit, truth):
    return {
        "id": _case_id(pr["repo"], pr["number"], path, hunk, kind),
        "kind": kind,
        "split": split,
        "repo": pr["repo"],
        "pr": pr["number"],
        "merged_at": pr["merged_at"],
        "path": path,
        "language": language_of(path),
        "commit": commit,
        "hunk": hunk.text,
        "truth": truth,
    }


def build_cases(store, holdouts, fetch=gh.fetch_compare, log=print):
    """Return (cases, funnel). The funnel shows where candidates drop out, which is how the filters get tuned."""
    cases, funnel = [], collections.Counter()
    for index, pr in enumerate(store.prs(), 1):
        if index % 500 == 0:
            log(f"build: {index} PRs, {funnel['confirmed_bug']} confirmed bugs")
        funnel.update(["prs"])
        threads = store.threads_for(pr["repo"], pr["number"])
        funnel.update({"threads": len(threads)})
        split = split_for(pr["repo"], pr["number"], pr["repo"] in holdouts)
        by_hunk, used_controls, controls = {}, set(), []
        for thread in threads:
            if len(by_hunk) >= MAX_BUG_CASES_PER_PR:
                funnel.update(["over_pr_cap"])
                break
            found = _find_bug(store, pr, thread, fetch, funnel)
            if not found:
                continue
            hunk, truth, later, compare = found
            key = (thread["path"], hunk.new_start, thread["original_commit"])
            if key in by_hunk:
                by_hunk[key]["truth"].append(truth)
                continue
            by_hunk[key] = _record(pr, thread["path"], hunk, "bug", split, thread["original_commit"], [truth])
            if pr["threads_truncated"]:
                continue  # an unseen thread could be sitting on any file, so no hunk here is known to be uncommented
            control = _pick_control(hunk, later, compare, threads, used_controls)
            if control:
                path, control_hunk = control
                controls.append(_record(pr, path, control_hunk, "clean", split, thread["original_commit"], []))
                funnel.update(["control"])
        cases.extend(by_hunk.values())
        cases.extend(controls)
    return cases, funnel
