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

    def test_rebased_commit_is_not_treated_as_a_fix(self):
        store = seeded_store()
        store.put_compare("o/r", "c1", "head", "diverged", [{"filename": "src/a.py", "patch": LATER_HUNK}])
        cases, funnel = testset.build_cases(store, set(), fetch=no_fetch)
        self.assertEqual(cases, [])
        self.assertEqual(funnel["commit_not_ancestor_of_head"], 1)

    def test_unchanged_line_is_not_a_bug(self):
        far_away = [{"filename": "src/a.py", "patch": "@@ -90,1 +90,1 @@\n-x\n+y"}]
        cases, funnel = testset.build_cases(seeded_store(later=far_away), set(), fetch=no_fetch)
        self.assertEqual(cases, [])
        self.assertEqual(funnel["line_unchanged_before_merge"], 1)

    def test_comment_on_context_line_is_rejected(self):
        cases, funnel = testset.build_cases(seeded_store(thread_row(original_line=1)), set(), fetch=no_fetch)
        self.assertEqual(cases, [])
        self.assertEqual(funnel["comment_not_on_added_line"], 1)

    def test_control_never_comes_from_a_file_changed_later(self):
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
            cases, _ = testset.build_cases(store, set(), fetch=missing)
            self.assertEqual(cases, [])
        self.assertEqual(len(calls), 1)


class MineTests(unittest.TestCase):
    @staticmethod
    def node(number):
        return {
            "number": number, "title": "t", "mergedAt": "2026-01-01", "baseRefOid": "b", "headRefOid": "h",
            "author": None,
            "reviewThreads": {"pageInfo": {"hasNextPage": False}, "nodes": [{"comments": {"nodes": []}}]},
        }

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

    def test_permanent_error_raises(self):
        with self.assertRaises(gh.GhError):
            gh.run(["x"], sleep=lambda _: None, runner=self.runner([self.Proc(1, stderr="HTTP 401: bad creds")]))


if __name__ == "__main__":
    unittest.main()
