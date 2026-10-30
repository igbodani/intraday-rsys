"""Check this repository's portable Copilot metadata and local references.

This is a structural check, not a Copilot runtime or instruction-quality test.
Run from any directory; optionally pass a repository root for fixture checks.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import yaml


class UniqueLoader(yaml.SafeLoader):
    """Reject duplicate frontmatter keys instead of silently taking the last."""


def unique_mapping(loader, node):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if key in result:
            raise ValueError(f"duplicate metadata key: {key}")
        result[key] = loader.construct_object(value_node)
    return result


UniqueLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping
)

NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
LINK = re.compile(r"(?<!!)\[[^\]\n]+\]\(([^\s)]+)\)")
TOOLS = {"read", "search", "edit", "execute", "web"}
SCHEMAS = {
    "agents": ({"name", "description", "tools"}, {"name", "description", "tools"}),
    "instructions": ({"applyTo"}, {"applyTo"}),
    "prompts": ({"description", "agent"}, {"description", "agent"}),
    "skills": ({"name", "description"}, {"name", "description"}),
}


def check(root: Path) -> list[str]:
    root = root.resolve()
    errors: list[str] = []
    groups = {
        "agents": sorted(root.glob(".github/agents/*.agent.md")),
        "instructions": sorted(root.glob(".github/instructions/*.instructions.md")),
        "prompts": sorted(root.glob(".github/prompts/*.prompt.md")),
        "skills": sorted(root.glob(".github/skills/*/SKILL.md")),
    }
    metadata: dict[Path, dict] = {}
    names: dict[str, set[str]] = {"agents": set(), "skills": set()}
    for kind, paths in groups.items():
        if not paths:
            errors.append(f"missing {kind} files")
        required, allowed = SCHEMAS[kind]
        for path in paths:
            label = path.relative_to(root)
            content = path.read_text(encoding="utf-8")
            parts = content.split("---\n", 2)
            if len(parts) != 3 or parts[0] or not parts[2].strip():
                errors.append(f"{label}: missing frontmatter or body")
                continue
            try:
                meta = yaml.load(parts[1], Loader=UniqueLoader)
                if not isinstance(meta, dict):
                    raise TypeError("metadata must be a mapping")
            except (yaml.YAMLError, ValueError, TypeError) as exc:
                errors.append(f"{label}: invalid YAML: {exc}")
                continue
            metadata[path] = meta
            if not required <= meta.keys() or meta.keys() - allowed:
                errors.append(f"{label}: unsupported or missing metadata fields")
            for key in required - {"tools"}:
                if not isinstance(meta.get(key), str) or not meta[key].strip():
                    errors.append(f"{label}: {key} must be a nonempty string")
            if kind in names:
                name = meta.get("name")
                expected = path.parent.name if kind == "skills" else path.name[:-9]
                if not isinstance(name, str) or not NAME.fullmatch(name) or len(name) > 64:
                    errors.append(f"{label}: invalid name")
                else:
                    if name != expected or name in names[kind]:
                        errors.append(f"{label}: name must be unique and match its path")
                    names[kind].add(name)
            if kind == "agents":
                values = meta.get("tools")
                if not isinstance(values, list) or not values or any(
                    not isinstance(value, str) or value not in TOOLS for value in values
                ):
                    errors.append(f"{label}: unsupported tool aliases")
                elif "edit" in values and meta.get("name") != "python-engineer":
                    errors.append(f"{label}: only python-engineer gets edit tools")
                if len(parts[2]) > 30000:
                    errors.append(f"{label}: agent body exceeds 30000 characters")
            if kind == "instructions" and isinstance(meta.get("applyTo"), str):
                for pattern in meta["applyTo"].split(","):
                    pattern = pattern.strip()
                    if not pattern or pattern.startswith("/") or ".." in Path(pattern).parts:
                        errors.append(f"{label}: invalid applyTo pattern")
                    elif not any(root.glob(pattern)):
                        errors.append(f"{label}: applyTo matches no current files: {pattern}")

    for path in groups["prompts"]:
        agent = metadata.get(path, {}).get("agent")
        if not isinstance(agent, str) or agent not in names["agents"]:
            errors.append(f"{path.relative_to(root)}: prompt references unknown agent")

    required_docs = [
        root / "AGENTS.md",
        root / ".github/copilot-instructions.md",
        root / "docs/COPILOT_WORKFLOW.md",
    ]
    docs = set(required_docs)
    docs.update(root.glob(".github/**/*.md"))
    docs.update(root.glob("docs/agent-workflow/*.md"))
    for path in sorted(docs):
        if not path.is_file():
            errors.append(f"missing {path.relative_to(root)}")
            continue
        for target in LINK.findall(path.read_text(encoding="utf-8")):
            if target.startswith("#") or re.match(r"[a-zA-Z][a-zA-Z0-9+.-]*:", target):
                continue
            destination = (path.parent / target.split("#", 1)[0]).resolve()
            if not destination.is_relative_to(root) or not destination.exists():
                errors.append(f"{path.relative_to(root)}: broken local link: {target}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", type=Path, default=Path(__file__).resolve().parents[1])
    errors = check(parser.parse_args().root)
    if errors:
        print("\n".join(errors))
        return 1
    print("Copilot configuration valid: metadata, role references, globs and local links")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
