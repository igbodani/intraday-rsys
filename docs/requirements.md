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
| CFG-010 | Only implemented schema versions are accepted (an in-range integer is not proof of a supported schema) | `test_unimplemented_schema_version_rejected`, `test_unimplemented_schema_version_rejected_for_research` | H1 |
| CFG-011 | Booleans never satisfy a numeric field (count/budget/seed/delay/money); legal zero costs/delays and seed zero are preserved | `test_boolean_never_satisfies_a_numeric_field`, `test_boolean_never_satisfies_fixture_numeric_field`, `test_boolean_does_not_satisfy_promotion_gate_numeric_leaf`, `test_zero_is_still_a_legal_seed_and_zero_cost` | H1 |
| CFG-012 | `entry_cutoff_local` requires an offset-free exchange-local `HH:MM` | `test_offset_bearing_or_malformed_entry_cutoff_rejected`, `test_well_formed_entry_cutoff_accepted` | H1 |
| CFG-013 | Known experiment date-pair relationships are enforced even while an unrelated endpoint is still missing | `test_partial_reversed_development_interval_rejected_even_with_other_dates_missing`, `test_partial_adjacent_pair_overlap_rejected_even_with_other_dates_missing` | H1 |
| CFG-014 | Blank required identifiers and blank list elements are rejected, not silently accepted as resolved | `test_blank_required_identifier_is_blocked_not_silently_resolved`, `test_blank_list_element_in_cost_stress_scenarios_is_blocked` | H1 |
| CFG-015 | A gate "method" field must cite a declared, versioned specification; a placeholder string like `defined` is insufficient | `test_placeholder_method_string_is_insufficient_for_a_gate_method_field`, `test_versioned_method_reference_is_accepted_for_a_gate_method_field` | H1 |
| CFG-016 | Decoding failures (malformed YAML, invalid UTF-8) are controlled `ConfigError`s; custom validator and registry blocker messages never echo the submitted/resolved value | `test_invalid_utf8_file_is_a_controlled_failure`, `test_custom_validator_error_does_not_leak_the_submitted_value`, `test_registry_blocker_reason_does_not_leak_the_resolved_value` | H1 |
| DATA-001 | Validate intervals, duplicate identity, OHLCV and source metadata | `test_data_contract` | M1-01 |
| DATA-002 | Record historical coverage and universe exclusions | `test_universe_manifest` | D1 (was M1-01; not yet implemented) |
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

## Replay and trade-driven bar requirements (targets; not yet implemented)

Registered per `docs/IMPLEMENTATION_TODO_AND_REPLAY_PLAN.md` section 8 (P0 ticket-scope reconciliation). These are future-ticket targets, not claims of current behavior; none of them is satisfied by the P0/H1 work in this pass.

| ID | Requirement | Acceptance test / fixture | Ticket |
|---|---|---|---|
| BAR-001 | The prefix after T1 contains no T2/T3/T4 information | `test_bar_builder.py` | R2 |
| BAR-002 | Boundary trades belong to the next half-open interval; nanosecond precision is preserved | `test_bar_builder.py` | R2 |
| BAR-003 | Field eligibility and combined-condition precedence match the pinned adapter; unknown conditions are explicit | `test_bar_rules.py` | R2 |
| BAR-004 | Volume-only contributors do not contaminate OHLC or the separate VWAP accumulator | `test_bar_rules.py` | R2 |
| BAR-005 | Identical trade delivery is idempotent; conflicting identity is rejected or handled as an explicit correction | `test_bar_builder.py` | R2 |
| BAR-006 | Late arrival creates a current-time revision of the correct past bucket | `test_bar_revisions.py` | R2 |
| BAR-007 | Corrections/cancels recompute affected extrema/open/close/statistics; unresolved original references block evidence | `test_bar_revisions.py` | R2 |
| BAR-008 | No-trade, missing-data, and no-eligible-price states stay distinct; no fabricated flat bars | `test_bar_builder.py` | R2 |
| BAR-009 | Timer closes a quiet bucket without waiting for another trade; incomplete EOF is visible | `test_bar_builder.py` | R2 |
| BAR-010 | Old snapshots cannot mutate after a new arrival, correction, or split | `test_bar_revisions.py` | R2 |
| BAR-011 | Source and feed changes cannot double-count or mix bar histories | `test_provider_bar_parity.py` | R3 |
| BAR-012 | Unsupported split/unit overlap blocks the affected interval | `test_bar_revisions.py` | R2 |
| REPLAY-001 | Randomly permuted files/pages yield the same output when event identities/order metadata are unchanged | `test_event_order.py` | R1 |
| REPLAY-002 | Perturbing unavailable future trades, quotes, revisions, or actions cannot change earlier outputs | `test_causality.py` | R1 |
| REPLAY-003 | Close-only strategies do not receive forming values; opted-in intrabar strategies receive only current snapshots | `test_replay_inputs.py` | R3 |
| REPLAY-004 | Equal-time batches and derived events follow the frozen phase rules, including boundary plus late trade plus action cases | `test_event_order.py` | R1 |
| REPLAY-005 | Historical revised data without arrival history carries the approximation label | `test_replay_inputs.py` | R3 |
| QUOTE-001 | Quote-only input never changes a stock trade bar or traded volume | `test_quote_state.py` | R3 |
| QUOTE-002 | Invalid/stale quotes cannot silently enter quote-dependent signals or execution estimates | `test_quote_state.py` | R3 |

## Future-perturbation harness

1. Build a multi-symbol synthetic stream with scanner changes, orders, open positions, feature warmup and session timers.
2. Choose a cutoff event key K, run the original stream, and collect all feature/ranking/proposal/order/ledger records with event keys <= K.
3. Create a second stream with the identical prefix through K. Change prices, volume, later availability, later universe records and later optional model outputs strictly after K. Preserve input validity.
4. Run again with the same configuration and seed. Assert byte-identical canonical records through K after excluding nondeterministic wall-clock/run-ID fields.
5. Repeat around range close, a same-time cross-symbol boundary and a stop-update boundary. Test late-arriving observations by availability, not merely the market time printed on a bar.
6. Separately permute input file order. After canonical ordering, the entire output must match. Add a deliberately leaky test double and verify that the harness catches it; do not ship the double as a strategy.
