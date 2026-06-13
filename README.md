# TauCetiData

The durable record of AI code reviews in the Tau Ceti project: every review
execution on [FormalFrontier/TauCeti](https://github.com/FormalFrontier/TauCeti)
PRs, archived with full provenance (exact rubric version, model, exact diff,
runtime, tokens, cost), plus the evaluation data built on top of it (pairwise
judgments by AI judges and human meta-reviewers).

Reviews are produced by
[FormalFrontier/TauCetiReview](https://github.com/FormalFrontier/TauCetiReview);
this repo is the analytics archive. The operational state (scheduling,
staleness, budgets) stays in TauCetiReview's `reviews` branch ledger — nothing
in the worker reads from here.

## Design

- **Write-time archival is the primary record.** The runner writes records to
  a local outbox as it reviews; a separate sync step pushes them here. The
  scoreboard comment on a PR is edited in place across rounds, so GitHub is a
  bad primary record — scraping it (see `scripts/scrape_github.py`) is only a
  backfill/repair path.
- **One file per record**, named by a unique id. Two independent writers exist
  (the local worker and CI), and shadow A/B arms add more; unique filenames
  make pushes conflict-free by construction and re-ingestion idempotent.
- **SQLite is derived, never committed.** `scripts/build_db.py` rebuilds
  `db/tauceti.db` from the record files in seconds.
- **Public, redacted.** Records are built from an explicit field allowlist;
  provider session ids and raw stderr never leave the producing machine, and
  transcripts are scrubbed before upload.

## Layout

```
schema/        JSON Schemas, one per record kind, versioned (run.v1, round.v1, …)
records/
  runs/<pr>/<run_id>.json      one per review execution (pr × head × rubric × model)
  rounds/<pr>/<round_id>.json  one per review round (a scheduling envelope, may be partial)
  posts/<pr>/<round_id>.json   comment ids confirmed posted, for linking to live threads
blobs/<aa>/<sha256>.gz         content-addressed reviewed diffs and reviewer transcripts
eval/          pairwise A/B evaluation data (pairs, judgments, resolutions, decisions)
scripts/       ingest/scrape/build tooling + A/B pairing (make_pairs, ab_fill)
state/         scrape cursors (per writer)
docs/          design docs for the evaluation pipeline
db/            derived SQLite (gitignored)
```

## The analysis unit

A **run record** (`tauceti.run/v1`) is one review execution. A/B queries group
runs by `(pr, head_sha, rubric)` and compare across `(model, rubrics_sha, arm)`
— restricted to matching `prompt_policy`, because a production re-review
carries prior-case-file context that a fresh shadow run does not see.

Rounds are scheduling envelopes (they halt on a block verdict, skip on budget,
or run a single contested rubric); don't treat them as evaluation units.

## Producing records

The TauCetiReview runner writes records into `<store>/outbox/` during a review
(`runner/archive.py`), and `runner/archive.py sync --data-dir <checkout>`
drains the outbox into this repo: write-if-absent, commit, rebase, push, then
clear. Safe to re-run; a push outage never fails a review.

## A/B pairs

```
python3 scripts/make_pairs.py                       # register pairs from coexisting runs
python3 scripts/ab_fill.py --target deepseek        # estimate cost of filling deepseek's arm
python3 scripts/ab_fill.py --target deepseek --commands --limit 25 > fill.sh   # then run a sample
```

`make_pairs.py` registers an `eval/pairs/<id>.json` for every two runs of the same
(pr, head_sha, rubric) that differ in model, rubric version, or arm — the naturally-occurring
model-vs-model collisions already in the history, plus any shadow arm added later.
`ab_fill.py` finds tasks lacking a given provider's arm and either estimates the cost of
filling them (calibrated against that provider's own historical token usage) or emits the
`tauceti-review --shadow` commands that produce the arms. The shadow runs archive back here and
pair up the next time `make_pairs.py` runs.

## Rebuilding the database

```
python3 scripts/build_db.py            # writes db/tauceti.db
sqlite3 db/tauceti.db "SELECT * FROM ab_pairs LIMIT 5"
```

## Cost analysis

`tauceti-review-costs` (in
[TauCetiReview](https://github.com/FormalFrontier/TauCetiReview/blob/main/runner/COSTS.md))
attributes review spend — tokens **and** imputed dollars — to PRs and to merged
lines of code, reading the `records/runs/` files here. Because the token counts
are the immutable fact, it recomputes cost from them at the rate in effect on
each run's date (`runner/prices-history.json`), so the numbers are faithful to
when a run happened and reproducible by anyone from this public archive:

```
git clone --depth 1 https://github.com/FormalFrontier/TauCetiData /tmp/TauCetiData
uvx --from git+https://github.com/FormalFrontier/TauCetiReview tauceti-review-costs \
  --source data --data-dir /tmp/TauCetiData all
```

It defaults to the production arm; `--include-shadows` includes the A/B arms.
