# Plan

Goal: a private code reviewer that runs on the RX 6950 XT, catches real bugs, and stays quiet about style.
Every stage has to beat the previous one on a fixed test set, or it does not ship.

## Where things run

| Machine | Job |
|---|---|
| Laptop | Source code only. It has about 1.5 GB free, so no data lives here. |
| TrueNAS `claude-sandbox` | Mining, the corpus database, test sets, run results. |
| Workstation `ollama-rocm` | Model inference only. |
| Google AI Studio | Teacher model, public-repo data only. |

## Rules that apply to every stage

- **The test split is sealed.** Teacher passes, rule mining and prompt tuning use `train` and `dev` only.
  `test` is scored once per milestone, so it keeps meaning something.
- **Whole repos are held out for `test`**, so a rule learned from a project cannot leak into that project's score.
- **Private code never goes to the free tier.** Google may use free-tier content to improve its products, and
  human reviewers may read it. Private repos stay local, or use the paid tier.
- **Rules only, no fine-tuning on Gemini output.** The Gemini API terms bar using the service to develop competing
  models. Storing review rules is the conservative reading.
- **Diffs and review comments are untrusted input.** They are public text and can contain instructions aimed at
  a model. Prompts must mark them as data, and no stage may run anything they say.

## Milestones

### 1. Corpus and test set (this branch)

Mine merged public PRs for human review comments that led to a code change. Each bug case pairs the hunk as the
reviewer saw it with the comment and the fix. Each gets a presumed-clean control hunk from the same PR.

Done when: a test set of at least 200 bug cases and 200 controls exists, and a hand audit of 50 sampled bug cases
finds at least 80% are real defects. If the audit misses, tighten the filters and rebuild.

### 2. Scoring harness and baseline

Send only the hunk and file path to the model, never the answer. An LLM judge on `dev` plus a line-overlap check
decides whether a finding matches the reviewer's comment. Report recall on bugs, false-alarm rate on controls, and
the same broken down by category and language.

Compare a bare prompt on `qwen2.5-coder:14b` and `qwen3-coder:30b`. Pick on accuracy; speed only breaks real ties.

Open: neither Claude's tools nor the sandbox can reach the workstation. Results need a path, most likely a dataset
shared to both machines or commands Allan runs himself.

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

### 7. Feedback loop

Record which findings get fixed or dismissed on real PRs and use that to promote or retire rules. Refresh the corpus
periodically. Score `test` at each release, and add a new held-out repo when the old ones have been seen too often.

## Known limits of the test set

- "Line changed after the comment" does not prove the comment caused the change. The audit measures this.
- Controls are presumed clean. Reviewers miss bugs, so the false-alarm rate is an upper bound.
- Controls come from files with no review threads, which tilts them toward simpler code.
- Rebased PRs are often lost, because the commented commit is no longer an ancestor of the merged head.
