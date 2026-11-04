# Shared scanner evidence (BR-04-F01)

Historical and synthetic minute-history runs now retain a version-2
`scanner_history.json`. The run manifest explicitly declares
`scanner_history_version: 2`. Semantic traces, logical snapshot IDs, feature
selection, availability rules, input revisions, fills and P&L are unchanged.

## What changed

Previously, each growing RVOL snapshot copied every selected minute observation.
The new archive stores an observation once and shares ordered sequence prefixes.
Snapshot headers retain the original date, coverage, volume and exclusion reason.
Each snapshot references a sequence head and exact count. Observation and sequence
content hashes protect the references; snapshot IDs remain hashes of the original
fully expanded logical payload. No revisions, decisions or lookback dates are
sampled, truncated or discarded. Original inputs still retain all supplied
revisions, including ones not selected by a decision's cutoff.

`data/history_evidence.py` owns the active interning service and reader. Strategy
code has no dependency on storage/application/research modules. The batch writer
merges shared tables across sessions without expanding all snapshots.

```python
from rsys.data.history_evidence import read_history_archive

history = read_history_archive(attempt / "scanner_history.json")
snapshot = history[snapshot_id]  # ID from the decision's scanner evidence
selected_revisions = snapshot["selected_revisions"]
```

The reader also accepts the old unversioned dictionary (logical version 1).
Directly iterating JSON top-level entries as snapshots is only valid for the old
format; use the reader for both. Unknown/mismatched versions, missing references,
wrong sequence lengths and changed logical snapshots fail. Reads return isolated
copies so inspecting a snapshot cannot mutate retained shared observations.

## Integrity and failure behavior

Report regeneration, comparison and status share streamed-artifact validation.
They verify file hashes, the archive graph and each logical snapshot, then check
that every trace snapshot reference exists. Validation expands one snapshot at a
time. It never retains all expanded snapshots in a second registry. Old valid
artifacts remain readable; they do not silently gain a new format.

Atomic JSON persistence now encodes in bounded chunks rather than creating and
parsing complete serialized copies. Existing JSON key conversion and scalar
encoding are preserved, including custom serializers. The temporary file is
flushed and synced before replacement. Short/late write failure preserves the
previous destination and the primary application failure. Artifact hashing also
streams file bytes. Buffers are bounded by the chunk target plus the largest
individual serialized scalar; arbitrary giant strings are not claimed constant-size.

The complete decision summaries, semantic traces and HTML details remain retained
for compatibility. Large universes and long studies still need measurement;
this change is a measured reduction, not an unlimited-resource guarantee.

## Verification

`tests/test_history_evidence.py` compares each reconstructed payload and ID against
the original encoding, proves prefix sharing grows with distinct observations,
checks numeric spelling/revisions/missing/zero cases, tests old readers, and
rejects corrupted/missing references even when outer file hashes are recomputed.
Public bar/trade execution equals the original retention algorithm in every
decision, fill, metric and trace row. Existing future-prefix negative controls and
hand-calculated account oracles remain active.

Atomic-write tests compare prior serialized bytes exactly, intercept bounded
writes and inject a later disk failure to prove the previous file survives.
Measured before/after resources and completed test evidence are in
[BR04_F01_VERIFICATION.md](agent-workflow/BR04_F01_VERIFICATION.md).
