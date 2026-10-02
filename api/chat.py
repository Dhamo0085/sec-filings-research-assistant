"""
Chat session management — SQLite storage + conversation memory.

Endpoints:
  POST   /chat                         — send a message (creates session if needed)
  GET    /chat/sessions                — list all sessions
  GET    /chat/sessions/{sid}          — get all turns in a session
  DELETE /chat/sessions/{sid}          — delete a session + its turns
  PATCH  /chat/sessions/{sid}/turns/{tid} — mark a turn correct/incorrect
  GET    /chat/review                  — all turns for quality review (filterable)
"""

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from loguru import logger
from pydantic import BaseModel

from api.auth import ADMIN_DEPENDENCY
from config import settings
from query import ask

# ── DB setup ──────────────────────────────────────────────────────────────────

DB_PATH = settings.data_dir / "chat_history.db"
settings.data_dir.mkdir(parents=True, exist_ok=True)


@contextmanager
def _conn():
    con = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    con.row_factory = sqlite3.Row
    # WAL needs shared-memory-mapped -wal/-shm files with specific locking
    # semantics that some network/bind-mounted volumes (observed on a hosted volume)
    # don't support, raising "disk I/O error" — which, unguarded, crashed
    # this at IMPORT time and took the entire API down with it (every
    # route, not just chat), since api/app.py imports this module directly.
    # Fall back to SQLite's plain default (rollback-journal) mode, which
    # needs no auxiliary files, instead of letting that exception propagate.
    try:
        con.execute("PRAGMA journal_mode=WAL")
    except sqlite3.OperationalError as exc:
        logger.warning(f"WAL mode unavailable on this filesystem ({exc}) — using default journal mode")
    con.execute("PRAGMA foreign_keys=ON")
    try:
        yield con
        con.commit()
    finally:
        con.close()


