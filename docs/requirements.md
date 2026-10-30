# Requirement-to-test traceability

IDs are stable. Current verification is recorded in `R3_REPAIR_RESULTS.md`: 426 tests pass, with the new R3SAFE rows exercising the public runner/view/CLI repairs. Older R3INT rows retain their historical mapping but do not by themselves prove delayed-event safety. M2 and later rows remain implementation targets; no ledger, strategy, execution or holdout behavior is claimed.

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
| DATA-002 | Record historical coverage and universe exclusions; a verified no-trade interval is distinct from missing delivery | `test_manifest_accepts_exclusions_covering_the_gap`, `test_verified_no_trade_is_distinct_from_missing_delivery`, `test_manifest_rejects_a_requested_instrument_neither_observed_nor_excluded` | D1 |
| DATA-003 | Raw execution and as-of split units are consistent | F17; DQ07; `test_future_split_invariance` | M1-01, M2-01 |
| DATA-004 | RVOL respects calendar coverage and feed consistency | H01–H03, DQ08 | M2-02 |
| DATA-006 | Every market event shares a common, frozen, nanosecond-precise envelope with availability, revision, source sequence and a recorded-vs-modeled timing label | `tests/test_market_events.py` (envelope tests) | D1 |
| DATA-007 | A `NormalizedBar`'s identity canonicalizes equivalent UTC timestamp strings; interval length/alignment and availability-before-end are enforced | `test_normalized_bar_identity_is_canonical_across_equivalent_timestamp_strings`, `test_normalized_bar_rejects_duration_mismatching_declared_interval`, `test_normalized_bar_rejects_availability_before_bar_end` | D1 |
| DATA-008 | Corporate-action timestamps must be explicit tz-aware UTC; a bar straddling an action's dispatch moment is invalidated, not settled against mixed units | `test_action_rejects_naive_timestamps`, `test_action_rejects_non_utc_offset_timestamps`, `test_bar_straddling_a_split_dispatch_is_invalidated` | D1 |
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
| EXP-006 | A process that dies mid-run is recoverable as a truthful, non-success status; handled interruptions (including `KeyboardInterrupt`) are distinct from crashes; recovery never marks a live-owned run crashed | `test_handled_interruption_preserves_truthful_status`, `test_keyboard_interrupt_inside_run_context_is_recorded_as_interrupted_not_failed`, `test_recover_crashed_runs_marks_a_run_with_no_live_owner_as_crashed`, `test_recover_crashed_runs_marks_a_real_dead_child_process_as_crashed`, `test_recover_crashed_runs_leaves_a_live_owned_run_running`, `test_recover_crashed_runs_does_not_touch_finalized_runs` | M0-02, H2 |
| EXP-007 | A persistence failure during a write leaves the authoritative record in its last durably-committed state (whole transaction rolls back, not a partial write); reopening storage yields one truthful committed state and consistent history | `test_injected_persistence_failure_leaves_record_in_last_good_state`, `test_transition_log_records_every_status_change_with_payload_integrity`, `test_reopening_storage_yields_one_truthful_committed_state` | M0-02, H2 |
| EXP-008 | Secret-like fields are redacted by exact tokenized field-name matching (including camelCase and header/environment-name forms), not substring matching; legitimate settings (e.g. `ranking_sort_key`) are preserved; saved error text is scrubbed of the original secret values; a reloaded record carries no real secret to leak | `test_redact_secrets_matches_key_patterns`, `test_redact_secrets_does_not_false_positive_on_ranking_sort_key`, `test_redact_secrets_catches_camelcase_and_header_style_canaries`, `test_fake_secret_is_scrubbed_from_saved_error_text`, `test_scrubbing_is_safe_after_reload_even_though_in_memory_secrets_are_gone` | M0-02, H2 |
| EXP-009 | Every run captures a content-based code fingerprint (differs for differently-dirty trees) plus git metadata and dependency-lock hash; input-data identity is either a nonblank concrete value or a nonblank explicit not-applicable reason, never both | `test_run_record_captures_code_and_dependency_provenance`, `test_compute_source_fingerprint_differs_for_differently_dirty_trees`, `test_compute_source_fingerprint_excludes_credential_shaped_files`, `test_data_not_applicable_reason_is_recorded_explicitly`, `test_data_identity_is_recorded_when_provided`, `test_start_run_rejects_blank_data_identity_and_blank_reason`, `test_start_run_rejects_contradictory_data_declarations` | M0-02, H2 |
| EXP-010 | A run ID is restricted to a single safe path component (no traversal, no nested segments) | `test_non_single_component_run_ids_are_rejected`, `test_valid_single_component_run_id_is_accepted` | M0-02, H2 |
| EXP-011 | A run's owning process/host is recorded once it starts running and cannot be reassigned afterward | `test_owner_pid_and_host_recorded_when_run_starts`, `test_owner_pid_cannot_be_reassigned_once_set` | H2 |
| EXP-012 | A concurrent recovery sweep cannot run twice at once; `run_context`'s own finalize never overwrites an outcome another finalizer already committed | `test_recovery_lock_rejects_a_concurrent_sweep`, `test_run_context_does_not_overwrite_an_already_finalized_record` | H2 |
| REPORT-001 | Report identifies synthetic/market evidence and missing metrics | `test_report_labels` | M2-04 |

