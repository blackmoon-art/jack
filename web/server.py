#!/usr/bin/env python3
"""
Lazy Cat — Web UI (FastAPI + SSE Streaming)
"""

import json
import logging
import os
import sqlite3
import sys
import uuid
from pathlib import Path
from queue import Queue
from threading import Thread
from typing import Optional

# Ensure project root in path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from nano_agent import Agent, Config

logger = logging.getLogger("nano_agent.web")

app = FastAPI(title="Lazy Cat")

# ── SQLite 持久化 ────────────────────────────────────

DB_PATH = Path(__file__).parent / "sessions.db"


def _get_db() -> sqlite3.Connection:
    """获取 SQLite 连接（每次调用创建新连接，线程安全）。"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _init_db():
    """初始化数据库表。"""
    with _get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS session_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_session_id
            ON session_history(session_id)
        """)


_init_db()


def db_save_message(session_id: str, role: str, content: str):
    """持久化一条消息到 SQLite。"""
    try:
        with _get_db() as conn:
            conn.execute(
                "INSERT INTO session_history (session_id, role, content) VALUES (?, ?, ?)",
                (session_id, role, content),
            )
            conn.commit()
    except Exception as e:
        logger.warning(f"Failed to save session message: {e}")


def db_load_history(session_id: str) -> list[dict]:
    """从 SQLite 加载会话历史。"""
    try:
        with _get_db() as conn:
            rows = conn.execute(
                "SELECT role, content FROM session_history WHERE session_id = ? ORDER BY id",
                (session_id,),
            ).fetchall()
            return [{"role": r["role"], "content": r["content"]} for r in rows]
    except Exception as e:
        logger.warning(f"Failed to load session history: {e}")
        return []


def db_clear_session(session_id: str):
    """清除会话历史。"""
    try:
        with _get_db() as conn:
            conn.execute(
                "DELETE FROM session_history WHERE session_id = ?",
                (session_id,),
            )
            conn.commit()
    except Exception as e:
        logger.warning(f"Failed to clear session: {e}")


import threading

# Session storage: session_id -> {"agent": Agent, "history": list}
# 线程安全：所有读写都经过 _sessions_lock
_sessions_lock = threading.Lock()
sessions: dict[str, dict] = {}

STATIC_DIR = Path(__file__).parent / "static"


def get_or_create_session(session_id: Optional[str] = None) -> str:
    """获取或创建会话。新 session 或内存中不存在时从 DB 恢复历史。线程安全。"""
    with _sessions_lock:
        if session_id and session_id in sessions:
            return session_id
        new_id = session_id or uuid.uuid4().hex[:12]
        if new_id not in sessions:
            history = db_load_history(new_id)
            sessions[new_id] = {
                "agent": Agent(Config()),
                "history": history,
            }
            if history:
                logger.info(f"Restored session {new_id}: {len(history)} messages from DB")
        return new_id


# ── SSE 流式响应 ──────────────────────────────────────

def agent_stream(task: str, strategy: str, session_id: str):
    """Generator that yields SSE events as the agent runs.

    Args:
        show_thinking: 是否发送思考过程事件 (orient/tool_call/tool_result/text)
    """
    agent = sessions[session_id]["agent"]

    # 用队列收集 agent 事件
    queue: Queue = Queue()

    def on_event(event_type: str, data: dict):
        queue.put({"event": event_type, "data": data})

    # 在后台线程运行 agent
    last_item = None

    def run():
        nonlocal last_item
        try:
            agent.run(task, strategy=strategy, on_event=on_event)
        except Exception as e:
            logger.exception(f"Agent run failed: {e}")
            last_item = {"event": "error", "data": {"text": str(e)}}
            queue.put(last_item)

    thread = Thread(target=run)
    thread.start()

    # 流式发送事件
    while True:
        item = queue.get()
        last_item = item
        if item is None:
            break
        event_type = item["event"]
        data = item["data"]
        yield f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
        if event_type in ("done", "error"):
            break

    thread.join()
    # 记录历史（内存 + SQLite）
    sessions[session_id]["history"].append({"role": "user", "content": task})
    db_save_message(session_id, "user", task)
    if last_item and last_item.get("event") == "done":
        reply = last_item["data"]["text"]
        sessions[session_id]["history"].append({"role": "assistant", "content": reply})
        db_save_message(session_id, "assistant", reply)


# ── API 路由 ──────────────────────────────────────────

@app.post("/api/chat")
async def chat(request: Request):
    """SSE streaming chat endpoint."""
    body = await request.json()
    task = body.get("message", "").strip()
    strategy = body.get("strategy", "default")
    session_id = body.get("session_id", "")

    # 访问控制：如果设置了 WEB_ACCESS_CODE，需要验证
    access_code = os.getenv("WEB_ACCESS_CODE", "")
    if access_code and body.get("code") != access_code:
        return StreamingResponse(
            iter([f"event: error\ndata: {json.dumps({'text': '访问码错误'})}\n\n"]),
            media_type="text/event-stream",
        )

    if not task:
        return {"error": "Empty message"}

    session_id = get_or_create_session(session_id)
    agent = sessions[session_id]["agent"]

    # 模型切换
    model = body.get("model", "")
    if model:
        agent.llm.set_model(model)

    return StreamingResponse(
        agent_stream(task, strategy, session_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Session-Id": session_id,
        },
    )


@app.get("/api/sessions/{session_id}/history")
async def get_history(session_id: str):
    """Get chat history for a session."""
    with _sessions_lock:
        if session_id not in sessions:
            history = db_load_history(session_id)
            return {"history": history}
        return {"history": sessions[session_id]["history"]}


@app.delete("/api/sessions/{session_id}")
async def clear_session(session_id: str):
    """Clear session memory."""
    with _sessions_lock:
        if session_id in sessions:
            sessions[session_id]["agent"].clear_memory()
            sessions[session_id]["history"] = []
    db_clear_session(session_id)
    return {"ok": True}


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "sessions": len(sessions),
        "model": Config().model,
        "provider": Config().provider,
    }


# ── 静态文件 ──────────────────────────────────────────

@app.get("/")
async def index():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    import time
    html = html.replace("{{TS}}", str(int(time.time())))
    return HTMLResponse(html, headers={"Cache-Control": "no-cache, no-store"})


# ── 图表文件服务 ──────────────────────────────────────────

CHARTS_DIR = STATIC_DIR / "charts"
CHARTS_DIR.mkdir(exist_ok=True)


@app.get("/charts/{filename}")
async def serve_chart(filename: str):
    from fastapi.responses import FileResponse
    filepath = CHARTS_DIR / filename
    if not filepath.exists():
        return {"error": "Chart not found"}
    return FileResponse(filepath, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})


# ── 启动 ──────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("WEB_PORT", "8080"))
    logger.info(f"Lazy Cat Web UI — http://localhost:{port}")
    logger.info(f"Model: {Config().model} | Provider: {Config().provider}")

    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