def _init_db() -> None:
    with _conn() as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id         TEXT PRIMARY KEY,
                title      TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS turns (
                id          TEXT PRIMARY KEY,
                session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                question    TEXT NOT NULL,
                answer      TEXT NOT NULL,
                query_type  TEXT NOT NULL DEFAULT 'single_doc',
                citations   TEXT NOT NULL DEFAULT '[]',
                is_correct  INTEGER,
                created_at  TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id);
        """)
        # P3-09 added the 6.5 status fields. The chat DB is runtime data
        # (D16, untracked), so an additive migration is cheaper and safer
        # than a rebuild — an existing history keeps working and old turns
        # simply report the pre-Phase-3 default.
        existing = {row[1] for row in con.execute("PRAGMA table_info(turns)")}
        for column, default in (
            ("status", "'answered_text'"),
            ("abstain_reason", "NULL"),
            ("error_code", "NULL"),
            ("as_of", "NULL"),
            ("definition_note", "NULL"),
        ):
            if column not in existing:
                con.execute(
                    f"ALTER TABLE turns ADD COLUMN {column} TEXT DEFAULT {default}"
                )


try:
    _init_db()
except Exception:
    # A broken chat-history DB (this filesystem issue, a corrupted file,
    # etc.) must not prevent the whole API from starting — /query and the
    # rest of the RAG pipeline don't depend on chat history at all. Chat
    # endpoints will fail individually if _conn() still can't connect, but
    # that's a contained failure instead of the entire process never
    # coming up.
    logger.exception("Chat history DB init failed — /chat endpoints may be unavailable, rest of the API is unaffected")


# ── Pipeline helper ───────────────────────────────────────────────────────────

def _run_pipeline(question: str, tickers=None, years=None, as_of=None, history=""):
    """Run the pipeline. Returns the P3-01 Outcome.

    P3-03 retired routing/classifier.py: the filter chips are now an override
    applied *after* routing (query.ask), so the intent, metric and focus are
    still read from the question rather than guessed again by a second,
    differently-prompted model call.
    """
    return ask(question, as_of=as_of, tickers=tickers, years=years,
               history=history)


# ── Schemas ───────────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    session_id: Optional[str] = None
    question:   str
    tickers:    Optional[List[str]] = None
    years:      Optional[List[int]] = None
    # P3-08/P3-09: point-in-time answers from the chat UI too (D2, G2).
    as_of:      Optional[str] = None


class TurnOut(BaseModel):
    id:          str
    session_id:  str
    question:    str
    answer:      str
    query_type:  str
    citations:   list
    is_correct:  Optional[bool] = None
    created_at:  str
    # Spec 6.5, so the chat UI can show the same badges and notes as /query.
    status:          str = "answered_text"
    abstain_reason:  Optional[str] = None
    error_code:      Optional[str] = None
    as_of:           Optional[str] = None
    definition_note: Optional[str] = None


class SessionOut(BaseModel):
    id:          str
    title:       str
    created_at:  str
    updated_at:  str
    turn_count:  int = 0


class ReviewPatch(BaseModel):
    is_correct: Optional[bool] = None


# ── Router ────────────────────────────────────────────────────────────────────

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("", response_model=TurnOut)
def chat(req: ChatRequest):
    """
    Send a message; creates a new session when session_id is omitted.

    History (session validation, prior-turn context, persisting the turn)
    is best-effort: a flaky disk under the sqlite file (observed on
    a hosted volume — sqlite3.OperationalError: disk I/O error on an otherwise
    healthy volume) must not stop the actual question from being answered.
    Every history touchpoint below degrades independently instead of
    raising, so the answer generation path always runs regardless of
    history's health.
    """
    if not req.question.strip():
        raise HTTPException(400, "question cannot be empty")

    now = datetime.now(timezone.utc).isoformat()
    sid = req.session_id
    history_ok = True

    if sid:
        try:
            with _conn() as con:
                if not con.execute("SELECT 1 FROM sessions WHERE id=?", (sid,)).fetchone():
                    raise HTTPException(404, f"Session {sid!r} not found")
        except sqlite3.Error as exc:
            logger.warning(f"Chat history unavailable (session lookup): {exc}")
            history_ok = False
    else:
        sid = str(uuid.uuid4())
        title = req.question[:60] + ("…" if len(req.question) > 60 else "")
        try:
            with _conn() as con:
                con.execute("INSERT INTO sessions VALUES (?,?,?,?)", (sid, title, now, now))
        except sqlite3.Error as exc:
            logger.warning(f"Chat history unavailable (session create): {exc}")
            history_ok = False

    # Fetch last 3 turns as memory context — best-effort, empty on failure
    prior = []
    if history_ok:
        try:
            with _conn() as con:
                prior = con.execute(
                    "SELECT question, answer FROM turns WHERE session_id=? ORDER BY created_at DESC LIMIT 3",
                    (sid,),
                ).fetchall()
        except sqlite3.Error as exc:
            logger.warning(f"Chat history unavailable (prior turns): {exc}")

    # Prior turns are passed SEPARATELY, never prepended to the question.
    # v1 prepended them so its LLM classifier could resolve "compare to last
    # year"; with a rules-first router (P3-03) that corrupts the parse — a
    # previous answer naming "fiscal 2024 (year ended 2024-09-28)" injects
    # years into the period parse and turns a figure request into a trend.
    history = ""
    if prior:
        history = "\n---\n".join(
            f"Q: {r['question']}\nA: {r['answer'][:400]}" for r in reversed(prior)
        )

    try:
        outcome = _run_pipeline(req.question, req.tickers, req.years,
                                req.as_of, history)
    except Exception as exc:
        logger.exception("Chat pipeline error")
        raise HTTPException(500, "Something went wrong while answering your question. Please try again.") from exc

    body = outcome.to_ui_response()
    if body["status"] == "error":
        # Same rule as /query: a dependency failure is a 503 with its code,
        # never a 200 carrying an apology (D7, G4).
        raise HTTPException(503, {"error_code": body["error_code"],
                                  "message": body["answer"]})

    tid = str(uuid.uuid4())
    try:
        with _conn() as con:
            con.execute(
                "INSERT INTO turns (id, session_id, question, answer, "
                "query_type, citations, is_correct, created_at, status, "
                "abstain_reason, error_code, as_of, definition_note) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (tid, sid, req.question, body["answer"], body["query_type"],
                 json.dumps(body["citations"]), None, now, body["status"],
                 body["abstain_reason"], body["error_code"], body["as_of"],
                 body["definition_note"]),
            )
            con.execute("UPDATE sessions SET updated_at=? WHERE id=?", (now, sid))
    except sqlite3.Error as exc:
        logger.warning(f"Chat history unavailable (saving turn): {exc}")

    return TurnOut(
        id=tid, session_id=sid,
        question=req.question, answer=body["answer"],
        query_type=body["query_type"], citations=body["citations"],
        is_correct=None, created_at=now,
        status=body["status"], abstain_reason=body["abstain_reason"],
        error_code=body["error_code"], as_of=body["as_of"],
        definition_note=body["definition_note"],
    )


@router.get("/sessions", response_model=List[SessionOut])
def list_sessions():
    with _conn() as con:
        rows = con.execute("""
            SELECT s.id, s.title, s.created_at, s.updated_at,
                   COUNT(t.id) AS turn_count
            FROM   sessions s
            LEFT JOIN turns t ON t.session_id = s.id
            GROUP BY s.id
            ORDER BY s.updated_at DESC
        """).fetchall()
    return [SessionOut(**dict(r)) for r in rows]


@router.get("/sessions/{sid}", response_model=List[TurnOut])
def get_session(sid: str):
    with _conn() as con:
        if not con.execute("SELECT 1 FROM sessions WHERE id=?", (sid,)).fetchone():
            raise HTTPException(404, "Session not found")
        rows = con.execute(
            "SELECT * FROM turns WHERE session_id=? ORDER BY created_at", (sid,)
        ).fetchall()

    def _to_out(r):
        d = dict(r)
        d["citations"]  = json.loads(d["citations"])
        d["is_correct"] = bool(d["is_correct"]) if d["is_correct"] is not None else None
        return TurnOut(**d)

    return [_to_out(r) for r in rows]


# Destructive and previously unauthenticated (spec issue N5): anyone who
# could reach the API could delete any session. Admin-guarded until P5-04
# gives each session its own secret.
@router.delete("/sessions/{sid}", status_code=204, dependencies=[ADMIN_DEPENDENCY])
def delete_session(sid: str):
    with _conn() as con:
        con.execute("DELETE FROM turns   WHERE session_id=?", (sid,))
        con.execute("DELETE FROM sessions WHERE id=?",        (sid,))


# Mutates stored review labels; same reasoning as DELETE above (N5).
@router.patch("/sessions/{sid}/turns/{tid}", response_model=TurnOut,
              dependencies=[ADMIN_DEPENDENCY])
def review_turn(sid: str, tid: str, body: ReviewPatch):
    """Mark a turn as correct (true), incorrect (false), or unreviewed (null)."""
    val = None if body.is_correct is None else int(body.is_correct)
    with _conn() as con:
        if not con.execute("SELECT 1 FROM turns WHERE id=? AND session_id=?", (tid, sid)).fetchone():
            raise HTTPException(404, "Turn not found")
        con.execute("UPDATE turns SET is_correct=? WHERE id=?", (val, tid))
        row = con.execute("SELECT * FROM turns WHERE id=?", (tid,)).fetchone()
    d = dict(row)
    d["citations"]  = json.loads(d["citations"])
    d["is_correct"] = bool(d["is_correct"]) if d["is_correct"] is not None else None
    return TurnOut(**d)


@router.get("/review")
def review_all(filter: Optional[str] = Query(None)):
    """
    Return all turns for review.
    ?filter=correct | incorrect | unreviewed   (omit for all)
    """
    where = {
        "correct":    "WHERE t.is_correct = 1",
        "incorrect":  "WHERE t.is_correct = 0",
        "unreviewed": "WHERE t.is_correct IS NULL",
    }.get(filter or "", "")

    with _conn() as con:
        rows = con.execute(f"""
            SELECT t.*, s.title AS session_title
            FROM   turns t
            JOIN   sessions s ON s.id = t.session_id
            {where}
            ORDER BY t.created_at DESC
        """).fetchall()

    result = []
    for r in rows:
        d = dict(r)
        d["citations"]  = json.loads(d["citations"])
        d["is_correct"] = bool(d["is_correct"]) if d["is_correct"] is not None else None
        result.append(d)
    return result
