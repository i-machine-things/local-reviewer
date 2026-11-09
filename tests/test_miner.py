import unittest

from miner import diffs, gh, mine, signals, testset
from miner.store import Store

PATCH = """@@ -10,3 +10,4 @@ def f():
 keep
-old
+new
+extra
 tail
@@ -40 +41 @@ def g():
-a
+b"""

FILE_HUNK = "@@ -1,2 +1,4 @@\n one\n+two\n+three\n four"
OTHER_HUNK = "@@ -5,1 +5,2 @@\n five\n+six"
LATER_HUNK = "@@ -2,1 +2,1 @@\n-two\n+two fixed"
BUG_COMMENT = "This is a race condition when two workers write at once."


def pr_row(**overrides):
    row = {
        "repo": "o/r", "number": 1, "title": "t", "merged_at": "2026-01-01", "base_oid": "base", "head_oid": "head",
        "author": "writer", "threads_truncated": 0,
    }
    return {**row, **overrides}


def thread_row(**overrides):
    row = {
        "root_id": 100, "repo": "o/r", "number": 1, "path": "src/a.py", "diff_side": "RIGHT", "line": 2,
        "original_line": 2, "original_start_line": None, "is_resolved": 1, "is_outdated": 1, "author": "reviewer",
        "author_type": "User", "created_at": "2026-01-01", "body": BUG_COMMENT, "diff_hunk": "",
        "original_commit": "c1", "replies_json": "[]",
    }
    return {**row, **overrides}


def seeded_store(thread=None, later=None, compare=None, pr=None):
    store = Store(":memory:")
    store.save_page("o/r", [pr or pr_row()], [thread or thread_row()], "x", 1, True)
    store.put_compare("o/r", "c1", "head", "ahead", later or [{"filename": "src/a.py", "patch": LATER_HUNK}])
    files = compare or [
        {"filename": "src/a.py", "patch": FILE_HUNK},
        {"filename": "src/b.py", "patch": OTHER_HUNK},
    ]
    store.put_compare("o/r", "base", "c1", "diverged", files)
    return store


def no_fetch(*_):
    raise AssertionError("the build should have used the cache")


class DiffTests(unittest.TestCase):
    def test_parses_ranges_including_omitted_counts(self):
        hunks = diffs.parse_patch(PATCH)
        self.assertEqual([(h.old_start, h.new_start, h.new_count) for h in hunks], [(10, 10, 4), (40, 41, 1)])

    def test_added_lines_are_new_file_numbers(self):
        self.assertEqual(diffs.parse_patch(PATCH)[0].added_new_lines(), {11, 12})

    def test_touched_lines_are_old_file_numbers(self):
        self.assertEqual(diffs.parse_patch(PATCH)[0].touched_old_lines(), {11, 12})

    def test_touches_range_respects_window(self):
        hunks = diffs.parse_patch(PATCH)
        self.assertTrue(diffs.touches_range(hunks, 15, 15))
        self.assertFalse(diffs.touches_range(hunks, 16, 16))

    def test_zero_old_count_insertion_lands_on_the_next_line(self):
        hunk = diffs.parse_patch("@@ -5,0 +6,1 @@\n+x")[0]
        self.assertEqual(hunk.touched_old_lines(), {6})
        self.assertTrue(diffs.touches_range([hunk], 9, 9))
        self.assertFalse(diffs.touches_range([hunk], 10, 10))

    def test_empty_patch(self):
        self.assertEqual(diffs.parse_patch(None), [])

    def test_no_newline_marker_is_ignored(self):
        hunk = diffs.parse_patch("@@ -1 +1 @@\n-a\n\\ No newline at end of file\n+b")[0]
        self.assertEqual(hunk.added_new_lines(), {1})
        self.assertEqual(hunk.changed_lines, 2)


