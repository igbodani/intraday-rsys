"""Package boundaries, cold imports and public compatibility after the layout migration."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "rsys"
PACKAGES = (
    "account",
    "application",
    "cli",
    "data",
    "execution",
    "replay",
    "reporting",
    "research",
    "strategies",
)


def run_python(code):
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result


@pytest.mark.parametrize("package", PACKAGES)
def test_model_namespace_imports_in_a_fresh_process(package):
    run_python(f"import rsys.{package}.models")


def test_every_module_imports_without_running_a_command():
    modules = sorted(
        "rsys." + ".".join(p.relative_to(SOURCE).with_suffix("").parts)
        for p in SOURCE.rglob("*.py")
        if p.name not in ("__init__.py", "__main__.py")
    )
    run_python(
        f"import importlib\nfor name in {modules!r}: importlib.import_module(name)"
    )


def test_plain_record_definitions_are_owned_by_package_models():
    misplaced = []
    for path in SOURCE.rglob("*.py"):
        if path.name == "models.py":
            continue
        for node in ast.parse(path.read_text()).body:
            if not isinstance(node, ast.ClassDef):
                continue
            # Portfolio is a mutable account aggregate with transactions, not a plain record.
            if path == SOURCE / "account" / "portfolio.py" and node.name == "Portfolio":
                continue
            decorators = [ast.unparse(d) for d in node.decorator_list]
            bases = {ast.unparse(b) for b in node.bases}
            if any("dataclass" in d for d in decorators) or bases & {
                "BaseModel",
                "NamedTuple",
                "Enum",
                "StrEnum",
                "IntEnum",
            }:
                misplaced.append(f"{path.relative_to(SOURCE)}:{node.name}")
    assert misplaced == []


def imports(path):
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom) and node.module:
            yield node.module
        elif isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)


def test_core_packages_do_not_depend_on_cli_or_application_orchestration():
    violations = []
    for package in ("account", "data", "replay", "strategies", "execution"):
        forbidden = {"cli", "application", "research", "reporting"}
        if package != "execution":
            forbidden.add("execution")
        for path in (SOURCE / package).glob("*.py"):
            for module in imports(path):
                parts = module.split(".")
                if len(parts) > 1 and parts[0] == "rsys" and parts[1] in forbidden:
                    violations.append(f"{path.relative_to(SOURCE)} imports {module}")
    for path in (SOURCE / "application").glob("*.py"):
        assert all(not m.startswith("rsys.cli") for m in imports(path))
    assert violations == []


def test_models_have_no_service_or_runner_dependencies():
    utilities = {
        "rsys.account.money",
        "rsys.data.timestamps",
        "rsys.data.calendar",
        "rsys.application.validation",
    }
    for path in SOURCE.glob("*/models.py"):
        for module in imports(path):
            if module.startswith("rsys."):
                assert module.endswith((".models", ".errors")) or module in utilities, (
                    path,
                    module,
                )


def test_legacy_runner_name_aliases_the_canonical_implementation():
    from rsys.execution.bar import BarSessionRunner
    from rsys.standalone_run import StandaloneRunner

    assert StandaloneRunner is BarSessionRunner


def test_public_cli_module_entrypoint_still_works():
    from rsys.cli import main
    from rsys.cli.commands import main as command_main

    assert main is command_main
    result = subprocess.run(
        [sys.executable, "-m", "rsys.cli", "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "run" in result.stdout and "compare" in result.stdout
    assert result.stderr == ""
