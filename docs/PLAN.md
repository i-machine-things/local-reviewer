# Plan

Goal: a private code reviewer that runs on the RX 6950 XT, catches real bugs, and stays quiet about style.
Every stage has to beat the previous one on a fixed test set, or it does not ship.

## Where things run

| Machine | Job |
|---|---|
| Laptop | Source code only. It has about 1.5 GB free, so no data lives here. |
| TrueNAS | Storage only: the corpus database, test sets, rules, run results, checkpoints. |
| Workstation (RX 6950 XT) | All heavy work: inference, scoring, embeddings, training. |
| Google AI Studio | Teacher model, public-repo data only. |

## Rules that apply to every stage

- **The test split is sealed.** Teacher passes, rule mining and prompt tuning use `train` and `dev` only.
  `test` is scored once per milestone, so it keeps meaning something.
- **Whole repos are held out for `test`**, so a rule learned from a project cannot leak into that project's score.
- **Private code never goes to the free tier.** Google may use free-tier content to improve its products, and
  human reviewers may read it. Private repos stay local, or use the paid tier.
- **No training on Gemini output.** The Gemini API terms bar using the service to develop competing models. Teacher
  output only ever becomes review rules. Training data comes from the human review corpus.
- **Diffs and review comments are untrusted input.** They are public text and can contain instructions aimed at
  a model. Prompts must mark them as data, and no stage may run anything they say.

## Milestones

### 0. Shared storage

Create a TrueNAS dataset for this project and share it to the workstation, so the workstation reads and writes the
same files the server keeps. Mining is light network work and can write to it from the server sandbox.

Keep the SQLite database on a disk local to whichever machine is writing it, or give it one writer at a time.
SQLite locking is unreliable over network shares. Case files are plain JSONL and are safe to share.

### 1. Corpus and test set (this branch)

Mine merged public PRs for human review comments that led to a code change. Each bug case pairs the hunk as the
reviewer saw it with the comment and the fix. Each gets a presumed-clean control hunk from the same PR.

Keyword matching alone is not enough. On the first 6,500 PRs it confirmed 76 bug cases, and only 2 or 3 of the first
8 hand-checked were real defects. It also misses defects described in plain words. So the keyword filter gets replaced
by a classifier stage running on the workstation: a local model reads every human comment on reviewable code, together
with the fix, and decides whether it describes a defect. The classifier sees `train` and `dev` examples while it is
being tuned, and is applied to `test` unchanged.

Done when: a test set of at least 200 bug cases and 200 controls exists, and a hand audit of 50 sampled bug cases
finds at least 80% are real defects. If the audit misses, tighten the classifier and rebuild. Audits read `train` and
`dev` while tuning. The `test` audit happens once, at the end.

### 2. Scoring harness and baseline

Send only the hunk and file path to the model, never the answer. An LLM judge on `dev` plus a line-overlap check
decides whether a finding matches the reviewer's comment. Report recall on bugs, false-alarm rate on controls, and
the same broken down by category and language.

Compare a bare prompt on `qwen2.5-coder:14b` and `qwen3-coder:30b`. Pick on accuracy; speed only breaks real ties.

Runs on the workstation against the shared dataset. Claude's tools cannot reach the workstation directly, so runs are
started by hand on that machine, and results are read back from storage.

### 3. Triage and context

Filter lock files, binaries and doc-only hunks before the model. Add Tree-sitter context: the enclosing function,
and the callers and definitions of what changed. Ship each piece only if it improves the `dev` score.

### 4. Teacher pass

Run `gemini-3.8-flash` over `train` cases that already have a human-confirmed bug, and ask it for one general rule
per case. Pace to the limits shown in AI Studio, resume after failures, and append rather than overwrite.

Rules land in a `candidate` state and need review before they are used.

### 5. Rule retrieval

Store approved rules in SQLite with embeddings from `nomic-embed-text`. Embed a plain-language summary of the hunk
rather than the raw diff, apply a minimum similarity score, and allow zero rules to be injected. Keep only rules
that measurably help on `dev`.

### 6. Review pipeline

Review hunk by hunk with a bounded context budget filled by rules and code context. Past verdicts from other files
are left out unless a test shows they help. Output line-anchored findings with a confidence value.

Runs through the existing `localcode` backend, as a pre-push command first and then in CI.

### 7. Fine-tuning on the workstation

Train an adapter on the human review corpus (`train` only, tuned on `dev`), with checkpoints written to the server.
Start with a small trial, because training on ROCm with 16 GB of VRAM has not been tested here. Ship the adapter only
if it beats the rules-and-retrieval pipeline from milestone 6 on `test`.

### 8. Feedback loop

Record which findings get fixed or dismissed on real PRs and use that to promote or retire rules. Refresh the corpus
periodically, which needs a new mining mode because the saved cursor only walks back through older PRs. Score `test` at each release, and add a new held-out repo when the old ones have been seen too often.

## Known limits of the test set

- "Line changed after the comment" does not prove the comment caused the change. The audit measures this.
- Controls are presumed clean. Reviewers miss bugs, so the false-alarm rate is an upper bound.
- Controls come from hunks with no nearby review thread, which tilts them toward simpler code. Thread line numbers
  can also drift between commits, so a few controls may sit closer to a comment than intended.
- On rebased PRs the fix check compares two versions of the file, so a change pulled in from the base branch near
  the commented line can be mistaken for a fix.