class SignalTests(unittest.TestCase):
    def test_defects_are_categorized(self):
        self.assertEqual(signals.categorize("There is a data race on this counter here."), ["concurrency"])

    def test_nits_are_rejected_even_with_defect_words(self):
        self.assertEqual(signals.categorize("nit: this is a bug in the wording of the error"), [])

    def test_keywords_in_code_or_quotes_do_not_count(self):
        self.assertEqual(signals.categorize("> this has a race condition\n\nI think this reads fine to me."), [])
        self.assertEqual(signals.categorize("Maybe use this instead?\n```python\ndeadlock()\n```"), [])

    def test_bots_detected_by_type_or_name(self):
        self.assertTrue(signals.is_bot("anything", "Bot"))
        self.assertTrue(signals.is_bot("coderabbitai", "User"))
        self.assertFalse(signals.is_bot("alice", "User"))


class SplitTests(unittest.TestCase):
    def test_holdout_is_always_test(self):
        self.assertEqual({testset.split_for("o/r", n, True) for n in range(50)}, {"test"})

    def test_non_holdout_never_reaches_test(self):
        self.assertEqual({testset.split_for("o/r", n, False) for n in range(200)}, {"train", "dev"})


class PathTests(unittest.TestCase):
    def test_reviewable(self):
        self.assertTrue(testset.is_reviewable("src/core/net.cpp"))
        self.assertTrue(testset.is_reviewable("src/contest.py"))

    def test_not_reviewable(self):
        for path in ("tests/a.py", "pkg/x_test.go", "README.md", "third_party/z.c", "test_a.py"):
            self.assertFalse(testset.is_reviewable(path), path)


