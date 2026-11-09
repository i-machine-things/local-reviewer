# Coding Best Practices & Reminders

> **Style rule:** Notes must be clear and concise — no filler, no restating the obvious — but long enough to carry the actual lesson: what went wrong, why, and the fix. A one-line rule without that context gets re-broken the next time someone hits a similar case. Group by topic, not by date. Whenever a PR review (CodeRabbit or human) catches a mistake, add or amend a note here right away so it isn't repeated.

## Resource Cleanup & Temporary Files

**IMPORTANT**: Always add proper cleanup code in programs to prevent lingering temp files after closing.

### Best Practices:

1. **GUI Applications (PyQt, Tkinter, etc.)**
   - Implement `closeEvent()` handler to cleanup resources on window close
   - Call `deleteLater()` on widgets to ensure proper Qt object cleanup
   - Process pending events with `app.processEvents()` before exit

2. **File Handling**
   - Use context managers (`with` statements) for file operations
   - Explicitly close file handles when not using context managers
   - Release file locks before program exit
   - Clean up temporary files in temp directories

3. **Background Threads & Workers**
   - Stop and join all background threads before exit
   - Cancel any pending operations
   - Clean up thread-specific resources

4. **Testing Cleanup**
   - After closing the program, verify the executable can be:
     - Deleted immediately
     - Moved to another location
     - Replaced with a new version
   - If the file is locked, cleanup code is missing or incomplete

### Example Implementation (PyQt6):

```python
def closeEvent(self, event):
    """Handle window close event - ensure proper cleanup"""
    # Cleanup modules/components
    for module in self.modules:
        try:
            module.cleanup()
        except Exception as e:
            print(f"Error cleaning up module: {e}")

    # Save state
    self.save_settings()

    # Accept close event
    event.accept()
    QApplication.quit()

def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()

    exit_code = app.exec()

    # Final cleanup
    window.deleteLater()
    app.processEvents()

    sys.exit(exit_code)
```

### PyInstaller Specific:

In `.spec` file, add:
```python
exe = EXE(
    ...
    bootloader_ignore_signals=True,  # Better cleanup handling
    ...
)
```

## Date: 2025-12-16
This note was created based on issues encountered with PyInstaller executables remaining locked after closing.

## Branch Protection

- **GitHub branch protection requires a public repo, or GitHub Pro/Team/Enterprise, on private repos** — `PUT .../branches/.../protection` 403s with "Upgrade to GitHub Pro or make this repository public" otherwise. There's no automation around this now (the downstream sync workflow that used to warn-and-continue on this was removed — it never synced reliably); expect the 403 by hand on a private repo and don't treat it as a bug.
- **Don't set required status-check contexts before the repo has CI that produces them.** A required context that never reports a status permanently blocks merges. `ci.yml` needs per-project tailoring (see "tailor or language-agnostic" above) before its job names are known, so when setting branch protection on a new repo, start with what's safe without knowing them yet (no force-push, no deletion, enforced for admins), and add required checks once that repo's own CI is customized and green.
- **Avoid `required_pull_request_reviews` on a solo-maintained repo.** GitHub won't let an author approve their own PR, so requiring even 1 approval with no other reviewer deadlocks every merge. Rely on required status checks (which do block direct pushes too, since a bare push's commit never gets the PR-triggered check runs) instead of an approval-count gate.
- **Renaming a CI job blocks every PR if its old name is a required check.** Required contexts match the check-run name exactly (including any matrix suffix), and the old one never reports again. Change the protection's required contexts in the same change that renames or splits a job, and confirm the PR shows as mergeable.

## Claude Code Hooks

- **Inspect every git invocation in a compound command.** `git add f && git commit` starts with `add`; split on `&&`, `||`, `;`, `|` and newlines and check each simple command, or the commit check never runs.
- **Plain stdout from a PreToolUse hook is only logged.** To show a warning, print JSON with `systemMessage` (user) and `hookSpecificOutput.additionalContext` (Claude); exit 0 without a permission decision so the call is not blocked.
- **Scope pattern checks to the file types they apply to.** A Python anti-pattern check must skip Markdown, or the docs that show the bad example trip it.

## General Style Notes

