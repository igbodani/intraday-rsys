# BR-01 verification and completion handoff

Reviewed 2026-11-04 on `docs/copilot-backtest-readiness`, starting from upstream
`43fa56c745e449352fbe310b2de9cd0c7ce1929f`. The fetched source tree was checked
against GitHub tree `cce263804b96389aa28eeeb85a7b647923a5af12` before edits.
This handoff covers BR-01 only. BR-02 and later tickets remain open.

## Problem and behavior

Copilot had implemented early invocation files and an append-only operational
journal. The upstream suite passed **978 tests**, but extra failure injection
found uncovered lifecycle defects. Fourteen new regression cases failed on
that source before the repairs.

| Defect | Result after repair | Regression evidence |
|---|---|---|
| A download-directory collision overwrote an older manifest | Only the invocation that created an attempt may write its manifest; earlier bytes remain unchanged | `test_download_collision_preserves_existing_evidence` |
| Download status could remain successful after diagnostics failed | Publish a non-success manifest first; seal the journal; atomically commit the final outcome last | `test_download_never_commits_success_after_journal_failure`, `test_failed_final_download_commit_never_publishes_success` |
| Request identity, run-link and storage-construction errors escaped journaling | A shared invocation boundary records the error once and supplies inspection paths; resolved run-link/directory failures enter the SQLite failure lifecycle | `test_early_public_failure_is_recorded_once` |
| A short write was accepted and later appends could turn a torn tail into corrupt middle content | Check the returned byte count; after any append/flush/sync/close failure stop further appends | `test_short_write_stops_writer_without_corrupting_the_next_record`, `test_writer_io_failure_preserves_prefix_and_stops_future_appends` |
| Arbitrary details and provider exception text could expose secrets | Allowlist detail keys, scrub configured secret values recursively, withhold free text when secret knowledge is unavailable; sanitize CLI/manifest errors too | Secret and provider tests in `test_diagnostic_lifecycle.py`, existing nested-redaction tests in `test_diagnostic_journal.py` |
| Legacy readiness preceded the requested HTML report | Save required reports before readiness; report failure leaves a failed authoritative record | `test_legacy_report_exists_before_readiness`, `test_required_report_failure_is_not_comparable_success` |
| Journal sealing and a production prefix reader were missing | Seal actual bytes, forbid later appends, verify new evidence during comparison, and distinguish an incomplete tail from corruption without editing the file | `test_diagnostic_integrity.py` |
| Cleanup or output delivery could obscure the actual result | Preserve the primary error when evidence saving also fails; keep console delivery after the committed result | `test_backtest_preserves_preparation_error_when_diagnostics_also_fail`, `test_broken_console_does_not_rewrite_successful_download` |

The sealing integration also has an explicit regression for a legacy validation
failure: its failure record is written **before** finalization seals the journal.

## Ownership and completion contract

- `research/models.py` owns the invocation, event and reader-result models.
  `research/errors.py` owns diagnostic write/integrity errors.
- `research/diagnostics.py` owns append/flush/sync, atomic small-state replacement,
  redaction, the shared invocation boundary, read-only prefix validation and seals.
  It never supplies strategy time or enters the trading trace.
- `application/run.py`, `batch.py` and `historical.py` wire that boundary around
  preparation, execution and final saving. CLI errors print the invocation/journal
  paths even when no run has been resolved.
- **Simulation outcome authority:** the existing SQLite terminal run record.
  Write the required artifacts/report, append readiness, seal diagnostics, then
  commit the terminal record. A failed run cannot become comparable just because
  its manifest was already written.
- **Download outcome authority:** its atomically replaced final manifest.
  `finalizing` is a provisional non-success state. Final success is published only
  after all datasets and the diagnostic seal are available.
- Readiness is not a success event. If the final authoritative commit fails after
  sealing, the journal stays unchanged; the invocation summary records failure
  when writable, and the outcome remains failed or incomplete. Readers must not
  derive success from the last diagnostic event alone.
- Newly written schema-2/3 run manifests declare `diagnostics_version: 1`. Their
  SQLite record pins the diagnostic seal, and comparison requires matching bytes,
  identities and record counts. Older manifests without that declaration retain
  their original checks. No existing trace/artifact hash check is weakened.
- Completed journal records are flushed; lifecycle/failure checkpoints are synced.
  The tested process-kill guarantee covers the acknowledged prefix. This does not
  promise preservation of an unacknowledged write or survival of every power,
  filesystem or hardware failure.

The atomic JSON helper is also used for retained JSON artifacts so a failed
replacement cannot expose a partially overwritten summary. Trading/account,
scanner, strategy, provider mapping and risk/cost rules are unchanged.

## Acceptance coverage and reproduction