class BuildTests(unittest.TestCase):
    def test_confirmed_bug_gets_case_and_control(self):
        cases, funnel = testset.build_cases(seeded_store(), set(), fetch=no_fetch)
        self.assertEqual(sorted(c["kind"] for c in cases), ["bug", "clean"])
        bug = next(c for c in cases if c["kind"] == "bug")
        clean = next(c for c in cases if c["kind"] == "clean")
        self.assertEqual(bug["hunk"], FILE_HUNK)
        self.assertEqual(bug["truth"][0]["categories"], ["concurrency"])
        self.assertEqual(clean["path"], "src/b.py")
        self.assertEqual(clean["truth"], [])
        self.assertEqual(funnel["confirmed_bug"], 1)

    def test_pr_author_comment_is_excluded(self):
        cases, funnel = testset.build_cases(seeded_store(thread_row(author="writer")), set(), fetch=no_fetch)
        self.assertEqual(cases, [])
        self.assertEqual(funnel["not_independent_human"], 1)

    @staticmethod
    def rebased_store():
        store = seeded_store(compare=[{"filename": "src/a.py", "patch": FILE_HUNK}])
        # the whole-PR diff touches the commented line, which is exactly what must not be trusted after a rebase
        store.put_compare("o/r", "c1", "head", "diverged", [{"filename": "src/a.py", "patch": LATER_HUNK}])
        return store

    def test_unknown_author_is_excluded(self):
        store = seeded_store(thread_row(author=None, author_type=None))
        cases, funnel = testset.build_cases(store, set(), fetch=no_fetch)
        self.assertEqual(cases, [])
        self.assertEqual(funnel["not_independent_human"], 1)

    def test_repo_missing_from_config_is_skipped(self):
        cases, funnel = testset.build_cases(seeded_store(), set(), allowed={"other/repo"}, fetch=no_fetch)
        self.assertEqual(cases, [])
        self.assertEqual(funnel["repo_not_configured"], 1)

    def test_same_hunk_at_two_commits_gets_distinct_ids(self):
        hunk = diffs.parse_patch(FILE_HUNK)[0]
        pr = pr_row()
        first = testset._record(pr, "src/a.py", hunk, "bug", "train", "c1", [])
        second = testset._record(pr, "src/a.py", hunk, "bug", "train", "c2", [])
        self.assertNotEqual(first["id"], second["id"])

    def test_control_is_kept_out_of_a_long_comment_span(self):
        patch = f"{FILE_HUNK}\n@@ -40,1 +42,2 @@\n forty\n+added"
        store = seeded_store(compare=[{"filename": "src/a.py", "patch": patch}])
        span = thread_row(root_id=103, body="looks good", original_start_line=20, original_line=80)
        store.save_page("o/r", [pr_row()], [span], "x", 1, True)
        cases, _ = testset.build_cases(store, set(), fetch=no_fetch)
        self.assertEqual([c["kind"] for c in cases], ["bug"])

    def test_control_sees_a_later_rename(self):
        later = [
            {"filename": "src/a.py", "patch": LATER_HUNK},
            {"filename": "src/new.py", "previous_filename": "src/b.py", "patch": "@@ -5,1 +5,1 @@\n-five\n+5"},
        ]
        cases, _ = testset.build_cases(seeded_store(later=later), set(), fetch=no_fetch)
        self.assertEqual([c["kind"] for c in cases], ["bug"])

    def test_control_needs_a_complete_file_list(self):
        padding = [{"filename": f"src/p{i}.py", "patch": "@@ -900 +900 @@\n-a\n+b"} for i in range(299)]
        later = [{"filename": "src/a.py", "patch": LATER_HUNK}, *padding]
        cases, _ = testset.build_cases(seeded_store(later=later), set(), fetch=no_fetch)
        self.assertEqual([c["kind"] for c in cases], ["bug"])

    def test_later_comment_on_a_chosen_hunk_is_kept_past_the_cap(self):
        patch = "\n".join(f"@@ -{n},1 +{n},2 @@\n ctx\n+new{n}" for n in (100, 200, 300))
        hunks = "\n".join(f"@@ -{n},1 +{n},1 @@\n-new\n+fixed" for n in (2, 101, 201, 301))
        store = seeded_store(
            compare=[{"filename": "src/a.py", "patch": f"{FILE_HUNK}\n{patch}"}],
            later=[{"filename": "src/a.py", "patch": hunks}],
        )
        extra = [thread_row(root_id=200 + n, original_line=n + 1) for n in (100, 200)]
        extra.append(thread_row(root_id=500, original_line=3))
        store.save_page("o/r", [pr_row()], extra, "x", 1, True)
        cases, _ = testset.build_cases(store, set(), fetch=no_fetch)
        first = next(c for c in cases if c["kind"] == "bug" and c["hunk"] == FILE_HUNK)
        self.assertEqual(len(first["truth"]), 2)
        self.assertLessEqual(sum(c["kind"] == "bug" for c in cases), testset.MAX_BUG_CASES_PER_PR)

    def test_rebased_pr_counts_only_a_real_change_to_the_file(self):
        versions = {"c1": "one\ntwo\nthree\nfour", "head": "one\ntwo fixed\nthree\nfour"}
        cases, funnel = testset.build_cases(
            self.rebased_store(), set(), fetch=no_fetch, fetch_file=lambda _r, oid, _p: versions[oid]
        )
        self.assertEqual([c["kind"] for c in cases], ["bug"])
        self.assertEqual(funnel["checked_by_file_diff"], 1)

    def test_rebased_pr_with_identical_file_is_not_a_fix(self):
        cases, funnel = testset.build_cases(
            self.rebased_store(), set(), fetch=no_fetch, fetch_file=lambda *_: "one\ntwo\nthree\nfour"
        )
        self.assertEqual(cases, [])
        self.assertEqual(funnel["line_unchanged_before_merge"], 1)

    def test_rebased_file_diff_is_cached(self):
        calls = []
        store = self.rebased_store()

        def read(_repo, oid, _path):
            calls.append(oid)
            return "same"

        for _ in range(2):
            testset.build_cases(store, set(), fetch=no_fetch, fetch_file=read)
        self.assertEqual(calls, ["c1", "head"])

    def test_unchanged_line_is_not_a_bug(self):
        far_away = [{"filename": "src/a.py", "patch": "@@ -90,1 +90,1 @@\n-x\n+y"}]
        cases, funnel = testset.build_cases(seeded_store(later=far_away), set(), fetch=no_fetch)
        self.assertEqual(cases, [])
        self.assertEqual(funnel["line_unchanged_before_merge"], 1)

    def test_comment_on_context_line_is_rejected(self):
        cases, funnel = testset.build_cases(seeded_store(thread_row(original_line=1)), set(), fetch=no_fetch)
        self.assertEqual(cases, [])
        self.assertEqual(funnel["comment_not_on_added_line"], 1)

    def test_control_can_be_a_quiet_hunk_in_the_commented_file(self):
        patch = f"{FILE_HUNK}\n@@ -80,1 +82,2 @@\n eighty\n+added"
        store = seeded_store(compare=[{"filename": "src/a.py", "patch": patch}])
        cases, _ = testset.build_cases(store, set(), fetch=no_fetch)
        clean = next(c for c in cases if c["kind"] == "clean")
        self.assertTrue(clean["hunk"].startswith("@@ -80"))

    def test_control_is_kept_away_from_other_comments(self):
        patch = f"{FILE_HUNK}\n@@ -30,1 +32,2 @@\n thirty\n+added"
        store = seeded_store(compare=[{"filename": "src/a.py", "patch": patch}])
        store.save_page("o/r", [pr_row()], [thread_row(root_id=102, body="looks good", original_line=33)], "x", 1, True)
        cases, _ = testset.build_cases(store, set(), fetch=no_fetch)
        self.assertEqual([c["kind"] for c in cases], ["bug"])

    def test_control_never_comes_from_a_hunk_changed_later(self):
        later = [
            {"filename": "src/a.py", "patch": LATER_HUNK},
            {"filename": "src/b.py", "patch": "@@ -9 +9 @@\n-p\n+q"},
        ]
        cases, _ = testset.build_cases(seeded_store(later=later), set(), fetch=no_fetch)
        self.assertEqual([c["kind"] for c in cases], ["bug"])

    def test_truncated_threads_get_no_control(self):
        store = seeded_store(pr=pr_row(threads_truncated=1))
        cases, _ = testset.build_cases(store, set(), fetch=no_fetch)
        self.assertEqual([c["kind"] for c in cases], ["bug"])

    def test_two_comments_on_one_hunk_merge(self):
        store = seeded_store()
        store.save_page("o/r", [pr_row()], [thread_row(root_id=101, original_line=3)], "x", 1, True)
        cases, _ = testset.build_cases(store, set(), fetch=no_fetch)
        bugs = [c for c in cases if c["kind"] == "bug"]
        self.assertEqual(len(bugs), 1)
        self.assertEqual(len(bugs[0]["truth"]), 2)

    def test_unavailable_compare_is_cached(self):
        calls = []

        def missing(*args):
            calls.append(args)
            raise gh.NotAvailable("HTTP 404")

        store = Store(":memory:")
        store.save_page("o/r", [pr_row()], [thread_row()], "x", 1, True)
        for _ in range(2):
            cases, _ = testset.build_cases(store, set(), fetch=missing, fetch_file=missing)
            self.assertEqual(cases, [])
        self.assertEqual(len(calls), 2)  # one compare, then one file read before the file diff gives up


