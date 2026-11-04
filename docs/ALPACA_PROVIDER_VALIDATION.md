# Validate one explicitly approved Alpaca sample

`rsys validate-alpaca` checks the acquisition-to-backtest workflow for a small,
declared development sample. It reuses the existing downloader, immutable cache,
offline preflight and backtest runner. It does not choose settings, score a strategy,
place orders or open final holdout dates.

The software has offline tests. **A real Alpaca sample has not been run here.**
Credentials alone never enable this new command or its integration test.

## 1. Prepare local inputs

Copy [the backtest template](../configs/alpaca_backtest.example.json) and
[the validation request](../configs/alpaca_validation.example.json) outside Git.
The examples deliberately cannot execute unchanged. Their numerical values are
illustrations, not approved research settings.

In the backtest template, explicitly choose:

- One symbol, an accessible `sip` or `iex` feed, and an `asof` symbol-mapping policy.
  There is no fallback to another feed.
- Supported 2024 execution dates, development partition permissions and the scanner's
  full calendar search budget. Every warmup date must also be permitted. If the
  budget reaches outside calendar coverage, the request fails before acquisition.
- Account, risk, costs, delay, entry/exit and scanner settings. Use one fixed strategy
  for this software validation; do not search for profitable parameters.
- An actual corporate-action review covering the entire sample. The current path
  requires `verified_none`; split conversion is not implemented. This is your
  attestation, not a verification performed by the program.
- `purpose: "development"`, `objects: {}` and the explicit modeled-final-bar timing
  acknowledgement. The command saves a separate config containing acquired pins.

In the validation request, set `backtest_config` to that file, `adjustment` to `raw`,
`sample_dates` to the sorted union of execution dates and **every date in the full
warmup search budget**, and `max_sessions` to your explicit acquisition limit.
Dates must match exactly; the command never adds dates to fill gaps. Nonconsecutive
dates are requested individually. Relative paths resolve from their owning config
file; `--out` resolves from the command's working directory.

## 2. Enable and run deliberately

Set `APCA_API_KEY_ID` and `APCA_API_SECRET_KEY` privately in your local environment.
Do not paste credentials into chat, config files, commands committed to Git or logs.
Set `RSYS_ENABLE_ALPACA_VALIDATION=1` only when you intend to make the approved request.
The CLI also requires `--enable-live`:

```bash
uv run rsys validate-alpaca --config /local/sample.json --out /local/validation --enable-live
```

“Live” here means a real **historical market-data request**, not trading. The request
is validated before client construction, credential reads or cache access. Missing
enablement, invalid scope or unavailable data returns nonzero and retains an attempt.
The existing lower-level `rsys download` interface keeps its existing behavior; this
two-part gate belongs to the validation command.

## 3. Inspect the result

The command prints the parent manifest and generated pinned-backtest config paths.
Use the ID in `provider-validations/<id>` with the existing status command:

```bash
uv run rsys status --storage-root /local/validation --id <id> --json
```

| Evidence | Location / meaning |
|---|---|
| Validation JSON and escaped HTML | `provider-validations/<id>/validation.json`, `report.html` |
| Exact acquired pins and executable input | `provider-validations/<id>/backtest.json` |
| Integrity inventory and diagnostic seal | `provider-validations/<id>/manifest.json` |
| Sanitized operational journal | `invocations/<id>/diagnostics.jsonl` |
| Each acquisition and cache reuse attempt | Linked download manifests in each date's record |
| Coverage and missing-minute details | Linked preflight manifest and `preflight.json` |
| Actual offline simulation evidence | Linked backtest manifest, trace, fills, metrics and report |

The checklist distinguishes `verified`, `failed` and `not_run`. Check coverage,
first/last bar fields, exact feed/dates, raw units, object hashes, discrepancies,
request/retry/page counts and HTTP status counts. An empty page does not end
pagination while a next-page token exists. Missing minutes are never inserted as
zero-volume bars. A permitted backtest with warmup gaps can finish while its coverage
check remains failed: software success is not data or research approval.

401/403 failures stop without fallback. Selected transient errors use at most three
attempts per page, with bounded waits. A failed later date leaves earlier completed
objects intact. Rerunning without `--refresh` reuses them. Report-write failures and
interruptions remain non-success; inspect status and retained child manifests.

The harness itself repeats acquisition using a transport that rejects any request,
checks unchanged pins, then runs the existing backtest inside a Python socket/DNS
tripwire. That guard is process-wide and intended for this single-threaded workflow;
it is not an operating-system sandbox for arbitrary native code or subprocesses.

## 4. Complete the manual provider gate

`real_provider_gate` never becomes `verified` automatically. A real successful
request yields `pending_manual_review`; a cache-only run does not prove current feed
access. Synthetic tests are explicitly labeled and leave the real gate `not_run`.

Before accepting the sample, review current provider documentation and entitlement,
inspect actual timestamps/coverage, verify the corporate-action evidence and manually
reconcile at least one trade against saved bars and explicit costs. Record remaining
uncertainty. Final historical bars do not establish what arrived or was revised in
real time. Action audit, arrival history and operator review remain `not_run` in the
automatic checklist. The research result remains `unscored`; final evaluation remains
blocked by the existing research decisions.

Use `--refresh` only for a deliberate new acquisition. It creates new immutable
objects when content changes and preserves old pins; it does not silently update an
earlier backtest's evidence.

## Optional integration test

Default `uv run pytest tests/ -q` excludes `alpaca_integration`, even if credentials
are present. For a deliberate real check, set all three local controls:
`RSYS_ENABLE_ALPACA_VALIDATION=1`, `RSYS_ALPACA_SAMPLE_CONFIG` to the approved request,
and `RSYS_ALPACA_SAMPLE_OUT` to a persistent local output directory. Then explicitly
select the integration marker:

```bash
uv run pytest tests/integration/test_alpaca_sample.py -m alpaca_integration -q
```

Missing controls fail before calling the provider. A passing integration test means
the software workflow completed; manual provider/research approval is still separate.

## Dated source contract

Official references reviewed on **2026-11-03**:

- [Historical stock bars](https://docs.alpaca.markets/us/reference/stockbars): explicit
  feed, raw adjustment, symbol mapping, pagination and response/error fields.
- [Historical stock data](https://docs.alpaca.markets/us/docs/historical-stock-data-1):
  IEX and consolidated SIP are different source scopes.

The report retains this reference date and the URLs. It does not claim to have
retrieved or re-audited documentation on every invocation. Recheck the contract
before accepting real evidence; `asof` is a symbol-mapping setting, not a causal
arrival-time cutoff. D12/D20/D22 and the imported decision-date provenance warning
remain unchanged. See [the implementation verification](agent-workflow/BR05_VERIFICATION.md).