| Acceptance | Evidence |
|---|---|
| T01: early visibility and correlation | Existing distinct-ID/link tests plus a real public `backtest` child paused at cache loading. The parent reads the request hash and journal before release. A second child is force-killed at the same acknowledged barrier; its prefix remains, with no falsely completed run. |
| T02: secret handling | Nested fake credentials, punctuation/newlines, arbitrary messages and fake-provider exceptions; inspect decoded records/manifests and captured console output. Unknown free text is withheld. |
| T03: append/encoding/reader | Encoding and flush/sync assertions; injected short writes and write/flush/fsync/close failures; torn-tail prefix read; rejection of malformed complete lines, sequence gaps, changed IDs and unsupported versions; original bytes preserved. |
| T04: error on error | Creation/metadata/storage errors, directory collision, manifest/report write failures, and primary preparation errors followed by evidence failure. Assert nonzero public exits, failed SQLite/download outcomes and retained primary exception. |
| BR-01 sealing dependency | Actual-byte hash, refusal to append after sealing, comparison rejection of missing/tampered new diagnostics, and original schema-2/3 reader compatibility. This does not complete BR-02's trace-streaming requirements. |

Run the shipped tests with the frozen environment:

```bash
uv sync --frozen --group dev
uv run pytest tests/test_diagnostic_journal.py tests/test_diagnostic_lifecycle.py tests/test_diagnostic_integrity.py -q
uv run pytest tests/ -q
uv run ruff check .
uv run python scripts/check_copilot_config.py
git diff --check
```

Final checks on the repaired source:

| Check | Actual result |
|---|---|
| Upstream baseline full suite | 978 passed, zero failures |
| Three diagnostic test files | 85 passed in 14.03 seconds |
| Final full suite | **1022 passed in 134.18 seconds; zero failures or skips** |
| Ruff | All checks passed |
| Copilot metadata/local-link checker | Valid |
| Changed Python files formatting and `git diff --check` | Clean |

The final full suite inherited fake `APCA_API_KEY_ID` and
`APCA_API_SECRET_KEY` values and a temporary `sitecustomize.py` tripwire that
raises on socket connection/address-resolution calls, including in subprocesses.
The tripwire is verification scaffolding outside the repository, not a new
provider integration harness. All provider responses in these checks are fake or
cached fixtures. No genuine credentials or network provider call was used.

## Compatibility and public smoke checks

Before/after runs of the actual CLI retained **identical trading-trace bytes**
and these independently tested fixture outcomes:

| Public fixture | Initial equity | Ending equity |
|---|---:|---:|
| Legacy standalone bars | 10000 | 9880 |
| Multi-session bars | 1000 | 1022.34800 |
| Multi-session trades | 1000 | 1034.14420 |

Canonical before/after trace SHA-256 values were unchanged:

```text
legacy d67086832d008a2a69e9a9dafcb50ae0322e699c6526e9f3144adfba85b31cbc
bar    c029c04459d1bf9fbd6219525f482b3b14577d327bf0dfc3a2e121ec09245529
trade  767ca26db5c156389e98c264312eb2c1b6282cde29ebb3d7039b89a5bb117fb8
```

All three generated the requested HTML report. `rsys fixture --case all` and
the configured data-only replay completed successfully. `rsys final-evaluate`
still returned the required blocked exit code **5**.

Some old journal test assertions were corrected, with their substantive checks
preserved: count syncs on the journal inode (atomic state writes also sync);
inject creation failure at the shared owner; reject writing into a colliding
download directory; and check provisional-manifest → readiness → final-manifest
ordering. Unknown-context text tests now expect withholding rather than exposure.

## Independent review

A separate read-only reviewer inspected the final implementation diff against the
verified upstream snapshot and returned **no blocking findings in the reviewed
BR-01 scope**. It independently ran the two new test files: **44 passed in 12.67
seconds**. Its own reproductions confirmed one failure event for early errors,
preservation of a colliding download's original manifest, and suppression of fake
provider secrets in both CLI output and the download manifest.

Review covered error boundaries, redaction, stopping after writer failure,
primary/secondary error preservation, report/readiness order, download outcome
authority, seal validation and comparison integration. The reviewer did not
repeat the engineer's full suite or claim real-provider, Windows, power-loss or
BR-02 trace-streaming validation. Passing tests and this scoped review are not a
claim that every possible defect has been eliminated.

## Remaining work and owner inputs

Next is **BR-02: stream the existing trading trace as execution happens**. Today
operational preparation evidence survives a process kill, but the full semantic
trading trace is still primarily saved at finalization. Do not claim interrupted
simulations retain every already-executed fill until BR-02 passes its tests.

BR-03 still owns the operator-facing progress/status/recovery commands. BR-04
owns offline data preflight/resource inventory; BR-05 owns the explicitly enabled
real-provider validation gate. BR-06 owns finite experiment plans and trial
accounting. Existing D09–D17/D20/D22 research/provider decisions are unchanged;
final evaluation remains blocked and historical results remain estimates.

No credentials or research choices are needed to finish these BR-01 repairs or
start BR-02's offline implementation. Real Alpaca verification was **not run**:
before that gate, the owner must provide credentials locally and specify an
entitled feed, symbols, permitted 2024 dates, required warmup and action-reviewed
request. No live/paper orders, final-data access or branch merge is included.

The current execution platform is Linux/Python 3.12. Windows was not available
for this verification. Its previously reported symlink-permission and long-temp-
path problems are platform limits to recheck locally, not silently marked fixed.
