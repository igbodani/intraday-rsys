"""Configuration failures must be visible before an agent starts a task."""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts/check_copilot_config.py"


def run_check(root):
    return subprocess.run(
        [sys.executable, str(CHECKER), str(root)], check=False, capture_output=True, text=True
    )


@pytest.fixture
def config_repo(tmp_path):
    files = {
        "AGENTS.md": "# Rules\n",
        ".github/copilot-instructions.md": "Read [rules](../AGENTS.md).\n",
        "docs/COPILOT_WORKFLOW.md": "# Workflow\n",
        ".github/agents/python-engineer.agent.md": (
            "---\nname: python-engineer\ndescription: Implement tasks\n"
            "tools: [read, edit, execute]\n---\nImplement the requested task.\n"
        ),
        ".github/instructions/python.instructions.md": (
            '---\napplyTo: "src/**/*.py"\n---\nPreserve model ownership.\n'
        ),
        ".github/prompts/implement.prompt.md": (
            "---\ndescription: Implement a task\nagent: python-engineer\n---\nImplement it.\n"
        ),
        ".github/skills/review/SKILL.md": (
            "---\nname: review\ndescription: Review changes\n---\nInspect the diff.\n"
        ),
        "src/example.py": "",
    }
    for name, content in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return tmp_path


def test_repository_config_is_valid():
    result = run_check(ROOT)
    assert result.returncode == 0, result.stdout + result.stderr


def test_minimal_valid_config(config_repo):
    assert run_check(config_repo).returncode == 0


@pytest.mark.parametrize(
    "name,old,new,expected",
    [
        ("prompts/implement.prompt.md", "agent: python-engineer", "agent: missing", "unknown agent"),
        ("agents/python-engineer.agent.md", "tools: [read, edit, execute]", "tools: ['*']", "tool aliases"),
        ("agents/python-engineer.agent.md", "description: Implement tasks", "description: ''", "nonempty string"),
        ("agents/python-engineer.agent.md", "name: python-engineer", "name: reviewer", "match its path"),
        ("instructions/python.instructions.md", "src/**/*.py", "missing/**/*.py", "matches no current files"),
        ("skills/review/SKILL.md", "name: review", "name: review\nname: duplicate", "duplicate metadata key"),
        ("skills/review/SKILL.md", "Inspect the diff.", "Read [missing](missing.md).", "broken local link"),
        ("prompts/implement.prompt.md", "description: Implement a task", "description: Implement a task\ntools: [edit]", "metadata fields"),
    ],
)
def test_rejects_broken_config(config_repo, name, old, new, expected):
    path = config_repo / ".github" / name
    path.write_text(path.read_text().replace(old, new))
    result = run_check(config_repo)
    assert result.returncode == 1
    assert expected in result.stdout


def test_missing_skill_is_an_error(config_repo):
    (config_repo / ".github/skills/review/SKILL.md").unlink()
    result = run_check(config_repo)
    assert result.returncode == 1
    assert "missing skills files" in result.stdout
