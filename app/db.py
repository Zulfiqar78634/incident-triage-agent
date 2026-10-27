import json
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "agent.db"


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                state TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
                type TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(run_id) REFERENCES runs(id)
            );

            CREATE TABLE IF NOT EXISTS approvals (
                id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                action TEXT NOT NULL,
                rationale TEXT NOT NULL,
                risk TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                decided_at TEXT
            );

            CREATE TABLE IF NOT EXISTS long_term_memory (
                id TEXT PRIMARY KEY,
                incident_type TEXT NOT NULL,
                summary TEXT NOT NULL,
                resolution TEXT NOT NULL,
                confidence REAL NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )


def save_run(run_id: str, status: str, state: dict) -> None:
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO runs(id, status, state, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                status=excluded.status,
                state=excluded.state,
                updated_at=excluded.updated_at
            """,
            (run_id, status, json.dumps(state, default=str), now, now),
        )


def load_run(run_id: str) -> tuple[str, dict] | None:
    with get_connection() as conn:
        row = conn.execute("SELECT status, state FROM runs WHERE id=?", (run_id,)).fetchone()
    if not row:
        return None
    return row["status"], json.loads(row["state"])


def append_event(run_id: str, event_type: str, payload: dict) -> int:
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        seq = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 FROM events WHERE run_id=?", (run_id,)
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO events(run_id, seq, type, payload, created_at) VALUES (?, ?, ?, ?, ?)",
            (run_id, seq, event_type, json.dumps(payload, default=str), now),
        )
    return seq


def list_events(run_id: str) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT seq, type, payload, created_at FROM events WHERE run_id=? ORDER BY seq", (run_id,)
        ).fetchall()
    return [
        {"seq": r["seq"], "type": r["type"], "payload": json.loads(r["payload"]), "created_at": r["created_at"]}
        for r in rows
    ]


def create_approval(approval: dict) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO approvals(id, run_id, action, rationale, risk, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                approval["id"], approval["run_id"], approval["action"],
                approval["rationale"], approval["risk"], approval["status"], approval["created_at"],
            ),
        )


def get_approval(approval_id: str) -> dict | None:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone()
    return dict(row) if row else None


def decide_approval(approval_id: str, decision: str) -> dict | None:
    from datetime import datetime, timezone

    with get_connection() as conn:
        row = conn.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone()
        if not row:
            return None
        approval = dict(row)
        if approval["status"] != "pending":
            return approval
        conn.execute(
            "UPDATE approvals SET status=?, decided_at=? WHERE id=?",
            (decision, datetime.now(timezone.utc).isoformat(), approval_id),
        )
    approval["status"] = decision
    return approval


def remember_resolution(incident_type: str, summary: str, resolution: str, confidence: float) -> None:
    import uuid
    from datetime import datetime, timezone

    with get_connection() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO long_term_memory(id, incident_type, summary, resolution, confidence, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()), incident_type, summary, resolution, confidence,
                datetime.now(timezone.utc).isoformat(),
            ),
        )


def recall_similar_incidents(incident_type: str, limit: int = 3) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT incident_type, summary, resolution, confidence, created_at
            FROM long_term_memory
            WHERE incident_type=?
            ORDER BY created_at DESC LIMIT ?
            """,
            (incident_type, limit),
        ).fetchall()
    return [dict(r) for r in rows]
