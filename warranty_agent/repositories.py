from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class ClaimRepository:
    """SQLite persistence for claims, decisions, actions, and audit events."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        con = sqlite3.connect(self.db_path, timeout=15)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA busy_timeout=15000")
        try:
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as con:
            con.executescript("""
            CREATE TABLE IF NOT EXISTS claims (
                claim_id TEXT PRIMARY KEY, customer_name TEXT NOT NULL, product_ref TEXT NOT NULL,
                claim_date TEXT, claim_text TEXT NOT NULL, intent TEXT, issue_summary TEXT,
                requested_product TEXT, warranty_json TEXT, support_json TEXT,
                decision TEXT, reason TEXT, action_json TEXT, status TEXT NOT NULL DEFAULT 'new',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                interpretation_json TEXT, evidence_json TEXT, graph_thread_id TEXT,
                policy_version TEXT, reviewer TEXT, reviewer_note TEXT, proposal_json TEXT
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, claim_id TEXT NOT NULL,
                event_type TEXT NOT NULL, actor TEXT NOT NULL, details_json TEXT NOT NULL,
                created_at TEXT NOT NULL, FOREIGN KEY(claim_id) REFERENCES claims(claim_id)
            );
            CREATE TABLE IF NOT EXISTS actions (
                action_id TEXT PRIMARY KEY, claim_id TEXT NOT NULL, action_type TEXT NOT NULL,
                amount REAL, status TEXT NOT NULL, details_json TEXT NOT NULL, created_at TEXT NOT NULL,
                idempotency_key TEXT, FOREIGN KEY(claim_id) REFERENCES claims(claim_id)
            );
            CREATE TABLE IF NOT EXISTS llm_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT, claim_id TEXT NOT NULL,
                provider TEXT NOT NULL, model TEXT NOT NULL, agent TEXT NOT NULL,
                prompt_tokens INTEGER, completion_tokens INTEGER, total_tokens INTEGER,
                usage_available INTEGER NOT NULL, created_at TEXT NOT NULL,
                FOREIGN KEY(claim_id) REFERENCES claims(claim_id)
            );
            CREATE TABLE IF NOT EXISTS human_reviews (
                review_id INTEGER PRIMARY KEY AUTOINCREMENT, claim_id TEXT NOT NULL,
                reviewer TEXT NOT NULL, decision TEXT NOT NULL, note TEXT NOT NULL,
                created_at TEXT NOT NULL, FOREIGN KEY(claim_id) REFERENCES claims(claim_id)
            );
            """)
            self._migrate_columns(con)
            con.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_actions_idempotency ON actions(idempotency_key) WHERE idempotency_key IS NOT NULL"
            )
            con.execute("CREATE INDEX IF NOT EXISTS ix_claims_status_date ON claims(status, claim_date)")
            con.execute("CREATE INDEX IF NOT EXISTS ix_events_claim_id ON events(claim_id, id)")
            con.execute("CREATE INDEX IF NOT EXISTS ix_llm_usage_claim_id ON llm_usage(claim_id, id)")

    @staticmethod
    def _migrate_columns(con: sqlite3.Connection) -> None:
        existing = {r["name"] for r in con.execute("PRAGMA table_info(claims)")}
        additions = {
            "interpretation_json": "TEXT",
            "evidence_json": "TEXT",
            "graph_thread_id": "TEXT",
            "policy_version": "TEXT",
            "reviewer": "TEXT",
            "reviewer_note": "TEXT",
            "proposal_json": "TEXT",
        }
        for column, sql_type in additions.items():
            if column not in existing:
                con.execute(f"ALTER TABLE claims ADD COLUMN {column} {sql_type}")
        if "confidence" in existing:
            con.execute("ALTER TABLE claims DROP COLUMN confidence")
        for row in con.execute("SELECT claim_id,interpretation_json,evidence_json FROM claims").fetchall():
            cleaned = {}
            for column in ("interpretation_json", "evidence_json"):
                raw = row[column]
                if raw:
                    try:
                        value = json.loads(raw)
                    except (TypeError, json.JSONDecodeError):
                        continue
                    if isinstance(value, dict) and "confidence" in value:
                        value.pop("confidence", None)
                        cleaned[column] = json.dumps(value, ensure_ascii=False)
            if cleaned:
                con.execute(
                    "UPDATE claims SET " + ",".join(f"{column}=?" for column in cleaned) + " WHERE claim_id=?",
                    (*cleaned.values(), row["claim_id"]),
                )
        action_columns = {r["name"] for r in con.execute("PRAGMA table_info(actions)")}
        if "idempotency_key" not in action_columns:
            con.execute("ALTER TABLE actions ADD COLUMN idempotency_key TEXT")

    def import_claims(self, claims: list[dict]) -> int:
        required = {
            "claim_id",
            "customer_name",
            "product_ref",
            "claim_date",
            "claim_text",
        }
        for row in claims:
            missing = required - set(row)
            empty = {key for key in required if not str(row.get(key, "")).strip()}
            if missing or empty:
                raise ValueError(f"Claim import row is missing required values: {', '.join(sorted(missing | empty))}")
        claim_ids = [str(row["claim_id"]).strip() for row in claims]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("Claim import contains duplicate claim_id values.")
        inserted = 0
        with self.connect() as con:
            for row in claims:
                cur = con.execute(
                    """INSERT OR IGNORE INTO claims
                    (claim_id,customer_name,product_ref,claim_date,claim_text,status,created_at,updated_at)
                    VALUES(?,?,?,?,?,'new',?,?)""",
                    (
                        row["claim_id"],
                        row["customer_name"],
                        row["product_ref"],
                        row["claim_date"],
                        row["claim_text"],
                        now_utc(),
                        now_utc(),
                    ),
                )
                inserted += cur.rowcount
                if cur.rowcount:
                    self._event(
                        con,
                        row["claim_id"],
                        "claim_imported",
                        "system",
                        {"source": "seed_csv"},
                    )
        return inserted

    def clear_saved_data(self, seed_claims: list[dict]) -> int:
        """Reset the app ledger/checkpoints, then restore pristine seed claims."""
        # Keep everything in one transaction so a failure cannot leave a
        # partially-cleared app. Source CSVs and policy live outside the DB.
        required = {
            "claim_id",
            "customer_name",
            "product_ref",
            "claim_date",
            "claim_text",
        }
        for row in seed_claims:
            if required - set(row) or any(not str(row.get(key, "")).strip() for key in required):
                raise ValueError("Seed claim data is missing required values.")
        if len({row["claim_id"] for row in seed_claims}) != len(seed_claims):
            raise ValueError("Seed claim data contains duplicate claim IDs.")
        with self.connect() as con:
            con.execute("DELETE FROM human_reviews")
            con.execute("DELETE FROM llm_usage")
            con.execute("DELETE FROM actions")
            con.execute("DELETE FROM events")
            con.execute("DELETE FROM claims")
            checkpoint_tables = {
                row["name"] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            for table in ("writes", "checkpoints", "checkpoint_blobs"):
                if table in checkpoint_tables:
                    con.execute(f"DELETE FROM {table}")
            for row in seed_claims:
                created_at = now_utc()
                con.execute(
                    """INSERT INTO claims
                    (claim_id,customer_name,product_ref,claim_date,claim_text,status,created_at,updated_at)
                    VALUES(?,?,?,?,?,'new',?,?)""",
                    (
                        row["claim_id"],
                        row["customer_name"],
                        row["product_ref"],
                        row["claim_date"],
                        row["claim_text"],
                        created_at,
                        created_at,
                    ),
                )
                self._event(
                    con,
                    row["claim_id"],
                    "claim_imported",
                    "system",
                    {"source": "seed_csv"},
                )
        return len(seed_claims)

    def list_claims(self) -> list[dict]:
        with self.connect() as con:
            return [dict(r) for r in con.execute("SELECT * FROM claims ORDER BY claim_date DESC, claim_id DESC")]

    def get_claim(self, claim_id: str) -> dict | None:
        with self.connect() as con:
            row = con.execute("SELECT * FROM claims WHERE claim_id=?", (claim_id,)).fetchone()
            return dict(row) if row else None

    def update_claim(
        self,
        claim_id: str,
        values: dict,
        event_type: str | None = None,
        actor: str = "agent",
    ) -> None:
        allowed = {
            "intent",
            "issue_summary",
            "requested_product",
            "warranty_json",
            "support_json",
            "decision",
            "reason",
            "action_json",
            "status",
            "interpretation_json",
            "evidence_json",
            "graph_thread_id",
            "policy_version",
            "reviewer",
            "reviewer_note",
            "proposal_json",
        }
        update = {k: v for k, v in values.items() if k in allowed}
        update["updated_at"] = now_utc()
        assignments = ",".join(f"{key}=?" for key in update)
        with self.connect() as con:
            con.execute(
                f"UPDATE claims SET {assignments} WHERE claim_id=?",
                (*update.values(), claim_id),
            )
            if update.get("status") == "processing" and event_type is None:
                self._event(con, claim_id, "processing_started", actor, {})
            if event_type:
                audit_fields = {
                    "status",
                    "decision",
                    "reason",
                    "policy_version",
                    "reviewer",
                    "reviewer_note",
                }
                audit_details = {key: value for key, value in values.items() if key in audit_fields}
                self._event(con, claim_id, event_type, actor, audit_details)

    def record_event(self, claim_id: str, event_type: str, actor: str, details: dict | None = None) -> None:
        """Append one immutable, timestamped claim timeline event."""
        with self.connect() as con:
            self._event(con, claim_id, event_type, actor, details or {})

    def record_llm_usage(
        self,
        claim_id: str,
        provider: str,
        model: str,
        agent: str,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        total_tokens: int | None,
    ) -> None:
        available = total_tokens is not None
        created_at = now_utc()
        details = {
            "provider": provider,
            "model": model,
            "agent": agent,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "usage_available": available,
        }
        with self.connect() as con:
            con.execute(
                """INSERT INTO llm_usage
                (claim_id,provider,model,agent,prompt_tokens,completion_tokens,total_tokens,usage_available,created_at)
                VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    claim_id,
                    provider,
                    model,
                    agent,
                    prompt_tokens,
                    completion_tokens,
                    total_tokens,
                    int(available),
                    created_at,
                ),
            )
            self._event(con, claim_id, "llm_usage_recorded", f"llm:{provider}:{model}", details)

    def list_llm_usage(self, claim_id: str | None = None, limit: int = 500) -> list[dict]:
        with self.connect() as con:
            safe_limit = max(1, min(int(limit), 2000))
            if claim_id:
                rows = con.execute(
                    "SELECT * FROM llm_usage WHERE claim_id=? ORDER BY id DESC LIMIT ?",
                    (claim_id, safe_limit),
                )
            else:
                rows = con.execute("SELECT * FROM llm_usage ORDER BY id DESC LIMIT ?", (safe_limit,))
            return [dict(row) for row in rows]

    def summarize_llm_usage(self) -> dict:
        with self.connect() as con:
            totals = con.execute("""SELECT COUNT(*) AS calls,
                COALESCE(SUM(usage_available), 0) AS reported_calls,
                COALESCE(SUM(CASE WHEN usage_available=1 THEN prompt_tokens ELSE 0 END), 0) AS prompt_tokens,
                COALESCE(SUM(CASE WHEN usage_available=1 THEN completion_tokens ELSE 0 END), 0) AS completion_tokens,
                COALESCE(SUM(CASE WHEN usage_available=1 THEN total_tokens ELSE 0 END), 0) AS total_tokens
                FROM llm_usage""").fetchone()
            grouped = con.execute("""SELECT provider,model,COUNT(*) AS calls,
                COALESCE(SUM(usage_available),0) AS reported_calls,
                COALESCE(SUM(CASE WHEN usage_available=1 THEN prompt_tokens ELSE 0 END),0) AS prompt_tokens,
                COALESCE(SUM(CASE WHEN usage_available=1 THEN completion_tokens ELSE 0 END),0) AS completion_tokens,
                COALESCE(SUM(CASE WHEN usage_available=1 THEN total_tokens ELSE 0 END),0) AS total_tokens
                FROM llm_usage GROUP BY provider,model ORDER BY total_tokens DESC""").fetchall()
            result = dict(totals)
            result["unreported_calls"] = result["calls"] - result["reported_calls"]
            result["by_model"] = [dict(row) for row in grouped]
            return result

    def list_actions(self, claim_id: str | None = None) -> list[dict]:
        with self.connect() as con:
            if claim_id:
                rows = con.execute(
                    "SELECT * FROM actions WHERE claim_id=? ORDER BY created_at DESC, action_id DESC",
                    (claim_id,),
                )
            else:
                rows = con.execute("SELECT * FROM actions ORDER BY created_at DESC")
            return [dict(row) for row in rows]

    def has_action(self, claim_id: str) -> bool:
        return bool(self.list_actions(claim_id))

    def list_reviews(self, claim_id: str) -> list[dict]:
        with self.connect() as con:
            return [
                dict(row)
                for row in con.execute(
                    "SELECT review_id,claim_id,reviewer,decision,note,created_at FROM human_reviews WHERE claim_id=? ORDER BY review_id DESC",
                    (claim_id,),
                )
            ]

    def count_recent_replacements(self, customer_name: str, days: int = 30) -> int:
        """Count successfully recorded simulated replacements for this customer."""
        from datetime import timedelta

        cutoff = (datetime.now(timezone.utc) - timedelta(days=max(1, days))).isoformat(timespec="milliseconds")
        normalized_name = customer_name.strip()
        with self.connect() as con:
            row = con.execute(
                """SELECT COUNT(*) AS total
                FROM actions AS a JOIN claims AS c ON c.claim_id=a.claim_id
                WHERE LOWER(TRIM(c.customer_name))=LOWER(TRIM(?))
                  AND a.action_type='replacement'
                  AND a.status IN ('simulated_scheduled','simulated_success')
                  AND a.created_at>=?""",
                (normalized_name, cutoff),
            ).fetchone()
        return int(row["total"] or 0)

    def begin_run(self, claim_id: str) -> str:
        thread_id = f"{claim_id}:{uuid.uuid4().hex}"
        with self.connect() as con:
            cursor = con.execute(
                """UPDATE claims SET status='processing',graph_thread_id=?,intent=NULL,
                issue_summary=NULL,requested_product=NULL,warranty_json=NULL,
                support_json=NULL,decision=NULL,reason=NULL,action_json=NULL,
                interpretation_json=NULL,evidence_json=NULL,proposal_json=NULL,
                reviewer=NULL,reviewer_note=NULL,updated_at=?
                WHERE claim_id=? AND status IN ('new','error')""",
                (thread_id, now_utc(), claim_id),
            )
            if cursor.rowcount != 1:
                exists = con.execute("SELECT 1 FROM claims WHERE claim_id=?", (claim_id,)).fetchone()
                if not exists:
                    raise ValueError(f"Claim '{claim_id}' was not found.")
                raise ValueError(
                    "Only new or failed claims can be processed. Another worker may already have claimed it."
                )
            self._event(con, claim_id, "processing_started", "system", {"thread_id": thread_id})
        return thread_id

    def recover_stale_processing(self) -> int:
        """Move claims interrupted by a process restart back to the retryable error state."""
        recovered = 0
        with self.connect() as con:
            rows = con.execute("SELECT claim_id FROM claims WHERE status='processing'").fetchall()
            for row in rows:
                claim_id = row["claim_id"]
                cursor = con.execute(
                    "UPDATE claims SET status='error',reason=?,updated_at=? WHERE claim_id=? AND status='processing'",
                    ("Interrupted by restart", now_utc(), claim_id),
                )
                if cursor.rowcount:
                    self._event(
                        con,
                        claim_id,
                        "processing_recovered",
                        "system",
                        {
                            "previous_status": "processing",
                            "reason": "Interrupted by restart",
                        },
                    )
                    recovered += 1
        return recovered

    def add_action(
        self,
        claim_id: str,
        action: dict,
        actor: str = "agent",
        reviewer: str | None = None,
        reviewer_note: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict:
        idempotency_key = idempotency_key or f"{claim_id}:{action['action_type']}:{action.get('product_ref', '')}"
        with self.connect() as con:
            candidates = con.execute(
                """SELECT * FROM actions WHERE claim_id=? AND action_type=?
                AND (idempotency_key=? OR idempotency_key LIKE ?) ORDER BY created_at DESC, action_id DESC""",
                (
                    claim_id,
                    action["action_type"],
                    idempotency_key,
                    f"{idempotency_key}:retry:%",
                ),
            ).fetchall()
            existing = candidates[0] if candidates else None
            if existing:
                action = json.loads(existing["details_json"])
                # Idempotent retries must not recreate the external action, but
                # the workflow still records the prior result on the claim.
                self._event(con, claim_id, "action_reused", actor, action)
                if existing["claim_id"] != claim_id:
                    action = {
                        **action,
                        "action_id": uuid.uuid4().hex,
                        "claim_id": claim_id,
                        "external_reference": f"SIM-{uuid.uuid4().hex[:12].upper()}",
                    }
                    con.execute(
                        """INSERT INTO actions(action_id,claim_id,action_type,amount,status,details_json,created_at,idempotency_key)
                        VALUES(?,?,?,?,?,?,?,?)""",
                        (
                            action["action_id"],
                            claim_id,
                            action["action_type"],
                            action.get("amount", 0),
                            action["status"],
                            json.dumps(action),
                            now_utc(),
                            f"{claim_id}:{action['action_type']}:{action.get('product_ref', '')}",
                        ),
                    )
                if reviewer:
                    note = reviewer_note or "Approved through review workflow."
                    con.execute(
                        "INSERT INTO human_reviews(claim_id,reviewer,decision,note,created_at) VALUES(?,?,?,?,?)",
                        (claim_id, reviewer, "approve", note, now_utc()),
                    )
                    con.execute(
                        """UPDATE claims SET reviewer=?,reviewer_note=?,decision='human_approved_action',
                        reason=?,status='resolved',updated_at=? WHERE claim_id=?""",
                        (reviewer, note, note, now_utc(), claim_id),
                    )
                    self._event(
                        con,
                        claim_id,
                        "human_override",
                        reviewer,
                        {"decision": "approve", "note": note, "action": action},
                    )
                con.execute(
                    "UPDATE claims SET action_json=?,updated_at=? WHERE claim_id=?",
                    (json.dumps(action, ensure_ascii=False), now_utc(), claim_id),
                )
                return action
            action = {
                **action,
                "action_id": uuid.uuid4().hex,
                "claim_id": claim_id,
                "external_reference": f"SIM-{uuid.uuid4().hex[:12].upper()}",
                "status": "simulated_success" if action["action_type"] != "replacement" else "simulated_scheduled",
            }
            con.execute(
                """INSERT INTO actions(action_id,claim_id,action_type,amount,status,details_json,created_at,idempotency_key)
                VALUES(?,?,?,?,?,?,?,?)""",
                (
                    action["action_id"],
                    claim_id,
                    action["action_type"],
                    action.get("amount", 0),
                    action["status"],
                    json.dumps(action),
                    now_utc(),
                    idempotency_key,
                ),
            )
            self._event(con, claim_id, "action_taken", actor, action)
            if reviewer:
                note = reviewer_note or "Approved through review workflow."
                con.execute(
                    "INSERT INTO human_reviews(claim_id,reviewer,decision,note,created_at) VALUES(?,?,?,?,?)",
                    (claim_id, reviewer, "approve", note, now_utc()),
                )
                con.execute(
                    """UPDATE claims SET reviewer=?,reviewer_note=?,decision='human_approved_action',
                    reason=?,action_json=?,status='resolved',updated_at=? WHERE claim_id=?""",
                    (
                        reviewer,
                        note,
                        note,
                        json.dumps(action, ensure_ascii=False),
                        now_utc(),
                        claim_id,
                    ),
                )
                self._event(
                    con,
                    claim_id,
                    "human_override",
                    reviewer,
                    {"decision": "approve", "note": note, "action": action},
                )
        return action

    def record_override(self, claim_id: str, reviewer: str, decision: str, note: str) -> None:
        with self.connect() as con:
            con.execute(
                "INSERT INTO human_reviews(claim_id,reviewer,decision,note,created_at) VALUES(?,?,?,?,?)",
                (claim_id, reviewer or "human", decision, note, now_utc()),
            )
            self._event(
                con,
                claim_id,
                "human_override",
                reviewer or "human",
                {"decision": decision, "note": note},
            )
            if decision == "review":
                con.execute(
                    "UPDATE claims SET reviewer=?,reviewer_note=?,decision=?,reason=?,status='awaiting_review',updated_at=? WHERE claim_id=?",
                    (
                        reviewer,
                        note,
                        "human_override_review",
                        note,
                        now_utc(),
                        claim_id,
                    ),
                )
            elif decision == "deny":
                con.execute(
                    "UPDATE claims SET reviewer=?,reviewer_note=?,decision=?,reason=?,status=?,updated_at=? WHERE claim_id=?",
                    (
                        reviewer,
                        note,
                        "human_denied",
                        note,
                        "denied",
                        now_utc(),
                        claim_id,
                    ),
                )

    def reverse_automated_action(self, claim_id: str, reviewer: str, note: str) -> dict:
        """Append a human reversal for an agent-executed simulated action."""
        expected_actions = {
            "auto_refund": "refund",
            "auto_credit": "credit",
            "auto_replacement": "replacement",
        }
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            claim = con.execute("SELECT * FROM claims WHERE claim_id=?", (claim_id,)).fetchone()
            if not claim:
                raise ValueError(f"Claim '{claim_id}' was not found.")
            action_type = expected_actions.get(claim["decision"])
            if claim["status"] != "resolved" or not action_type or claim["reviewer"]:
                raise ValueError("Only an agent auto-resolved action can be overridden here.")

            action_row = con.execute(
                "SELECT * FROM actions WHERE claim_id=? AND action_type=? ORDER BY created_at DESC LIMIT 1",
                (claim_id, action_type),
            ).fetchone()
            if not action_row or action_row["status"] not in {"simulated_success", "simulated_scheduled"}:
                raise ValueError("No active simulated agent action was found for this claim.")
            original = json.loads(action_row["details_json"])
            prior_reversals = con.execute(
                "SELECT details_json FROM actions WHERE claim_id=? AND action_type='reversal'",
                (claim_id,),
            ).fetchall()
            if any(
                json.loads(row["details_json"]).get("reverses_action_id") == action_row["action_id"]
                for row in prior_reversals
            ):
                raise ValueError("This agent action has already been reversed.")

            reversal = {
                "action_id": uuid.uuid4().hex,
                "claim_id": claim_id,
                "action_type": "reversal",
                "reversed_action_type": action_type,
                "reverses_action_id": action_row["action_id"],
                "product_ref": original.get("product_ref"),
                "product_name": original.get("product_name"),
                "amount": -float(action_row["amount"] or 0),
                "currency": original.get("currency", "USD"),
                "status": "simulated_reversal",
                "external_reference": f"SIM-REV-{uuid.uuid4().hex[:12].upper()}",
                "reviewer": reviewer,
                "note": note,
            }
            con.execute(
                """INSERT INTO actions
                (action_id,claim_id,action_type,amount,status,details_json,created_at,idempotency_key)
                VALUES(?,?,?,?,?,?,?,?)""",
                (
                    reversal["action_id"],
                    claim_id,
                    "reversal",
                    reversal["amount"],
                    reversal["status"],
                    json.dumps(reversal, ensure_ascii=False),
                    now_utc(),
                    f"{claim_id}:reversal:{action_row['action_id']}",
                ),
            )
            original_for_claim = json.loads(claim["action_json"] or action_row["details_json"])
            original_for_claim.update(
                {"status": "reversed", "reversal_action_id": reversal["action_id"]}
            )
            now = now_utc()
            con.execute(
                """UPDATE claims SET status='denied',decision='human_denied_after_auto_resolution',
                reason=?,reviewer=?,reviewer_note=?,action_json=?,updated_at=? WHERE claim_id=?""",
                (
                    note,
                    reviewer,
                    note,
                    json.dumps(original_for_claim, ensure_ascii=False),
                    now,
                    claim_id,
                ),
            )
            con.execute(
                "INSERT INTO human_reviews(claim_id,reviewer,decision,note,created_at) VALUES(?,?,?,?,?)",
                (claim_id, reviewer, "deny", note, now),
            )
            self._event(
                con,
                claim_id,
                "action_reversed",
                reviewer,
                {
                    "action_id": action_row["action_id"],
                    "reversal_action_id": reversal["action_id"],
                    "action_type": action_type,
                    "amount": action_row["amount"],
                    "note": note,
                },
            )
            self._event(
                con,
                claim_id,
                "human_override",
                reviewer,
                {"decision": "deny", "note": note, "reversal_action_id": reversal["action_id"]},
            )
            return dict(con.execute("SELECT * FROM claims WHERE claim_id=?", (claim_id,)).fetchone())

    def record_review_decision(self, claim_id: str, reviewer: str, decision: str, note: str) -> None:
        """Persist the actual reviewer identity and decision from graph resume."""
        with self.connect() as con:
            con.execute(
                "INSERT INTO human_reviews(claim_id,reviewer,decision,note,created_at) VALUES(?,?,?,?,?)",
                (claim_id, reviewer, decision, note, now_utc()),
            )
            self._event(
                con,
                claim_id,
                "human_override",
                reviewer,
                {"decision": decision, "note": note},
            )
            if decision == "deny":
                con.execute(
                    """UPDATE claims SET reviewer=?,reviewer_note=?,decision='human_denied',reason=?,
                    status='denied',updated_at=? WHERE claim_id=?""",
                    (reviewer, note, note, now_utc(), claim_id),
                )

    @staticmethod
    def _event(
        con: sqlite3.Connection,
        claim_id: str,
        event_type: str,
        actor: str,
        details: dict,
    ) -> None:
        con.execute(
            "INSERT INTO events(claim_id,event_type,actor,details_json,created_at) VALUES(?,?,?,?,?)",
            (
                claim_id,
                event_type,
                actor,
                json.dumps(details, ensure_ascii=False, default=str),
                now_utc(),
            ),
        )

    def list_events(self, claim_id: str | None = None, limit: int | None = None) -> list[dict]:
        with self.connect() as con:
            if claim_id:
                query = "SELECT * FROM events WHERE claim_id=? ORDER BY id DESC"
                params: tuple = (claim_id,)
            else:
                query = "SELECT * FROM (SELECT * FROM events ORDER BY id DESC LIMIT 10000) ORDER BY id DESC"
                params = ()
            if limit is not None:
                query += " LIMIT ?"
                params += (max(1, min(int(limit), 1000)),)
            rows = con.execute(query, params)
            return [dict(r) for r in rows]
