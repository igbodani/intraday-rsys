"""Transactional local study registry; reservations never disappear on restart."""

import json
import sqlite3
from pathlib import Path

from rsys.research.errors import StudyBudgetError
from rsys.research.storage import canonical_hash


class StudyRegistry:
    def __init__(self, root):
        self.connection = sqlite3.connect(
            Path(root) / "studies.db", timeout=30, isolation_level=None
        )
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript("""
        CREATE TABLE IF NOT EXISTS studies (
          study_id TEXT PRIMARY KEY, contract_hash TEXT NOT NULL,
          contract TEXT NOT NULL, trial_limit INTEGER NOT NULL, external_count INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS trials (
          trial_id INTEGER PRIMARY KEY AUTOINCREMENT, study_id TEXT NOT NULL,
          parent_id TEXT NOT NULL, arm TEXT NOT NULL, config TEXT NOT NULL,
          invocation_id TEXT NOT NULL, run_id TEXT, state TEXT NOT NULL, error TEXT,
          UNIQUE(parent_id, arm));
        """)

    def close(self):
        self.connection.close()

    def register(self, study_id, contract, limit, external_count):
        fingerprint = canonical_hash(contract)
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            existing = self.connection.execute(
                "SELECT * FROM studies WHERE study_id=?", (study_id,)
            ).fetchone()
            if existing is None:
                self.connection.execute(
                    "INSERT INTO studies VALUES(?,?,?,?,?)",
                    (
                        study_id,
                        fingerprint,
                        json.dumps(contract, sort_keys=True),
                        limit,
                        external_count,
                    ),
                )
            elif (
                existing["contract_hash"] != fingerprint
                or existing["trial_limit"] != limit
                or existing["external_count"] != external_count
            ):
                raise ValueError(
                    "Study contract is frozen; reruns cannot change its plan or budget"
                )
            self.connection.execute("COMMIT")
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise

    def reserve(self, study_id, parent_id, arm, config, invocation_id):
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            study = self.connection.execute(
                "SELECT * FROM studies WHERE study_id=?", (study_id,)
            ).fetchone()
            if study is None:
                raise ValueError("Study must be registered before reserving a trial")
            used = self.connection.execute(
                "SELECT COUNT(*) FROM trials WHERE study_id=?", (study_id,)
            ).fetchone()[0]
            if used + study["external_count"] >= study["trial_limit"]:
                raise StudyBudgetError("Declared study trial budget exhausted")
            cursor = self.connection.execute(
                "INSERT INTO trials(study_id,parent_id,arm,config,invocation_id,state) VALUES(?,?,?,?,?,'running')",
                (
                    study_id,
                    parent_id,
                    arm,
                    json.dumps(config, sort_keys=True),
                    invocation_id,
                ),
            )
            trial_id = cursor.lastrowid
            self.connection.execute("COMMIT")
            return trial_id
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise

    def finish(self, trial_id, state, run_id=None, error=None):
        if state not in ("success", "failed", "interrupted"):
            raise ValueError("Invalid terminal trial state")
        changed = self.connection.execute(
            "UPDATE trials SET state=?,run_id=?,error=? WHERE trial_id=? AND state='running'",
            (state, run_id, error, trial_id),
        ).rowcount
        if changed != 1:
            raise ValueError("Trial is absent or already terminal")

    def trials(self, study_id):
        return [
            dict(row)
            for row in self.connection.execute(
                "SELECT * FROM trials WHERE study_id=? ORDER BY trial_id", (study_id,)
            )
        ]
