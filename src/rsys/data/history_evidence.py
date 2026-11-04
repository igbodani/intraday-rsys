"""Lossless shared history evidence; logical snapshot IDs remain version-1 hashes.

Observations and persistent sequence prefixes are stored once. A snapshot still
resolves to the exact ordered, dated revisions previously embedded in full.
No new selection, pruning, availability or research policy is introduced here.
"""

import hashlib
import json
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import asdict, fields, is_dataclass
from pathlib import Path


def _identity(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _json(value):
    return json.loads(json.dumps(value, default=str))


class HistorySnapshots(Mapping):
    """Active in-memory interning service with copy-isolated logical reads."""

    def __init__(self):
        self.observations = {}
        self.sequences = {}
        self.entries = {}
        self._observation_ids = {}

    def retain(self, value):
        header = _json(
            {
                f.name: asdict(getattr(value, f.name))
                if is_dataclass(getattr(value, f.name))
                else getattr(value, f.name)
                for f in fields(value)
                if f.name != "selected_revisions"
            }
        )
        head, revisions = None, []
        for observation in value.selected_revisions:
            cached = self._observation_ids.get(id(observation))
            identity = cached[1] if cached is not None else None
            if identity is None:
                payload = _json(asdict(observation))
                identity = _identity(payload)
                self.observations[identity] = payload
                self._observation_ids[id(observation)] = (observation, identity)
            revisions.append(self.observations[identity])
            node = {"previous": head, "observation": identity}
            head = _identity(node)
            self.sequences.setdefault(head, node)
        # Keep the existing logical ID (and therefore every semantic trace byte).
        identity = _identity({**header, "selected_revisions": revisions})
        self.entries[identity] = {
            "header": header,
            "sequence": head,
            "count": len(revisions),
        }
        return identity

    def __getitem__(self, identity):
        return _expand(self.entries[identity], self.observations, self.sequences)

    def __iter__(self):
        return iter(self.entries)

    def __len__(self):
        return len(self.entries)

    def __contains__(self, identity):
        return identity in self.entries


def _expand(entry, observations, sequences):
    if not isinstance(entry, dict) or set(entry) != {"header", "sequence", "count"}:
        raise ValueError("invalid history snapshot entry")
    if not isinstance(entry["header"], dict) or "selected_revisions" in entry["header"]:
        raise ValueError("invalid history snapshot header")
    count = entry["count"]
    if type(count) is not int or not 0 <= count <= len(sequences):
        raise ValueError("invalid history sequence length")
    head, revisions = entry["sequence"], []
    try:
        for _ in range(count):
            node = sequences[head]
            revisions.append(observations[node["observation"]])
            head = node["previous"]
    except (KeyError, TypeError) as exc:
        raise ValueError("missing history evidence reference") from exc
    if head is not None:
        raise ValueError("history sequence cycle or length mismatch")
    return deepcopy(
        {**entry["header"], "selected_revisions": list(reversed(revisions))}
    )


def history_archive(stores):
    """Merge shared tables, without expanding per-decision selected revisions."""
    archive = {
        "schema_version": 2,
        "observations": {},
        "sequences": {},
        "snapshots": {},
    }
    for store in stores:
        for table, values in (
            ("observations", store.observations),
            ("sequences", store.sequences),
            ("snapshots", store.entries),
        ):
            for identity, value in values.items():
                if identity in archive[table] and archive[table][identity] != value:
                    raise ValueError("conflicting history evidence identity")
                archive[table][identity] = value
    return archive


class HistoryArchive(Mapping):
    """Validated archive reader, expanding only a requested logical snapshot."""

    def __init__(self, payload, *, version=None):
        if not isinstance(payload, dict):
            raise TypeError("invalid history archive")
        declared = payload.get("schema_version", 1)
        if (
            type(declared) is not int
            or declared not in (1, 2)
            or version is not None
            and declared != version
        ):
            raise ValueError("unsupported history evidence version")
        self.version = declared
        self.payload = payload
        if declared == 1:
            self.entries = payload
        else:
            if set(payload) != {
                "schema_version",
                "observations",
                "sequences",
                "snapshots",
            } or any(
                not isinstance(payload[k], dict)
                for k in ("observations", "sequences", "snapshots")
            ):
                raise ValueError("invalid history archive tables")
            self.entries = payload["snapshots"]
            for identity, observation in payload["observations"].items():
                if (
                    not isinstance(observation, dict)
                    or _identity(observation) != identity
                ):
                    raise ValueError("history observation integrity mismatch")
            for identity, node in payload["sequences"].items():
                if (
                    not isinstance(node, dict)
                    or set(node) != {"previous", "observation"}
                    or _identity(node) != identity
                ):
                    raise ValueError("history sequence integrity mismatch")
                if (
                    node["observation"] not in payload["observations"]
                    or node["previous"] is not None
                    and node["previous"] not in payload["sequences"]
                ):
                    raise ValueError("missing history evidence reference")
        # Verify one logical snapshot at a time, never materialize the whole set.
        for identity in self.entries:
            value = self[identity]
            if (
                not isinstance(value, dict)
                or not isinstance(value.get("selected_revisions"), list)
                or _identity(value) != identity
            ):
                raise ValueError("history snapshot integrity mismatch")

    def __getitem__(self, identity):
        if self.version == 1:
            return deepcopy(self.entries[identity])
        return _expand(
            self.entries[identity],
            self.payload["observations"],
            self.payload["sequences"],
        )

    def __iter__(self):
        return iter(self.entries)

    def __len__(self):
        return len(self.entries)

    def __contains__(self, identity):
        return identity in self.entries


def read_history_archive(path, *, version=None):
    return HistoryArchive(json.loads(Path(path).read_bytes()), version=version)
