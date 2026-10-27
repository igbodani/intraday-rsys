# Agent working rules

## Scope

Build the modular research system defined in this pack. Begin with offline fixtures and one milestone ticket at a time. Keep the universe → replay → positions/features → scanner → strategies → proposal → acceptance → execution → results flow. A symbol leaving the scanner must not disable its position management.

## Decisions and authority

- Read `docs/decisions.md` before dependent work. Confirmed requirements are distinct from proposed research defaults.
- Do not invent a missing market assumption, risk limit, cost, date split, strategy rule or promotion threshold. Record the blocked dependency and ask the owner; continue unrelated work.
- Routine implementation choices may be made and recorded when they do not change research semantics. The owner need not approve variable names or internal refactoring.
- Implement interfaces with explicit settings before the owner chooses their research values. Synthetic fixtures use their own declared test values and must never be promoted to production defaults.
- The task currently authorizes planning artifacts. Later build instructions authorize implementation; market data purchases and trading require their own applicable instructions.

## Research integrity

- Features and decisions receive a bounded as-of view. Broker access, raw future arrays and ledger mutations are outside strategy authority.
- Keep every attempted configuration, including manual changes, failed runs and seed attempts. Never delete losing runs from a comparison.
- Development and tuning cannot access the final holdout. Use only the frozen, preregistered final-evaluation procedure in `docs/experiments.md`; log every access.
- Compare scanner, entry, exit and model changes separately before combinations. Keep account and cost assumptions equal when comparing signals.
- Separate synthetic expected outcomes, verified software outputs, historical evidence and paper results. Never call hand-calculated fixtures backtest results.

## Engineering workflow

- Implement the smallest behavior needed for the current ticket and its independent acceptance checks.
- Use the proposed Python stack and validate compatibility before pinning exact versions. Keep Kronos/Nautilus/model extras out of the core installation.
- Unit and fixture tests make no network calls. Mark provider integration checks separately and run them only when explicitly enabled with appropriate data access.
- Use local fixtures in CI. Never put credentials, market-data downloads or model weights in Git.
- Preserve decimal money, explicit instrument rounding and a single chronological clock. Stable output sorting is required for reproducibility.
- Use the ticket's completion command. Commands and test names below are target interfaces to implement, not claims that they currently exist.
- Report changed files, acceptance checks actually executed, failures, open decisions and the next dependency. Do not claim a milestone complete on placeholders or skipped acceptance checks.

## Optional components

Adapt selected Claude day-trader-skill rules only after source/commit/license review and tests. Kronos is an optional forecast provider. Nautilus is a later comparison or backend. Neither is a prerequisite for the offline baseline. Paper mode follows a frozen historical candidate and requires its own operational tests.
