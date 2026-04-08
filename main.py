"""
Multi-Agent AI System — Production-ready for Google Cloud Run
Architecture:
  - Primary Coordinator Agent (LangGraph ReAct)
  - Agent A: The Librarian (SQLite CRUD tools)
  - Agent B: The Executor (MCP-compliant Calendar + Task Manager tools)
"""

import os
import json
import sqlite3
import logging
from datetime import datetime
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from langchain_core.tools import tool
from langchain_core.messages import HumanMessage
from langchain_google_genai import ChatGoogleGenerativeAI
import os
from langgraph.prebuilt import create_react_agent

# ─── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
log = logging.getLogger("multi_agent")

# ─── Config ─────────────────────────────────────────────────────────────────
# Keep the DB_PATH as is (Cloud Run uses /tmp for temporary storage)
DB_PATH = os.getenv("DB_PATH", "/tmp/librarian.db")

# Replace Anthropic with Google API Key
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")

# Replace the Model Name with Gemini 1.5 Flash (it's fast and perfect for hackathons)
MODEL_NAME = "gemini-1.5-flash"
# ═══════════════════════════════════════════════════════════════════════════
#  DATABASE BOOTSTRAP
# ═══════════════════════════════════════════════════════════════════════════

def init_db() -> None:
    """Create tables if they don't exist."""
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS notes (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            title     TEXT    NOT NULL,
            content   TEXT    NOT NULL,
            tags      TEXT    DEFAULT '',
            created   TEXT    DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS calendar_events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            title       TEXT NOT NULL,
            description TEXT DEFAULT '',
            date_time   TEXT NOT NULL,
            duration_m  INTEGER DEFAULT 60,
            created     TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS tasks (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            title       TEXT NOT NULL,
            description TEXT DEFAULT '',
            due_date    TEXT,
            status      TEXT DEFAULT 'pending',
            priority    TEXT DEFAULT 'medium',
            created     TEXT DEFAULT (datetime('now'))
        );
    """)
    con.commit()
    con.close()
    log.info("Database initialised at %s", DB_PATH)


# ═══════════════════════════════════════════════════════════════════════════
#  AGENT A — THE LIBRARIAN  (SQLite tools)
# ═══════════════════════════════════════════════════════════════════════════

@tool
def save_note(title: str, content: str, tags: str = "") -> str:
    """
    [LIBRARIAN] Persist a note to the local knowledge base.

    Args:
        title:   Short descriptive title for the note.
        content: Full text body of the note.
        tags:    Comma-separated keywords for retrieval (optional).

    Returns:
        Confirmation string with the new note ID.
    """
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute(
        "INSERT INTO notes (title, content, tags) VALUES (?, ?, ?)",
        (title, content, tags),
    )
    note_id = cur.lastrowid
    con.commit()
    con.close()
    log.info("Note saved: id=%s title=%s", note_id, title)
    return f"✅ Note saved (id={note_id}): '{title}'"


@tool
def search_notes(query: str) -> str:
    """
    [LIBRARIAN] Full-text search across all stored notes.

    Args:
        query: Keyword or phrase to search in title, content, or tags.

    Returns:
        JSON array of matching notes (id, title, content, tags, created).
    """
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    pattern = f"%{query}%"
    cur.execute(
        """SELECT id, title, content, tags, created
           FROM notes
           WHERE title LIKE ? OR content LIKE ? OR tags LIKE ?
           ORDER BY created DESC LIMIT 10""",
        (pattern, pattern, pattern),
    )
    rows = [dict(r) for r in cur.fetchall()]
    con.close()
    if not rows:
        return f"No notes found matching '{query}'."
    return json.dumps(rows, indent=2)


@tool
def list_notes(limit: int = 10) -> str:
    """
    [LIBRARIAN] Return the most recently created notes.

    Args:
        limit: Maximum number of notes to return (default 10).

    Returns:
        JSON array of notes ordered by creation date descending.
    """
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute(
        "SELECT id, title, content, tags, created FROM notes ORDER BY created DESC LIMIT ?",
        (limit,),
    )
    rows = [dict(r) for r in cur.fetchall()]
    con.close()
    if not rows:
        return "The knowledge base is empty."
    return json.dumps(rows, indent=2)


@tool
def delete_note(note_id: int) -> str:
    """
    [LIBRARIAN] Delete a note by its ID.

    Args:
        note_id: Integer primary key of the note to delete.

    Returns:
        Confirmation or error string.
    """
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("DELETE FROM notes WHERE id = ?", (note_id,))
    deleted = cur.rowcount
    con.commit()
    con.close()
    if deleted == 0:
        return f"⚠️ No note found with id={note_id}."
    return f"🗑️ Note id={note_id} deleted."


# ═══════════════════════════════════════════════════════════════════════════
#  AGENT B — THE EXECUTOR  (MCP-compliant Calendar + Task Manager tools)
#
#  These tools mirror the Model Context Protocol pattern:
#    • Each tool has a well-typed schema (Pydantic via @tool docstring)
#    • Side-effects are isolated and logged
#    • Return values are structured JSON strings for composability
# ═══════════════════════════════════════════════════════════════════════════

@tool
def schedule_event(
    title: str,
    date_time: str,
    description: str = "",
    duration_minutes: int = 60,
) -> str:
    """
    [EXECUTOR / MCP:calendar.create] Schedule a new calendar event.

    Args:
        title:            Name of the event.
        date_time:        ISO-8601 datetime string, e.g. '2025-07-15T14:00:00'.
        description:      Optional agenda or notes.
        duration_minutes: Length of the event in minutes (default 60).

    Returns:
        JSON object with the created event details.
    """
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute(
        "INSERT INTO calendar_events (title, description, date_time, duration_m) VALUES (?,?,?,?)",
        (title, description, date_time, duration_minutes),
    )
    event_id = cur.lastrowid
    con.commit()
    con.close()
    event = {
        "id": event_id,
        "title": title,
        "date_time": date_time,
        "duration_minutes": duration_minutes,
        "description": description,
        "status": "scheduled",
    }
    log.info("Event scheduled: %s", event)
    return json.dumps(event, indent=2)


@tool
def list_events(date_filter: str = "") -> str:
    """
    [EXECUTOR / MCP:calendar.list] List upcoming calendar events.

    Args:
        date_filter: Optional ISO date prefix to filter by (e.g. '2025-07').
                     Leave empty to list all events.

    Returns:
        JSON array of calendar events.
    """
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    if date_filter:
        cur.execute(
            "SELECT * FROM calendar_events WHERE date_time LIKE ? ORDER BY date_time ASC",
            (f"{date_filter}%",),
        )
    else:
        cur.execute("SELECT * FROM calendar_events ORDER BY date_time ASC")
    rows = [dict(r) for r in cur.fetchall()]
    con.close()
    if not rows:
        return "No calendar events found."
    return json.dumps(rows, indent=2)


@tool
def create_task(
    title: str,
    description: str = "",
    due_date: str = "",
    priority: str = "medium",
) -> str:
    """
    [EXECUTOR / MCP:tasks.create] Create a new task in the task manager.

    Args:
        title:       Short title for the task.
        description: Detailed description of what needs to be done.
        due_date:    ISO-8601 date string (e.g. '2025-07-20').
        priority:    'low', 'medium', or 'high' (default 'medium').

    Returns:
        JSON object with the created task details.
    """
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute(
        "INSERT INTO tasks (title, description, due_date, priority) VALUES (?,?,?,?)",
        (title, description, due_date, priority),
    )
    task_id = cur.lastrowid
    con.commit()
    con.close()
    task = {
        "id": task_id,
        "title": title,
        "description": description,
        "due_date": due_date,
        "priority": priority,
        "status": "pending",
    }
    log.info("Task created: %s", task)
    return json.dumps(task, indent=2)


@tool
def list_tasks(status_filter: str = "pending") -> str:
    """
    [EXECUTOR / MCP:tasks.list] List tasks from the task manager.

    Args:
        status_filter: Filter by status: 'pending', 'in_progress', 'done', or 'all'.

    Returns:
        JSON array of matching tasks.
    """
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    if status_filter == "all":
        cur.execute("SELECT * FROM tasks ORDER BY created DESC")
    else:
        cur.execute(
            "SELECT * FROM tasks WHERE status = ? ORDER BY created DESC",
            (status_filter,),
        )
    rows = [dict(r) for r in cur.fetchall()]
    con.close()
    if not rows:
        return f"No tasks with status='{status_filter}'."
    return json.dumps(rows, indent=2)


@tool
def update_task_status(task_id: int, status: str) -> str:
    """
    [EXECUTOR / MCP:tasks.update] Update the status of an existing task.

    Args:
        task_id: Integer primary key of the task.
        status:  New status: 'pending', 'in_progress', or 'done'.

    Returns:
        Confirmation string.
    """
    valid = {"pending", "in_progress", "done"}
    if status not in valid:
        return f"⚠️ Invalid status '{status}'. Must be one of: {valid}"
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("UPDATE tasks SET status = ? WHERE id = ?", (status, task_id))
    updated = cur.rowcount
    con.commit()
    con.close()
    if updated == 0:
        return f"⚠️ No task found with id={task_id}."
    return f"✅ Task id={task_id} → status='{status}'"


# ═══════════════════════════════════════════════════════════════════════════
#  PRIMARY COORDINATOR AGENT  (LangGraph ReAct)
# ═══════════════════════════════════════════════════════════════════════════

ALL_TOOLS = [
    # Librarian tools
    save_note,
    search_notes,
    list_notes,
    delete_note,
    # Executor / MCP tools
    schedule_event,
    list_events,
    create_task,
    list_tasks,
    update_task_status,
]

SYSTEM_PROMPT = """You are a Primary Coordinator Agent managing two specialised sub-agents:

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AGENT A — THE LIBRARIAN
Tools: save_note, search_notes, list_notes, delete_note
Role: Manages a structured knowledge base (SQLite). Use these tools to store or retrieve
      notes, project ideas, research, and any persistent information the user wants saved.

AGENT B — THE EXECUTOR  (MCP-compliant)
Tools: schedule_event, list_events, create_task, list_tasks, update_task_status
Role: Interfaces with Calendar and Task Manager services via MCP-style tool calls.
      Use these tools to schedule meetings, create action items, and manage deadlines.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

COORDINATION RULES:
1. For multi-step requests, reason through the full plan BEFORE calling tools.
2. Always use the LIBRARIAN first when the request involves retrieving stored information.
3. Chain results naturally: e.g. search a note → extract key info → schedule an event about it.
4. Be concise and structured in your final response.
5. When scheduling events, default to ISO-8601 datetime. If the user provides a vague time
   like "tomorrow at 3pm", infer a sensible ISO string based on today's date.
6. Confirm all actions taken in a clear summary at the end.

Today's date/time: {now}
"""


def build_agent():
    if not GOOGLE_API_KEY:
        raise RuntimeError("GOOGLE_API_KEY environment variable is not set.")
    
    llm = ChatGoogleGenerativeAI(
        model=MODEL_NAME,
        google_api_key=GOOGLE_API_KEY,
        temperature=0,
    )
    
    # Create the agent using the same tools and prompt
    agent = create_react_agent(
        model=llm,
        tools=ALL_TOOLS,
        state_modifier=SYSTEM_PROMPT.format(now=datetime.utcnow().isoformat()),
    )
    return agent


# ═══════════════════════════════════════════════════════════════════════════
#  FASTAPI APPLICATION
# ═══════════════════════════════════════════════════════════════════════════

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle."""
    log.info("Starting up — initialising database and agent…")
    init_db()
    app.state.agent = build_agent()
    log.info("Agent ready ✓")
    yield
    log.info("Shutting down.")


app = FastAPI(
    title="Multi-Agent AI System",
    description=(
        "Primary Coordinator Agent backed by The Librarian (SQLite) "
        "and The Executor (MCP-style Calendar + Tasks)."
    ),
    version="1.0.0",
    lifespan=lifespan,
)


# ─── Request / Response schemas ─────────────────────────────────────────────

class ProcessRequest(BaseModel):
    query: str

    model_config = {
        "json_schema_extra": {
            "example": {
                "query": "Check my notes for the AI project idea, then schedule a meeting about it for next Monday at 2pm and create a follow-up task."
            }
        }
    }


class ProcessResponse(BaseModel):
    status: str
    query: str
    response: str
    steps: list[dict[str, Any]]
    duration_ms: float


# ─── Endpoints ───────────────────────────────────────────────────────────────
@app.get("/")
async def root():
    return {"status": "ok", "message": "Multi-Agent AI System is running"}

@app.get("/health")
async def health():
    """Liveness probe for Cloud Run."""
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


@app.post("/process", response_model=ProcessResponse)
async def process(req: ProcessRequest):
    """
    Primary entry point. Submit a natural-language query; the coordinator
    agent plans and executes across all sub-agents.
    """
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="Query must not be empty.")

    log.info("Processing query: %s", req.query)
    start = datetime.utcnow()

    try:
        result = app.state.agent.invoke(
            {"messages": [HumanMessage(content=req.query)]}
        )
    except Exception as exc:
        log.exception("Agent error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    elapsed = (datetime.utcnow() - start).total_seconds() * 1000

    # Extract steps (tool calls + results) for transparency
    steps: list[dict] = []
    final_text = ""
    for msg in result.get("messages", []):
        role = getattr(msg, "type", type(msg).__name__)
        if hasattr(msg, "tool_calls") and msg.tool_calls:
            for tc in msg.tool_calls:
                steps.append({"type": "tool_call", "tool": tc["name"], "args": tc["args"]})
        elif role == "tool":
            steps.append({"type": "tool_result", "content": msg.content[:500]})
        elif role == "ai":
            final_text = msg.content

    log.info("Query completed in %.0f ms — %d steps", elapsed, len(steps))
    return ProcessResponse(
        status="success",
        query=req.query,
        response=final_text,
        steps=steps,
        duration_ms=round(elapsed, 2),
    )


@app.get("/notes")
async def get_notes(limit: int = 20):
    """Quick inspection endpoint — list stored notes."""
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute("SELECT * FROM notes ORDER BY created DESC LIMIT ?", (limit,))
    rows = [dict(r) for r in cur.fetchall()]
    con.close()
    return {"notes": rows, "count": len(rows)}


@app.get("/events")
async def get_events():
    """Quick inspection endpoint — list calendar events."""
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute("SELECT * FROM calendar_events ORDER BY date_time ASC")
    rows = [dict(r) for r in cur.fetchall()]
    con.close()
    return {"events": rows, "count": len(rows)}


@app.get("/tasks")
async def get_tasks(status: str = "all"):
    """Quick inspection endpoint — list tasks."""
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    if status == "all":
        cur.execute("SELECT * FROM tasks ORDER BY created DESC")
    else:
        cur.execute("SELECT * FROM tasks WHERE status = ? ORDER BY created DESC", (status,))
    rows = [dict(r) for r in cur.fetchall()]
    con.close()
    return {"tasks": rows, "count": len(rows)}


# ═══════════════════════════════════════════════════════════════════════════
#  ENTRY POINT  — reads PORT env var for Cloud Run
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, log_level="info")
