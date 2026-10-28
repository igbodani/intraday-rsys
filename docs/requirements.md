# Requirement-to-test traceability

IDs are stable. Test paths/names are implementation targets; they have not been run against a system.

| ID | Requirement | Acceptance test / fixture | Ticket |
|---|---|---|---|
| CFG-001 | Required unresolved settings block market execution | `test_baseline_pending_config_blocks_every_unresolved_field`; draft YAML exits 2 | M0-01 |
| CFG-002 | Fixture profile cannot be used for market evidence | `test_fixture_config_has_no_blockers` (research blockers never apply to `FixtureConfig`) | M0-01 |
| CFG-003 | Malformed input (YAML, mode type, unknown mode) fails in a controlled way, no traceback or leaked secret | `test_malformed_yaml_is_controlled_failure`, `test_malformed_yaml_does_not_leak_fake_secret`, `test_unsupported_or_malformed_mode_is_controlled_failure`, `test_unknown_field_error_does_not_leak_fake_secret` | M0-01 |
| CFG-004 | Fixture profile invariants (no network, synthetic-only evidence, positive money/ticks, nonnegative costs/delays) are enforced | `test_fixture_invariant_violations_rejected` | M0-01 |
| CFG-005 | Resolved "open" research fields (account/risk/execution/scanner/experiment) are still checked against their declared numeric domain | `test_zero_trial_budget_is_blocked`, `test_out_of_range_risk_fraction_is_blocked`, `test_zero_max_positions_is_blocked`, `test_negative_quantity_step_is_blocked`, `test_zero_seed_is_not_blocked` | M0-01 |
| CFG-006 | Decided/supported-v1 enum fields (provider, side, acceptance, sessions, scanner/strategy ID, ambiguous-exit, data-adjustment policy) reject unsupported values | `test_research_structural_violations_rejected` | M0-01 |
| CFG-007 | Static cross-field dependencies (lookback cap vs. lookback sessions; chronological non-overlapping experiment dates) are enforced | `test_lookback_cap_below_lookback_sessions_rejected`, `test_lookback_cap_equal_to_lookback_sessions_accepted`, `test_malformed_experiment_date_rejected`, `test_overlapping_experiment_intervals_rejected`, `test_chronological_nonoverlapping_experiment_intervals_accepted`, `test_partial_experiment_dates_leave_named_blockers_not_format_errors` | M0-01 |
| CFG-008 | `promotion_gates` is structurally meaningful: `null`, `{}` and partially filled groups block every missing required leaf under D17 | `test_promotion_gates_empty_object_blocks_same_as_null`, `test_promotion_gates_partial_group_only_blocks_missing_leaves`, `test_promotion_gates_unknown_field_rejected` | M0-01 |
| CFG-009 | A fully populated, explicitly synthetic research profile has zero blockers (validation is not blanket rejection) | `test_fully_populated_synthetic_research_profile_has_no_blockers` | M0-01 |
| DATA-001 | Validate intervals, duplicate identity, OHLCV and source metadata | `test_data_contract` | M1-01 |
| DATA-002 | Record historical coverage and universe exclusions | `test_universe_manifest` | M1-01 |
| DATA-003 | Raw execution and as-of split units are consistent | F17; DQ07; `test_future_split_invariance` | M1-01, M2-01 |
| DATA-004 | RVOL respects calendar coverage and feed consistency | H01–H03, DQ08 | M2-02 |
| DATA-005 | Invalid, conflicting or missing bars cannot silently become signals | DQ01–DQ06 | M1-01 |
| CAL-002 | Exchange opening time follows DST | TZ01–TZ04 | M1-01 |
| ORDER-001 | Acceptance and fill order use declared stable keys | F05 plus reversed input; `test_acceptance_sort` | M2-01 |
| STORE-001 | Compact ranking evidence can reconstruct full ranking hash | `test_ranking_reconstruction`; storage measurement | M2-02 |
| CAUS-001 | Strategy views expose only available events | `test_future_perturbation` | M1-02 |
| CAUS-002 | Same-time cross-symbol decisions do not depend on input file order | `test_event_order`; F05 | M1-02 |
| CAUS-003 | New entry uses an eligible subsequent open | F01, F10 | M2-01 |
| CAUS-004 | Stop changes cannot apply inside the completed bar | F07 | M2-01 |
| EXEC-001 | Ambiguous bar uses stop-first; gap stop uses open | F02, F03, F12 | M2-01 |
| EXEC-002 | Invalid stop distance cannot open a trade | F09, F11 | M2-01 |
| ACCT-001 | Cash/equity/P&L reconcile and fees apply once | F01, F08 | M2-01 |
| ACCT-002 | Reservations/sizing prevent overspend | F04, F05, F13, F14 | M2-01 |
| ACCT-003 | Account profile governs proceeds availability | F15; `test_unsettled_cash_unavailable` | M2-01 |
| CAL-001 | Flatten uses actual session close including early close | F06 | M1-01, M2-01 |
| POS-001 | Held symbols are managed outside scanner | F16; `test_removed_candidate_position` | M2-01 |
| STRAT-001 | ORB follows the explicit state machine | ORB-01 through ORB-07 | M2-02 |
| STRAT-002 | VWAP follows the explicit state machine | VWAP-01 through VWAP-08 | M2-03 |
| EXP-001 | Every attempt has immutable inputs, status and outputs | `test_resolved_config_cannot_change_after_creation`, `test_config_hash_cannot_change_after_creation`, `test_hypothesis_cannot_change_after_creation`, `test_identical_resolved_config_gets_separate_run_ids` | M0-02, M2-04 |
| EXP-002 | Holdout access uses the frozen procedure | `test_holdout_access_blocked` | M0-01, M2-04 |
| EXP-003 | Control comparison checks dates/costs/capital | `test_incompatible_comparison` | M2-04 |
| EXP-004 | Full-account outcomes separate from entry/exit diagnostics | `test_metric_scope` | M2-04 |
| EXP-005 | Status transitions follow the declared lifecycle; a finalized attempt cannot resurrect or rerun in place | `test_finalized_run_cannot_transition_back_to_running`, `test_rerun_after_success_gets_a_fresh_id` | M0-02 |
| EXP-006 | A process that dies mid-run is recoverable as a truthful, non-success status; handled interruptions are distinct from crashes | `test_handled_interruption_preserves_truthful_status`, `test_recover_crashed_runs_marks_stuck_running_as_crashed_not_success`, `test_recover_crashed_runs_does_not_touch_finalized_runs` | M0-02 |
| EXP-007 | A persistence failure during a write leaves the authoritative record in its last durably-committed state, never corrupted or half-written | `test_injected_persistence_failure_leaves_record_in_last_good_state`, `test_transition_log_records_every_status_change` | M0-02 |
| EXP-008 | Secret-like fields are redacted by exact field-name matching, not substring matching; legitimate settings (e.g. `ranking_sort_key`) are preserved; saved error text is scrubbed of the original secret values | `test_redact_secrets_matches_key_patterns`, `test_redact_secrets_does_not_false_positive_on_ranking_sort_key`, `test_fake_secret_is_scrubbed_from_saved_error_text` | M0-02 |
| EXP-009 | Every run captures code/dependency provenance; input-data identity is either concrete or an explicit not-applicable reason | `test_run_record_captures_code_and_dependency_provenance`, `test_data_not_applicable_reason_is_recorded_explicitly`, `test_data_identity_is_recorded_when_provided` | M0-02 |
| EXP-010 | A run ID cannot traverse or escape the storage root | `test_traversal_run_ids_cannot_escape_storage_root`, `test_valid_nested_run_id_stays_inside_root` | M0-02 |
| REPORT-001 | Report identifies synthetic/market evidence and missing metrics | `test_report_labels` | M2-04 |

## Future-perturbation harness

1. Build a multi-symbol synthetic stream with scanner changes, orders, open positions, feature warmup and session timers.
2. Choose a cutoff event key K, run the original stream, and collect all feature/ranking/proposal/order/ledger records with event keys <= K.
3. Create a second stream with the identical prefix through K. Change prices, volume, later availability, later universe records and later optional model outputs strictly after K. Preserve input validity.
4. Run again with the same configuration and seed. Assert byte-identical canonical records through K after excluding nondeterministic wall-clock/run-ID fields.
5. Repeat around range close, a same-time cross-symbol boundary and a stop-update boundary. Test late-arriving observations by availability, not merely the market time printed on a bar.
6. Separately permute input file order. After canonical ordering, the entire output must match. Add a deliberately leaky test double and verify that the harness catches it; do not ship the double as a strategy.