## Replay and trade-driven bar requirements

Registered per `docs/IMPLEMENTATION_TODO_AND_REPLAY_PLAN.md` section 8 (P0 ticket-scope reconciliation). As of the 2026-10-29 corrections-and-R3 pass, `REPLAY-001`..`REPLAY-005`, `BAR-001`..`BAR-012` and `QUOTE-001`/`QUOTE-002` are ALL implemented and passing, including the forming-bar/quote half of `REPLAY-003` (previously the only remaining gap): `ReplayView.forming_bar` is wired to a real `SeriesRouter`/`TradeBarBuilder`, `src/rsys/data/quotes.py` implements quote state/freshness, and `src/rsys/parity.py` implements the offline provider/builder comparator behind `BAR-011`/`REPLAY-005`. None of this is verified-Alpaca behavior: `FixtureBarRuleSet` and the parity comparator's fixtures are explicitly synthetic-only, and a real `providers/alpaca_rules.py` remains blocked on D12's provider audit.

| ID | Requirement | Acceptance test / fixture | Ticket |
|---|---|---|---|
| BAR-001 | The prefix after T1 contains no T2/T3/T4 information | `test_worked_example_full_sequence` | R2 |
| BAR-002 | Boundary trades belong to the next half-open interval; nanosecond precision is preserved | `test_boundary_trade_belongs_to_the_next_bucket`, `test_nanosecond_precision_is_preserved_in_bucket_assignment` | R2 |
| BAR-003 | Field eligibility and combined-condition precedence match the pinned adapter; unknown conditions are explicit | `test_unknown_condition_is_an_explicit_error`, `test_combined_conditions_use_the_most_restrictive_eligibility` | R2 |
| BAR-004 | Volume-only contributors do not contaminate OHLC or the separate VWAP accumulator | `test_volume_only_condition_restricts_price_and_vwap`, `test_worked_example_full_sequence` | R2 |
| BAR-005 | Identical trade delivery is idempotent; conflicting identity is rejected or handled as an explicit correction | `test_identical_redelivery_is_idempotent`, `test_conflicting_redelivery_without_a_correction_is_rejected` | R2 |
| BAR-006 | Late arrival creates a current-time revision of the correct past bucket | `test_worked_example_full_sequence` (T5 late arrival) | R2 |
| BAR-007 | Corrections/cancels recompute affected extrema/open/close/statistics; unresolved original references block evidence | `test_worked_example_full_sequence` (cancels), `test_correction_referencing_an_unknown_trade_id_is_rejected` | R2 |
| BAR-008 | No-trade, missing-data, and no-eligible-price states stay distinct; no fabricated flat bars | `test_volume_only_only_bucket_has_no_eligible_price`, `test_empty_bucket_snapshot_has_no_trades_and_no_price` | R2 |
| BAR-009 | Timer closes a quiet bucket without waiting for another trade; incomplete EOF is visible | `test_timer_closes_a_bucket_with_no_recent_trade` | R2 |
| BAR-010 | Old snapshots cannot mutate after a new arrival, correction, or split | `test_previously_returned_snapshot_is_unaffected_by_a_later_correction`, `test_bar_snapshot_is_frozen` | R2 |
| BAR-011 | Source and feed changes cannot double-count or mix bar histories | `test_provider_bar_parity.py`; `SeriesRouter`'s single-authority-per-series wiring | R3 |
| BAR-012 | Unsupported split/unit overlap blocks the affected interval | `test_bucket_straddling_a_split_dispatch_is_blocked`, `test_bucket_not_overlapping_a_split_dispatch_is_not_blocked` | R2 |
| REPLAY-001 | Randomly permuted files/pages yield the same output when event identities/order metadata are unchanged | `test_replay_order_is_independent_of_input_list_order`; `test_reversed_input_order_yields_the_same_semantic_trace_hash` (CLI-level) | R1, R3 |
| REPLAY-002 | Perturbing unavailable future trades, quotes, revisions, or actions cannot change earlier outputs | `test_future_perturbation_does_not_change_the_honest_decision_through_cutoff`, `test_future_perturbation_harness_catches_a_deliberately_leaky_double` | R1 |
| REPLAY-003 | Close-only strategies do not receive forming values; opted-in intrabar strategies receive only current snapshots at their decision key | `test_closed_only_context_has_no_forming_or_trade_access`, `test_intrabar_context_still_has_closed_bars_and_a_bounded_latest_trade`, `test_forming_bar_query_before_t2_does_not_see_t2`, `test_context_after_freezes_at_the_cutoff_and_exposes_forming_bar` | R1, R3 |
| REPLAY-004 | Equal-time batches and derived events follow the frozen phase rules, including boundary plus late trade plus action cases | `test_same_time_batch_includes_every_symbol_with_an_event_at_that_instant`, `test_insert_derived_rejects_an_already_dispatched_bucket`, `test_insert_derived_accepts_a_future_bucket_and_preserves_order`, `test_insert_derived_rejects_same_phase_same_instant_even_with_a_larger_tiebreak`, `test_insert_derived_accepts_a_later_phase_at_the_same_instant` | R1, FIX-05 |
| REPLAY-005 | Historical revised data without arrival history carries the approximation label | `test_provider_bar_parity.py` (`ParityResult.not_comparable_reason` for a first-publication-vs-revision mismatch) | R3 |
| QUOTE-001 | Quote-only input never changes a stock trade bar or traded volume | `tests/test_quote_state.py`; `SeriesRouter.apply_quote` never touches `TradeBarBuilder` state | R3 |
| QUOTE-002 | Invalid/stale quotes cannot silently enter quote-dependent signals or execution estimates | `tests/test_quote_state.py` (`QuoteFreshnessPolicy`, locked/crossed/missing-side conditions) | R3 |

