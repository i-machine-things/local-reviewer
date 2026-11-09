# local-reviewer

A private code reviewer that runs on a local GPU and aims to catch real bugs while staying quiet about style.
[docs/PLAN.md](docs/PLAN.md) has the full plan. Only the first stage exists so far.

## Corpus and test set

The miner reads merged pull requests from the public repos in `repos.toml` and looks for human review comments that
describe a defect and were followed by a change to that line. It needs Python 3.11+ and a logged-in `gh`.

```bash
export REVIEWER_DATA=/path/to/storage
python3 -m miner mine --max-prs 500     # safe to stop and rerun
python3 -m miner build                  # writes cases/train.jsonl, dev.jsonl, test.jsonl and report.json
python3 -m miner audit --split dev -n 50
```

`report.json` shows how many candidates each filter dropped. The `audit` command prints a repeatable sample for hand
checking.

Run the tests with `python3 -m unittest discover tests`.