- **Keep lines under 120 characters.** Long lines are hard to review side-by-side in a diff or split editor pane, and tend to signal a line doing too many things at once. Wrap or break up expressions rather than letting them run long.
- **Add docstrings to explain code.** Focus on *why* a function/class exists or *why* it does something non-obvious — the code itself already shows *what* it does. A docstring worth writing usually covers intent, assumptions, edge cases, or a gotcha a future reader would otherwise have to rediscover the hard way.
- **Strip docstrings when building a release.** Release builds don't need internal rationale shipped alongside the binary — it bloats the artifact and can leak implementation notes you didn't mean to publish. Do not use Python's `-OO` by default: it also removes `assert` statements, so checks written as asserts vanish from the release. Use a step that strips only docstrings (for example a minifier's remove-literal-statements option), or confirm nothing relies on `assert` first.

## Shared Files & Lists

A file that many unrelated PRs each add one entry to (a changelog, an attribution table, a package
index, a message catalog) is a conflict magnet — two PRs adding neighbouring lines fight over the
same diff hunk even though the change is logically independent. Three patterns, in order of how
heavy a project needs:

- **Keep the list append-only and sorted, nothing else.** If every entry is independent and order
  doesn't carry meaning beyond sorting, enforce "new entries go into the existing sorted position,
  never a new unsorted block at the end" with a lint or a one-line review check. Cheapest option;
  fine for a file that gets touched occasionally.
- **Split into a `pending/` directory, folded by a scheduled job.** For a file many PRs touch in
  the same release window, have each PR add its own small file (`pending/<slug>.md`) instead of
  editing the shared file directly, and run a scheduled job on the default branch that folds every
  pending file into the real one and deletes them. Two PRs can never collide, because neither edits
  the shared file — only the fold job does, and it runs after both have merged.
- **Regenerate it entirely on the default branch, never in a PR.** If the file is fully derived from
  something else in the repo (a dependency manifest, the command/route registry), don't let a PR
  edit it by hand at all — a scheduled or push-triggered job on the default branch regenerates and
  commits it after merge. No PR's version of it is ever "the" version, so nothing to conflict.

## Releases

- **A green release rehearsal proves the pipeline, not the artifact.** Install, import or run what the rehearsal built, the way a user will, before tagging (Rule 4, "Rehearse before releasing").

## Error Handling

- **Treat all input as hostile** — command parameters, file contents, config, network responses, environment variables, anything that didn't originate as an already-validated internal value. Validate it before using it; a crash on bad input is a defect regardless of whose fault the bad input was.
- **Fail with the language's own error-handling idiom** (an exception, a `Result`/`Either`, an error return) — never a bare `panic!`/`unwrap`/`assert` outside tests or a genuinely-unreachable internal invariant. An unfinished feature returns a clear "not supported yet" error; it never crashes.
- **A crash fix's regression test replays the actual failing input**, not just the surrounding logic. A bug that crashed once on a specific input crashes the same way again if nothing exercises that exact input.

## Testing

- **A regression test for a bug goes into the existing test file for that area or feature, never a new file named after the issue or bug number.** Tests organized by subject stay navigable as the codebase grows; tests organized by bug-tracker history don't. See Rule 3 in `CLAUDE.md` for evidence capture and the known-failing-test logging rule.

## Documentation

- **Example code or commands shown in a README or doc file should be exercised by CI**, even minimally (a doctest, a smoke-test script, a build step that runs the example). An unverified example silently drifts from reality as the code changes, and nobody notices until a user hits it.

## Easter Eggs

- `python3 -m miner --rubber-duck` prints a duck. It is checked before argparse in `miner/__main__.py`, so it stays out of `--help`.

## Test Set Integrity

- Check the GitHub compare `status` before trusting a commit-to-head diff. After a rebase it spans the whole PR, so `LaterChanges` diffs the two file versions instead.
- Never send a case's `truth` to a model under test, and keep the `test` split away from teacher and tuning steps.
- Build only repos named in the config. A former holdout left in the store would otherwise land in train or dev and unseal its test cases.
- Controls must clear a review thread's whole line span, not just its end line. Match later renames through `previous_filename`, and treat a compare listing GitHub's full 300 files as incomplete.
- A comment with no known author can't count as human ground truth. Deleted accounts return a null author, and that includes deleted bots.
- In `@@ -a,0 +b @@` the old side names the line before the insertion, so inserted lines sit at `a + 1`.

## Deferred Review Findings (PR #1)

- Not fixed, rare or low value: fixes made by deleting a file on a rebased PR, and hunks whose base later absorbed the reviewed commit. Both only lose a few cases.
- Not fixed, covered by the planned classifier and audit: changes pushed before a comment on an older commit, and affirmative wording like "this is thread-safe".
- Refresh mode for newer PRs is deferred to the feedback-loop milestone, because the corpus only needs backfill until then.
- Findings on the template's own files belong upstream in `i-machine-things/.claude`, so this repo stays in step with its siblings.
