# Coding Notes

> Notes are 300 characters or less, grouped by topic.

## Easter Eggs

- `python3 -m miner --rubber-duck` prints a duck. It is checked before argparse in `miner/__main__.py`, so it stays out of `--help`.

## Test Set Integrity

- Check the GitHub compare `status` before trusting a commit-to-head diff. After a rebase it spans the whole PR and would "confirm" nearly every comment as fixed.
- Never send a case's `truth` to a model under test, and keep the `test` split away from teacher and tuning steps.