## Correction regressions (2026-10-29 REPLAY_CORRECTIONS_AND_R3_HANDOFF pass)

New stable IDs for the C01-C10 findings; C01-C10 themselves are handoff references, not requirement IDs.

| ID | Requirement | Acceptance test / fixture | Ticket |
|---|---|---|---|
| CORR-001 | Source fingerprinting hashes every implementation file under `src/rsys/data/`; only root-level `data/`/`runs/` output dirs are excluded | `test_compute_source_fingerprint_includes_a_nested_data_package`, `test_compute_source_fingerprint_still_excludes_root_level_data_and_runs` | FIX-01 |
| CORR-002 | A method-reference field resolves against a registered, versioned method, not merely a `name:vN` naming pattern | `test_syntactically_valid_but_unregistered_method_reference_is_rejected` | FIX-01 |
| CORR-003 | Two concurrent writers cannot both commit a different terminal outcome for the same run | `test_concurrent_finalizers_cannot_both_commit_a_different_terminal_outcome`, `test_different_result_cannot_overwrite_a_sealed_terminal_outcome` | FIX-02 |
| CORR-004 | A genuine finalization conflict/corruption raises `FinalizationError`, never silently leaves a run `running` | existing `_finalize` regression coverage in `tests/test_run_record.py` | FIX-02 |
| CORR-005 | A reloaded record without in-memory secrets withholds (never leaks) error text when a redactor is genuinely unknown; an explicit redactor still scrubs | `test_reloaded_pending_record_without_explicit_redactor_withholds_error_text_safely`, `test_reloaded_record_with_explicit_redactor_still_scrubs_the_canary` | FIX-02 |
| CORR-006 | A completed provider bar cannot claim availability at or before its own bar-end; OHLC/price/volume positivity is enforced at the event-envelope layer | `test_completed_bar_available_at_its_own_start_is_rejected`, `test_provider_bar_event_rejects_inconsistent_ohlc` | FIX-03 |
| CORR-007 | The builder independently rejects a mismatched-instrument/feed or non-OK-quality event before any mutation | `test_wrong_instrument_trade_cannot_alter_this_builders_state`, `test_non_ok_quality_trade_cannot_alter_builder_state` | FIX-03 |
| CORR-008 | A historical as-of query selects the last version actually published by the cutoff; it never recomputes current contributors and backdates the label | `test_version_store_as_of_does_not_leak_a_later_trades_price_into_an_earlier_query` | FIX-04 |
| CORR-009 | A correction with changed conditions recomputes field eligibility; an unchanged correction keeps the original classification | `test_correction_with_changed_conditions_reclassifies_eligibility`, `test_correction_without_new_conditions_keeps_the_original_eligibility` | FIX-04 |
| CORR-010 | Derived insertion accepts only a strictly later `(availability, phase)` pair; a same-phase/same-instant insertion is rejected regardless of tiebreak ordering | `test_insert_derived_rejects_same_phase_same_instant_even_with_a_larger_tiebreak` | FIX-05 |
| CORR-011 | `latest_trade`/`latest_quote` resolve an availability tie to the actually-last-dispatched event, not the first tie `max()` encounters | `test_latest_trade_at_a_tied_availability_returns_the_last_dispatched_not_the_first`, `test_latest_quote_at_a_tied_availability_returns_the_last_dispatched` | FIX-05 |
| CORR-012 | A `replay`-mode config validates its own required settings; a legacy `fixture`-mode config cannot satisfy them by switching `mode` alone | `test_a_legacy_fixture_config_is_not_accepted_as_a_replay_config`, `test_replay_unsupported_setting_value_rejected` | R3-A |
| CORR-013 | Field eligibility separates open/high/low/close/volume/count/vwap; a late-report condition can be close-eligible without being high/low/vwap-eligible | `test_late_report_condition_allows_close_but_not_high_low_or_vwap` | R3-A |

