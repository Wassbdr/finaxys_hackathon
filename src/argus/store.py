"""Small SQLite persistence layer for demo runs, evidence, and governance."""
from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any


class Store:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY, seed INTEGER NOT NULL, status TEXT NOT NULL,
                started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, ts INTEGER NOT NULL,
                instrument TEXT NOT NULL, event_type TEXT NOT NULL, order_id INTEGER NOT NULL,
                trader_id INTEGER NOT NULL, counterparty_id INTEGER NOT NULL DEFAULT 0,
                side INTEGER NOT NULL, price INTEGER NOT NULL, qty INTEGER NOT NULL,
                payload TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS events_by_run_time ON events(run_id, ts, id);
            CREATE TABLE IF NOT EXISTS cases (
                id TEXT PRIMARY KEY, run_id TEXT NOT NULL, title TEXT NOT NULL, status TEXT NOT NULL,
                created_ts INTEGER NOT NULL, agents TEXT NOT NULL, graph TEXT NOT NULL,
                event_ids TEXT NOT NULL, report TEXT
            );
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, ts INTEGER NOT NULL,
                trader_id INTEGER NOT NULL, kind TEXT NOT NULL, source TEXT NOT NULL,
                score REAL NOT NULL, evidence TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS proposals (
                id TEXT PRIMARY KEY, case_id TEXT NOT NULL, action TEXT NOT NULL,
                agents TEXT NOT NULL, duration_ns INTEGER NOT NULL, status TEXT NOT NULL,
                created_ts INTEGER NOT NULL, reviewed_ts INTEGER, reviewer TEXT
            );
            CREATE TABLE IF NOT EXISTS audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, case_id TEXT,
                proposal_id TEXT, ts INTEGER NOT NULL, action TEXT NOT NULL, details TEXT NOT NULL
            );
        """)
        self.db.commit()

    def new_run(self, seed: int) -> str:
        run_id = uuid.uuid4().hex[:12]
        self.db.execute("INSERT INTO runs(id, seed, status) VALUES (?, ?, 'running')", (run_id, seed))
        self.db.commit()
        return run_id

    def set_run_status(self, run_id: str, status: str) -> None:
        self.db.execute("UPDATE runs SET status=? WHERE id=?", (status, run_id))
        self.db.commit()

    def append_events(self, run_id: str, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        stored: list[dict[str, Any]] = []
        with self.db:
            for event in events:
                cursor = self.db.execute(
                    "INSERT INTO events(run_id, ts, instrument, event_type, order_id, trader_id, "
                    "counterparty_id, side, price, qty, payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (run_id, int(event["ts"]), str(event["instrument"]), str(event["event_type"]),
                     int(event["order_id"]), int(event["trader_id"]), int(event.get("counterparty_id", 0)),
                     int(event["side"]), int(event["price"]), int(event["qty"]),
                     json.dumps(event, separators=(",", ":"))),
                )
                value = dict(event)
                value["id"] = cursor.lastrowid
                stored.append(value)
        return stored

    def recent_events(self, run_id: str, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.db.execute("SELECT payload, id FROM events WHERE run_id=? ORDER BY id DESC LIMIT ?",
                               (run_id, limit)).fetchall()
        return [dict(json.loads(row["payload"]), id=row["id"]) for row in reversed(rows)]

    def events_by_ids(self, run_id: str, event_ids: list[int]) -> list[dict[str, Any]]:
        if not event_ids:
            return []
        placeholders = ",".join("?" for _ in event_ids)
        rows = self.db.execute(f"SELECT id, payload FROM events WHERE run_id=? AND id IN ({placeholders}) "
                               "ORDER BY ts, id", [run_id, *event_ids]).fetchall()
        return [dict(json.loads(row["payload"]), id=row["id"]) for row in rows]

    def save_alert(self, run_id: str, alert: dict[str, Any]) -> None:
        self.db.execute("INSERT INTO alerts(run_id, ts, trader_id, kind, source, score, evidence) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (run_id, alert["ts"], alert["trader_id"], alert["kind"], alert["source"],
                         alert["score"], json.dumps(alert["evidence"])))
        self.db.commit()

    def alerts(self, run_id: str, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.db.execute("SELECT * FROM alerts WHERE run_id=? ORDER BY id DESC LIMIT ?",
                               (run_id, limit)).fetchall()
        return [{"id": r["id"], "ts": r["ts"], "trader_id": r["trader_id"], "kind": r["kind"],
                 "source": r["source"], "score": r["score"], "evidence": json.loads(r["evidence"])}
                for r in rows]

    def alerts_for_agents(self, run_id: str, agents: list[int]) -> list[dict[str, Any]]:
        if not agents:
            return []
        marks = ",".join("?" for _ in agents)
        rows = self.db.execute(f"SELECT * FROM alerts WHERE run_id=? AND trader_id IN ({marks}) ORDER BY ts",
                               [run_id, *agents]).fetchall()
        result = []
        for row in rows:
            event_rows = self.db.execute("SELECT id FROM events WHERE run_id=? AND trader_id=? AND ts>=? AND ts<? ORDER BY ts",
                                         (run_id, row["trader_id"], row["ts"], row["ts"] + 10_000_000_000)).fetchall()
            result.append({"id": row["id"], "ts": row["ts"], "trader_id": row["trader_id"],
                           "kind": row["kind"], "source": row["source"], "score": row["score"],
                           "evidence": json.loads(row["evidence"]),
                           "event_ids": [event["id"] for event in event_rows]})
        return result

    def record_audit(self, run_id: str, action: str, details: dict[str, Any], ts: int,
                     case_id: str | None = None, proposal_id: str | None = None) -> None:
        self.db.execute("INSERT INTO audit(run_id, case_id, proposal_id, ts, action, details) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (run_id, case_id, proposal_id, ts, action, json.dumps(details)))
        self.db.commit()

    def create_case(self, run_id: str, title: str, ts: int, agents: list[int],
                    graph: dict[str, Any], event_ids: list[int]) -> str:
        case_id = uuid.uuid4().hex[:10]
        with self.db:
            self.db.execute("INSERT INTO cases(id, run_id, title, status, created_ts, agents, graph, event_ids) "
                            "VALUES (?, ?, ?, 'investigating', ?, ?, ?, ?)",
                            (case_id, run_id, title, ts, json.dumps(agents), json.dumps(graph),
                             json.dumps(event_ids)))
            self.db.execute("INSERT INTO proposals(id, case_id, action, agents, duration_ns, status, created_ts) "
                            "VALUES (?, ?, 'temporary_restriction', ?, ?, 'pending', ?)",
                            (uuid.uuid4().hex[:10], case_id, json.dumps(agents), 10_000_000_000, ts))
            self.db.execute("INSERT INTO audit(run_id, case_id, ts, action, details) VALUES (?, ?, ?, ?, ?)",
                            (run_id, case_id, ts, "case_opened", json.dumps({"agents": agents,
                             "evidence_events": len(event_ids)})))
        return case_id

    @staticmethod
    def _case(row: sqlite3.Row) -> dict[str, Any]:
        return {"id": row["id"], "run_id": row["run_id"], "title": row["title"],
                "status": row["status"], "created_ts": row["created_ts"],
                "agents": json.loads(row["agents"]), "graph": json.loads(row["graph"]),
                "event_ids": json.loads(row["event_ids"]), "report": json.loads(row["report"]) if row["report"] else None}

    def cases(self, run_id: str) -> list[dict[str, Any]]:
        rows = self.db.execute("SELECT * FROM cases WHERE run_id=? ORDER BY created_ts DESC", (run_id,)).fetchall()
        return [self._case(row) for row in rows]

    def case(self, case_id: str) -> dict[str, Any] | None:
        row = self.db.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()
        return self._case(row) if row else None

    def proposals(self, run_id: str) -> list[dict[str, Any]]:
        rows = self.db.execute("SELECT p.*, c.title FROM proposals p JOIN cases c ON c.id=p.case_id "
                               "WHERE c.run_id=? ORDER BY p.created_ts DESC", (run_id,)).fetchall()
        return [{"id": r["id"], "case_id": r["case_id"], "title": r["title"], "action": r["action"],
                 "agents": json.loads(r["agents"]), "duration_ns": r["duration_ns"],
                 "status": r["status"], "created_ts": r["created_ts"]} for r in rows]

    def review_proposal(self, proposal_id: str, decision: str, reviewer: str, ts: int) -> dict[str, Any] | None:
        row = self.db.execute("SELECT p.*, c.run_id FROM proposals p JOIN cases c ON c.id=p.case_id WHERE p.id=?",
                              (proposal_id,)).fetchone()
        if not row:
            return None
        if row["status"] != "pending":
            raise ValueError("proposal has already been reviewed")
        self.db.execute("UPDATE proposals SET status=?, reviewed_ts=?, reviewer=? WHERE id=?",
                        (decision, ts, reviewer, proposal_id))
        self.db.execute("INSERT INTO audit(run_id, case_id, proposal_id, ts, action, details) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (row["run_id"], row["case_id"], proposal_id, ts, f"proposal_{decision}",
                         json.dumps({"reviewer": reviewer, "agents": json.loads(row["agents"]),
                                     "duration_ns": row["duration_ns"]})))
        self.db.commit()
        return {"id": proposal_id, "case_id": row["case_id"], "run_id": row["run_id"],
                "agents": json.loads(row["agents"]), "duration_ns": row["duration_ns"],
                "status": decision}

    def audit(self, run_id: str) -> list[dict[str, Any]]:
        rows = self.db.execute("SELECT * FROM audit WHERE run_id=? ORDER BY id DESC LIMIT 50", (run_id,)).fetchall()
        return [{"id": r["id"], "case_id": r["case_id"], "proposal_id": r["proposal_id"],
                 "ts": r["ts"], "action": r["action"], "details": json.loads(r["details"])} for r in rows]

    def save_report(self, case_id: str, report: dict[str, Any]) -> None:
        self.db.execute("UPDATE cases SET report=? WHERE id=?", (json.dumps(report), case_id))
        self.db.commit()
