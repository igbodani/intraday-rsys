"""Shared authoritative lifecycle for file-only checks, separate from research trials."""

from contextlib import contextmanager
from pathlib import Path

from rsys.research.diagnostics import invocation_scope, protect_evidence_write
from rsys.research.errors import DiagnosticWriteError
from rsys.research.trace import file_sha256


@contextmanager
def file_attempt(
    out_dir,
    result,
    *,
    command,
    directory,
    link_method,
    report_name,
    renderer,
    write_json,
    invocation=None,
):
    with invocation_scope(out_dir, command=command, invocation=invocation) as writer:
        attempt = Path(out_dir) / directory / writer.invocation_id
        manifest = attempt / "manifest.json"
        owns = False
        try:
            attempt.mkdir(parents=True, exist_ok=False)
            owns = True
            getattr(writer, link_method)(str(manifest))
            yield writer, attempt
        except BaseException as exc:
            if owns:
                exc.manifest_path = manifest
            result["status"] = (
                "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed"
            )
            if result.get("real_provider_gate") == "pending_manual_review":
                result["real_provider_gate"] = "failed"
            if "can_execute" in result:
                result["can_execute"] = False
            result["error"] = {
                "type": type(exc).__name__,
                "message": writer.error_message(exc),
            }
            writer.fail(exc)
            raise
        finally:

            def finalize():
                if not owns:
                    return
                if writer.stage != "failed":
                    writer.set_stage("finalizing")
                write_json(attempt / report_name, result)
                (attempt / "report.html").write_text(renderer(result), encoding="utf-8")
                envelope = {
                    "status": "finalizing",
                    "error": result.get("error", {}),
                    "artifacts": {
                        p.name: file_sha256(p)
                        for p in sorted(attempt.iterdir())
                        if p.is_file() and p.name != "manifest.json"
                    },
                }
                write_json(manifest, envelope)
                if result["status"] == "success":
                    writer.artifacts_ready(stage=writer.stage)
                envelope.update(status=result["status"], diagnostics=writer.seal())
                write_json(manifest, envelope)

            try:
                protect_evidence_write(finalize, catch=(OSError, DiagnosticWriteError))
            except BaseException as exc:
                exc.manifest_path = manifest
                writer.fail(exc)
                if owns:
                    protect_evidence_write(
                        lambda error=exc: write_json(
                            manifest,
                            {
                                "status": "failed",
                                "error": {
                                    "type": type(error).__name__,
                                    "message": writer.error_message(error),
                                },
                            },
                        ),
                        catch=(OSError, DiagnosticWriteError),
                    )
                raise