## R3 integration repair regressions (2026-10-29 R3_INTEGRATION_REPAIR_TASKS pass)

New stable IDs for the R3I-01..14 findings; R3I-01..14 themselves are handoff references, not requirement IDs.

| ID | Requirement | Acceptance test / fixture | Ticket |
|---|---|---|---|
| R3INT-01 | A saved context's own query answers never move after later trades/corrections/closure/actions -- `forming_bar`/`closed_bars` are sourced from the immutable version store, never a live mutable builder query | `test_saved_context_survives_later_trade_correction_and_close`, `test_saved_context_stability_integrated` | IR-02 |
| R3INT-02 | The authoritative trade-built completed-interval history reaches the common `closed_bars()` view, not only provider-publication events | `test_trade_authority_exposes_completed_bars_to_closed_context` | IR-02 |
| R3INT-03 | A provider OHLCV change changes the trace content and its semantic hash | `test_provider_price_change_changes_trace_and_semantic_hash` | IR-05 |
| R3INT-04 | A later quote never changes an earlier quote's trace row (captured at dispatch time, not re-queried afterward) | `test_quote_trace_prefix_is_unchanged_by_future_quotes` | IR-05 |
| R3INT-05 | Every `rsys replay` execution creates a fresh retained run-evidence attempt (distinct run ID + attempt directory); failures are retained too | `test_repeated_replay_runs_produce_identical_semantic_trace_hashes_but_distinct_run_ids`, `test_two_successful_and_one_failed_attempt_all_independently_retained` | IR-05 |
| R3INT-06 | An unrelated timer (wrong boundary name/session) cannot certify replay completion | `test_early_unrelated_timer_cannot_complete_truncated_replay` | IR-03 |
| R3INT-07 | A corporate action straddling a forming bucket explicitly invalidates it; a boundary-exact dispatch is not a straddle | `test_split_cannot_silently_create_a_mixed_unit_bar`, `test_split_exactly_on_a_bucket_boundary_is_not_a_straddle`, `test_unsupported_split_straddle_through_the_runner` | IR-03 |
| R3INT-08 | `replay.input_mode` and `bars.authority` must describe the same actual data; incompatible combinations fail before execution | `test_completed_bar_input_mode_rejects_trade_builder_authority`, `test_historical_trade_input_mode_rejects_provider_publication_authority` | IR-04 |
| R3INT-09 | A `fixture_only`-evidence method reference is recognized by the registry but never satisfies a research-profile gate | `test_fixture_only_method_reference_never_satisfies_a_research_gate`, `test_registered_fixture_only_method_resolves_in_the_registry_itself` | IR-04 |
| R3INT-10 | A rejected correction (e.g. unknown condition) leaves the contributor, eligibility and publication history completely unchanged | `test_rejected_correction_has_no_side_effects` | IR-01 |
| R3INT-11 | Identical redelivery of a trade/correction is an idempotent no-op; a bare trade ID reused in a different bucket is an explicit identity conflict | `test_duplicate_correction_and_identity_conflict_are_safe` | IR-01 |
| R3INT-12 | A first late trade into an already-elapsed bucket is never presented as currently forming | `test_first_late_trade_respects_elapsed_boundary` | IR-02/IR-03 |
| R3INT-13 | Disabled quote capability refuses to serve raw quotes; an invalid-quality provider bar never reaches `closed_bars` | `test_disabled_quote_capability_refuses_to_serve_raw_quotes`, `test_invalid_quality_provider_bar_never_reaches_closed_bars` | IR-06 |
| R3INT-14 | Parity count mismatches are reported independent of VWAP-precision checking; adjustment-mode/quality prerequisites gate comparability | `test_count_mismatch_is_reported_with_default_arguments_no_vwap_precision`, `test_different_adjustment_mode_basis_is_not_comparable`, `test_invalid_quality_provider_bar_is_not_comparable` | IR-06 |

