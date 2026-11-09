"""Command line: mine GitHub, build the case files, and print audit samples."""
import argparse
import collections
import json
import os
import pathlib
import random
import sys
import tomllib

from .mine import mine_repo
from .store import Store
from .testset import build_cases

SPLITS = ("train", "dev", "test")
DUCK = r"""
   __
 <(o )___   Have you tried explaining it to me?
  ( ._> /
   `---'
"""


def load_config(path):
    with open(path, "rb") as handle:
        return tomllib.load(handle)["repo"]


def write_atomic(path, lines):
    """Replace a file in one step, so a failed build can never leave a truncated dataset behind."""
    temp = path.with_suffix(path.suffix + ".tmp")
    with open(temp, "w", encoding="utf-8") as handle:
        handle.writelines(line + "\n" for line in lines)
    os.replace(temp, path)


def cmd_mine(args):
    store = Store(args.data / "corpus.db")
    try:
        for repo in load_config(args.config):
            if not args.repo or repo["name"] in args.repo:
                mine_repo(store, repo["name"], args.max_prs)
    finally:
        store.close()


def summarize(cases):
    counts = collections.Counter((c["split"], c["kind"]) for c in cases)
    by_repo = collections.Counter((c["repo"], c["kind"]) for c in cases)
    by_category = collections.Counter(
        category for c in cases if c["kind"] == "bug" for category in {x for t in c["truth"] for x in t["categories"]}
    )
    return {
        "counts": {f"{split}/{kind}": n for (split, kind), n in sorted(counts.items())},
        "by_repo": {f"{repo}/{kind}": n for (repo, kind), n in sorted(by_repo.items())},
        "by_category": dict(by_category.most_common()),
    }


def cmd_build(args):
    holdouts = {repo["name"] for repo in load_config(args.config) if repo.get("holdout")}
    store = Store(args.data / "corpus.db")
    try:
        cases, funnel = build_cases(store, holdouts)
    finally:
        store.close()
    out = args.data / "cases"
    out.mkdir(exist_ok=True)
    for split in SPLITS:
        write_atomic(out / f"{split}.jsonl", (json.dumps(c) for c in cases if c["split"] == split))
    report = {"funnel": dict(funnel.most_common()), **summarize(cases)}
    write_atomic(out / "report.json", [json.dumps(report, indent=2)])
    print(json.dumps(report, indent=2))


def cmd_audit(args):
    """Print a reproducible sample. Hand-checking these is what tells us whether the filters can be trusted."""
    with open(args.data / "cases" / f"{args.split}.jsonl", encoding="utf-8") as handle:
        bugs = [c for c in map(json.loads, handle) if c["kind"] == "bug"]
    for case in random.Random(args.seed).sample(bugs, min(args.n, len(bugs))):
        print(f"\n{'=' * 100}\n{case['id']}  {case['repo']}#{case['pr']}  {case['path']}")
        print(case["hunk"])
        for truth in case["truth"]:
            print(f"\n--- COMMENT at line {truth['line']} {truth['categories']}\n{truth['comment']}")
            print(f"\n--- FIX\n{truth['fix_patch']}")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["--rubber-duck"]:
        print(DUCK)
        return
    parser = argparse.ArgumentParser(prog="miner", description=__doc__)
    parser.add_argument("--data", type=pathlib.Path, default=os.environ.get("REVIEWER_DATA"))
    parser.add_argument("--config", default="repos.toml")
    commands = parser.add_subparsers(dest="command", required=True)
    mine = commands.add_parser("mine", help="download merged PRs and review threads")
    mine.add_argument("--max-prs", type=int, default=500, help="total PRs per repo, including earlier runs")
    mine.add_argument("--repo", action="append", help="limit to this repo, repeatable")
    mine.set_defaults(run=cmd_mine)
    commands.add_parser("build", help="build the case files").set_defaults(run=cmd_build)
    audit = commands.add_parser("audit", help="print sampled bug cases for hand checking")
    audit.add_argument("--split", choices=SPLITS, default="dev")
    audit.add_argument("-n", type=int, default=50)
    audit.add_argument("--seed", type=int, default=1)
    audit.set_defaults(run=cmd_audit)
    args = parser.parse_args(argv)
    if args.data is None:
        parser.error("set --data or REVIEWER_DATA")
    args.data = pathlib.Path(args.data)
    args.data.mkdir(parents=True, exist_ok=True)
    args.run(args)


if __name__ == "__main__":
    main()
