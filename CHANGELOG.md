# Changelog

All notable changes to this project are documented here. Newest first.

## 2026-11-14

### Fixed
- **Secondary rate limits were treated as primary limits.** GitHub's secondary-limit `403` for the
  GraphQL API carries no `X-RateLimit-*` headers (only `Retry-After`, e.g. 300 s, and a
  "You have exceeded a secondary rate limit" message). `handle_rate_limit()` defaulted the missing
  `X-RateLimit-Remaining` header to `0`, so every secondary limit slept ~1 hour (3605 s) instead of
  the few minutes GitHub asks for. On repositories with very heavy workflow runs this could stall a
  worker almost completely. A `403`/`429` is now a primary limit only when GitHub explicitly reports
  `X-RateLimit-Remaining: 0`.
- Secondary limits now wait `Retry-After`, doubling on repeats and capped at 1 hour, for up to
  6 retries (previously 3) before skipping. Other `403`s that still have quota left
  (e.g. permission errors) keep the previous 60 / 120 / 240 s backoff, then skip.
- **Degraded GraphQL responses.** A `200 OK` whose `rateLimit` field is missing means the query was
  degraded; the whole batch is now treated as failed and retried/split, instead of being accepted
  with cost 0. Per-node errors (e.g. a deleted check suite) are logged and the batch is kept, as
  null nodes are already skipped. `nodes: null` no longer crashes the batch.

### Changed
- **Adaptive GraphQL batch size.** Per repository, the batch starts at `GRAPHQL_BATCH_SIZE` (10),
  halves after a failed request (minimum 2) and doubles back after 30 clean batches. On heavy
  repositories 10-run queries hit GitHub's 10-second GraphQL timeout (`502`) far more often than
  smaller ones, and bursts of timeouts are what trigger secondary limits. The query and output are
  unchanged.
- On resume, runs already identified as "massive" are no longer sent through GraphQL again when
  their repository is re-discovered; they are still fetched by the REST cleanup phase.
- `--input` is now optional. Without it, the default dataset is used (`repo_weights.csv`, else
  `data-raw/workflows.csv.zst`), as in the original collection setup.
- The output directory can be set with `OUTPUT_DIR=<folder>` in `.env` (default: `data`).
- Window reports, `progress_shard_*.log` and `pipeline_summary.json` now include the number of
  degraded GraphQL responses (`gql_degraded`).

### Added
- `test_rate_limit.py`: tests for the rate-limit classification and backoff, and for batch splitting.