## Future-perturbation harness

1. Build a multi-symbol synthetic stream with scanner changes, orders, open positions, feature warmup and session timers.
2. Choose a cutoff event key K, run the original stream, and collect all feature/ranking/proposal/order/ledger records with event keys <= K.
3. Create a second stream with the identical prefix through K. Change prices, volume, later availability, later universe records and later optional model outputs strictly after K. Preserve input validity.
4. Run again with the same configuration and seed. Assert byte-identical canonical records through K after excluding nondeterministic wall-clock/run-ID fields.
5. Repeat around range close, a same-time cross-symbol boundary and a stop-update boundary. Test late-arriving observations by availability, not merely the market time printed on a bar.
6. Separately permute input file order. After canonical ordering, the entire output must match. Add a deliberately leaky test double and verify that the harness catches it; do not ship the double as a strategy.

## R3SAFE: historical visibility, runtime enforcement and public acceptance

These tests supplement existing requirements; none lowers the research or execution bar. Unless specified, paths are in `tests/test_r3_clock_fixes.py` (clock) or `tests/test_replay_contract_fixes.py` (contract).

| ID | Requirement | Executed acceptance test(s) | Ticket |
|---|---|---|---|
| R3SAFE-001 | Delayed closures/actions cannot rewrite saved or freshly queried historical observations | `test_delayed_timer_cannot_backdate_a_late_trade_into_saved_context`, `test_delayed_action_cannot_rewrite_a_saved_forming_context`, `test_phase_cutoffs_and_same_timestamp_context_are_frozen` | R3 repair |
| R3SAFE-002 | Elapsed minutes close without input timers; first late arrivals have truthful availability | `test_elapsed_minute_closes_without_a_timer_in_the_input`, `test_late_first_arrival_without_prior_instrument_timer_is_closed` | R3 repair |
| R3SAFE-003 | Completed-bar cadence excludes unrelated arrivals and redelivery | `test_closed_observer_is_not_called_again_for_a_later_trade`, `test_duplicate_delivery_does_not_retrigger_a_closed_observer`, `test_provider_redelivery_does_not_retrigger_completed_bar_observer` | R3 repair |
| R3SAFE-004 | Unsupported config fails early; supported sessions and timing are enforced | `test_unsupported_replay_configuration_fails_early`, `test_unknown_policy_or_undeclared_modeled_delay_is_rejected`, `test_declared_replay_windows_follow_dst_and_early_close`, `test_outside_session_and_unknown_calendar_dates_are_rejected`, `test_modeled_delay_is_enforced_and_cannot_be_labeled_recorded` | R3 repair |
| R3SAFE-005 | Public quote access respects freshness/quality without resurrecting old good state | `test_strategy_quote_access_enforces_declared_freshness`, `test_bad_quote_does_not_resurrect_an_older_good_quote` | R3 repair |
| R3SAFE-006 | Full runner-generated decision prefixes resist future perturbations; harness detects an intentional leak | `test_future_perturbations_preserve_full_decision_prefix_and_saved_contexts`, `test_same_prefix_harness_detects_a_deliberately_leaky_runner`, `test_cross_symbol_batch_observer_sees_both_symbols_before_deciding` | R3 repair |
| R3SAFE-007 | Coverage uses source inventory and interval states independently of timers | `test_source_inventory_detects_truncation_even_with_a_closing_timer`, `test_source_inventory_counts_repeated_ids_instead_of_collapsing_them`, `test_verified_quiet_minute_completes_without_fabricating_a_bar` | R3 repair |
| R3SAFE-008 | Real CLI invokes synthetic observers and records decisions/no-decisions/input versions | `test_real_cli_dispatches_named_synthetic_observers` (three cases) | R3 repair |
| R3SAFE-009 | Invalid/unreadable/incomplete inputs retain truthful attempts and controlled errors | `test_unreadable_input_retains_failed_attempt_and_explicit_unavailable_provenance`, `test_recorded_observations_without_receipt_evidence_fail_truthfully`, `test_incomplete_cli_uses_data_error_exit_code`, `test_invalid_event_error_does_not_persist_submitted_secret_values` | R3 repair |
| R3SAFE-010 | Unsupported action-unit combinations fail safely, including late-first-contributor cases | `test_first_late_trade_after_action_cannot_create_a_valid_mixed_unit_bar`, `test_provider_split_straddle_retains_non_success_instead_of_mixed_units` | R3 repair |
| R3SAFE-011 | Provider revisions conflict explicitly; parity requires valid versions and an appropriate cutoff | `test_conflicting_provider_revision_fails_before_acceptance`; `test_provider_bar_parity.py`: `test_invalidated_version_is_not_comparable`, `test_revised_builder_cannot_silently_compare_with_initial_provider_publication`, `test_explicit_comparison_cutoff_rejects_future_publication` | R3 repair |
| R3SAFE-012 | Inputs resolve relative to config; explicit modeled timing can execute | `test_config_relative_input_works_from_another_working_directory`, `test_explicit_modeled_timing_can_run_when_every_external_event_agrees` | R3 repair |
