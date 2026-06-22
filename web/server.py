#!/usr/bin/env python3
"""
Nano Agent Plus — Web UI (FastAPI + SSE Streaming)
"""

import json
import os
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

app = FastAPI(title="Nano Agent Plus")

# Session storage: session_id -> {"agent": Agent, "history": list}
sessions: dict[str, dict] = {}

# ── 使用次数统计 ──────────────────────────────────────

USAGE_FILE = Path(__file__).parent / "usage.json"
DAILY_LIMIT = int(os.getenv("DAILY_LIMIT_PER_USER", "20"))


def _today() -> str:
    from datetime import date
    return date.today().isoformat()


def load_usage() -> dict:
    """加载使用统计。结构: {"2026-06-22": {"abc123": 5, ...}}"""
    if USAGE_FILE.exists():
        try:
            return json.loads(USAGE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_usage(data: dict):
    """保存使用统计。"""
    USAGE_FILE.write_text(json.dumps(data, ensure_ascii=False))


def check_and_increment(session_id: str) -> tuple[bool, str]:
    """
    检查并增加会话使用次数。

    Returns:
        (allowed: bool, message: str)
    """
    if DAILY_LIMIT <= 0:
        return True, ""

    today = _today()
    usage = load_usage()

    if today not in usage:
        usage[today] = {}

    count = usage[today].get(session_id, 0)

    if count >= DAILY_LIMIT:
        return False, f"今日已达上限 ({DAILY_LIMIT} 次)，请明天再试"

    usage[today][session_id] = count + 1
    save_usage(usage)
    return True, ""

STATIC_DIR = Path(__file__).parent / "static"


def get_or_create_session(session_id: Optional[str] = None) -> str:
    """获取或创建会话。"""
    if session_id and session_id in sessions:
        return session_id
    new_id = session_id or uuid.uuid4().hex[:12]
    if new_id not in sessions:
        sessions[new_id] = {
            "agent": Agent(Config()),
            "history": [],
        }
    return new_id


# ── SSE 流式响应 ──────────────────────────────────────

def agent_stream(task: str, strategy: str, session_id: str):
    """Generator that yields SSE events as the agent runs."""
    agent = sessions[session_id]["agent"]

    # 用队列收集 agent 事件
    queue: Queue = Queue()

    def on_event(event_type: str, data: dict):
        queue.put({"event": event_type, "data": data})

    # 在后台线程运行 agent
    def run():
        try:
            agent.run(task, strategy=strategy, on_event=on_event)
        except Exception as e:
            queue.put({"event": "error", "data": {"text": str(e)}})

    thread = Thread(target=run)
    thread.start()

    # 流式发送事件
    while True:
        item = queue.get()
        if item is None:
            break
        event_type = item["event"]
        data = item["data"]
        yield f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
        if event_type in ("done", "error"):
            break

    thread.join()
    # 记录历史
    sessions[session_id]["history"].append({"role": "user", "content": task})
    if item and item["event"] == "done":
        sessions[session_id]["history"].append(
            {"role": "assistant", "content": item["data"]["text"]}
        )


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

    # 使用次数检查
    allowed, limit_msg = check_and_increment(session_id)
    if not allowed:
        return StreamingResponse(
            iter([f"event: error\ndata: {json.dumps({'text': limit_msg})}\n\n"]),
            media_type="text/event-stream",
        )

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
    if session_id not in sessions:
        return {"history": []}
    return {"history": sessions[session_id]["history"]}


@app.delete("/api/sessions/{session_id}")
async def clear_session(session_id: str):
    """Clear session memory."""
    if session_id in sessions:
        sessions[session_id]["agent"].clear_memory()
        sessions[session_id]["history"] = []
    return {"ok": True}


@app.get("/api/health")
async def health():
    today = _today()
    usage = load_usage().get(today, {})
    return {
        "status": "ok",
        "sessions": len(sessions),
        "today_usage": {k: f"{v}/{DAILY_LIMIT}" for k, v in usage.items()},
        "model": Config().model,
        "provider": Config().provider,
    }


# ── 静态文件 ──────────────────────────────────────────

@app.get("/")
async def index():
    return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))


# ── 启动 ──────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("WEB_PORT", "8080"))
    print(f"\n🤖 Nano Agent Plus Web UI")
    print(f"   地址: http://localhost:{port}")
    print(f"   模型: {Config().model}")
    print(f"   后端: {Config().provider}\n")

    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
