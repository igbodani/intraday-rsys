# Windows cache and recovery repair

The supplied Windows log for the experiment-runner revision reported 45 failed
tests, 98 fixture errors, 1,130 passes and one deliberately deselected provider
integration test. This repair changes filesystem/process handling, not strategy,
risk, data-coverage or trial-budget rules.

## Causes and changes

| Observed problem | Change | Regression evidence |
|---|---|---|
| 137 missing-file errors writing deeply nested cache files; three later assertions failed because acquisition never completed | Use Windows extended absolute paths for cache access, including UNC roots. Shorten temporary filenames and create them exclusively. Preserve full object/request hashes and the cache layout. | `test_windows_portability.py`: simulated MAX_PATH rejection, actual object path over 260 characters, reuse/integrity, write/replace failures, temporary-name collision and drive/UNC conversion |
| A huge process ID was classified as dead on Windows | Reject IDs outside the Windows DWORD range before the OS call; recovery reports unknown ownership. | Three out-of-range IDs remain unknown without an OS call; existing live/dead-process recovery tests remain |
| Attempting recovery changed an existing empty guard file | Lock the byte range without writing a placeholder; keep the guard persistent and preserve its contents/modification time. | Empty and legacy nonempty guards remain unchanged when another owner blocks recovery; existing exclusion/race/process-kill tests exercise real OS locks |
| A recovery test compared Windows separators with hard-coded slashes | Normalize test inventory keys with `as_posix()`; retain all byte/hash/time assertions. | Existing receipt/idempotence test |
| A known symlink privilege failure could surface after fixing cache writes | Separate request-scope validation from symlink validation. Only WinError 1314 skips a symlink-specific case; other errors fail. Narrow the existing preflight skip likewise. | Request-scope checks always run; Linux and permitted Windows hosts run real symlink rejection |

Review caught a POSIX regression in the initial patch: applying `abspath` before
`resolve` changed a symlink followed by `..`. The final code converts only Windows
paths, with a separate POSIX regression. Existing cache files need no migration.

## Verification

- Before fixes, new regressions reproduced five failures: overlong temporary
  name, three invalid process IDs and the mutated empty guard.
- Initial focused run: **150 passed**, covering acquisition, preflight,
  diagnostics/recovery and new portability cases.
- After the POSIX correction: **15 portability tests passed**; Ruff, Copilot
  configuration validation and `git diff --check` passed.
- Independent review: **no blocking findings** after the POSIX correction;
  17 targeted portability/cache tests and five existing recovery tests passed.
- Final Linux full suite: **1,289 passed, one real-provider integration test
  deliberately deselected**, in 466.56 seconds. The outer Python socket/DNS
  tripwire was verified loaded, with dummy credentials only. Zero failures/skips.
- Reviewed Python diff fingerprint:
  `6ba03c2b7844217417507fa45268df2b13e6b873cc312cdbe5cdad216783338c`.
- CI now runs the offline suite on Ubuntu and Windows. Native Windows results
  must be checked on the published revision; Linux and mocked API checks alone
  do not establish native Windows success.

## Local verification

From the repository root:

```powershell
uv sync --frozen --group dev
uv run pytest tests/ -q -rs
uv run ruff check .
```

No credentials or administrator access are required for the offline suite.
A Windows account without symlink privilege may skip two specific symlink cases;
the POSIX-only path test also skips on Windows. Real-provider integration remains
excluded by default. Do not enable it merely to validate this repair.

Internal cache paths no longer require a Windows long-path registry change.
This does not promise arbitrary path-length support in external editors or all
other application outputs; keep project/output roots reasonably short. These
tests do not establish IEX coverage or strategy profitability.

Implementation references, checked 2026-11-04:

- [Microsoft: extended-length local and UNC paths](https://learn.microsoft.com/en-us/windows/win32/fileio/maximum-file-path-limitation)
- [Microsoft: `_locking`, including locking beyond EOF](https://learn.microsoft.com/en-us/cpp/c-runtime-library/reference/locking?view=msvc-170)