class MineTests(unittest.TestCase):
    @staticmethod
    def node(number):
        return {
            "number": number, "title": "t", "mergedAt": "2026-01-01", "baseRefOid": "b", "headRefOid": "h",
            "author": None,
            "reviewThreads": {"pageInfo": {"hasNextPage": False}, "nodes": [{"comments": {"nodes": []}}]},
        }

    def test_stores_original_coordinates_not_current_ones(self):
        node = self.node(7)
        comment = {
            "databaseId": 9, "createdAt": "2026-01-02", "body": "b", "diffHunk": "@@", "originalCommit": {"oid": "c9"},
            "author": {"__typename": "User", "login": "r"},
        }
        node["reviewThreads"]["nodes"] = [{
            "isResolved": True, "isOutdated": True, "path": "a.py", "diffSide": "RIGHT", "line": 50, "originalLine": 12,
            "startLine": 48, "originalStartLine": 10, "comments": {"nodes": [comment]},
        }]
        _, threads = mine.parse_page("o/r", [node])
        self.assertEqual(
            (threads[0]["original_line"], threads[0]["original_start_line"], threads[0]["line"]), (12, 10, 50)
        )
        self.assertEqual(threads[0]["original_commit"], "c9")

    def test_resumes_from_saved_cursor(self):
        cursors = []

        def fetch(_repo, _first, cursor):
            cursors.append(cursor)
            number = len(cursors)
            return {"pageInfo": {"hasNextPage": True, "endCursor": f"c{number}"}, "nodes": [self.node(number)]}

        store = Store(":memory:")
        mine.mine_repo(store, "o/r", 1, fetch=fetch, log=lambda _: None)
        mine.mine_repo(store, "o/r", 2, fetch=fetch, log=lambda _: None)
        self.assertEqual(cursors, [None, "c1"])

    def test_page_size_recovers_after_a_failure(self):
        sizes = []

        def fetch(_repo, first, _cursor):
            sizes.append(first)
            if len(sizes) == 1:
                raise gh.GhError("HTTP 502")
            more = len(sizes) < 3
            return {"pageInfo": {"hasNextPage": more, "endCursor": "c"}, "nodes": [self.node(len(sizes))]}

        mine.mine_repo(Store(":memory:"), "o/r", 100, fetch=fetch, log=lambda _: None)
        self.assertEqual(sizes, [25, 12, 24])

    def test_page_failure_shrinks_request(self):
        sizes = []

        def fetch(_repo, first, _cursor):
            sizes.append(first)
            if first > 12:
                raise gh.GhError("HTTP 502")
            return {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": [self.node(1)]}

        mine.mine_repo(Store(":memory:"), "o/r", 100, fetch=fetch, log=lambda _: None)
        self.assertEqual(sizes, [25, 12])


class GhRunTests(unittest.TestCase):
    class Proc:
        def __init__(self, returncode, stdout="", stderr=""):
            self.returncode, self.stdout, self.stderr = returncode, stdout, stderr

    def runner(self, results):
        pending = iter(results)
        return lambda *_a, **_k: next(pending)

    def test_retries_transient_then_succeeds(self):
        runner = self.runner([self.Proc(1, stderr="HTTP 502"), self.Proc(0, stdout="ok")])
        self.assertEqual(gh.run(["x"], sleep=lambda _: None, runner=runner), "ok")

    def test_missing_object_raises_without_retry(self):
        with self.assertRaises(gh.NotAvailable):
            gh.run(["x"], sleep=lambda _: None, runner=self.runner([self.Proc(1, stderr="HTTP 404")]))

    def test_rate_limit_waits_without_spending_retries(self):
        limited = self.Proc(1, stderr="gh: API rate limit already exceeded")
        waits = []
        runner = self.runner([limited, limited, limited, self.Proc(0, stdout="ok")])
        self.assertEqual(gh.run(["x"], retries=0, sleep=waits.append, runner=runner), "ok")
        self.assertEqual(waits.count(gh.RATE_LIMIT_WAIT_SECONDS), 3)

    def test_rate_limit_eventually_gives_up(self):
        limited = [self.Proc(1, stderr="API rate limit exceeded")] * (gh.MAX_RATE_LIMIT_WAITS + 1)
        with self.assertRaises(gh.GhError):
            gh.run(["x"], retries=0, sleep=lambda _: None, runner=self.runner(limited))

    def test_permanent_error_raises(self):
        with self.assertRaises(gh.GhError):
            gh.run(["x"], sleep=lambda _: None, runner=self.runner([self.Proc(1, stderr="HTTP 401: bad creds")]))


if __name__ == "__main__":
    unittest.main()
