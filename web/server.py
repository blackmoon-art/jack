#!/usr/bin/env python3
"""
Sleeping fox — Web UI (FastAPI + SSE Streaming)
"""

import json
import logging
import os
import sqlite3
import sys
import uuid
from pathlib import Path
from queue import Empty, Queue
from threading import Thread
from typing import Optional

# Ensure project root in path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from nano_agent import Agent, Config

logger = logging.getLogger("nano_agent.web")

app = FastAPI(title="Sleeping fox")

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
import time as _time

# Session storage: session_id -> {"agent": Agent, "history": list, "last_access": float}
# 线程安全：所有读写都经过 _sessions_lock
_sessions_lock = threading.Lock()
sessions: dict[str, dict] = {}

# Session 淘汰配置
_MAX_SESSIONS = 100          # 最大会话数
_SESSION_TTL_SECONDS = 7200  # 2 小时未访问则可淘汰

# ── 使用次数限制 ──────────────────────────────────────

USAGE_FILE = Path(__file__).parent / "usage.json"
DAILY_LIMIT = int(os.getenv("DAILY_LIMIT_PER_USER", "0"))  # 0 = 不限


def _today() -> str:
    from datetime import date
    return date.today().isoformat()


def load_usage() -> dict:
    if USAGE_FILE.exists():
        try:
            return json.loads(USAGE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_usage(data: dict):
    USAGE_FILE.write_text(json.dumps(data, ensure_ascii=False))


def check_daily_limit(session_id: str) -> str:
    """检查每日使用次数。返回空字符串表示通过，否则返回错误信息。"""
    if DAILY_LIMIT <= 0:
        return ""
    today = _today()
    usage = load_usage()
    if today not in usage:
        usage[today] = {}
    count = usage[today].get(session_id, 0)
    if count >= DAILY_LIMIT:
        return f"今日已达上限 ({DAILY_LIMIT} 次)，请明天再试"
    usage[today][session_id] = count + 1
    save_usage(usage)
    return ""


STATIC_DIR = Path(__file__).parent / "static"


def _evict_sessions():
    """淘汰过期或超量的 session（需在 _sessions_lock 内调用）。"""
    now = _time.time()
    # 1. 淘汰超时的 session
    expired = [
        sid for sid, s in sessions.items()
        if now - s.get("last_access", 0) > _SESSION_TTL_SECONDS
    ]
    for sid in expired:
        del sessions[sid]
        logger.info(f"Evicted expired session {sid}")
    # 2. 如果还超量，按 LRU 淘汰
    if len(sessions) > _MAX_SESSIONS:
        sorted_sessions = sorted(
            sessions.items(), key=lambda x: x[1].get("last_access", 0)
        )
        for sid, _ in sorted_sessions[:len(sessions) - _MAX_SESSIONS]:
            del sessions[sid]
            logger.info(f"Evicted LRU session {sid}")


def get_or_create_session(session_id: Optional[str] = None) -> str:
    """获取或创建会话。新 session 或内存中不存在时从 DB 恢复历史。线程安全。"""
    with _sessions_lock:
        if session_id and session_id in sessions:
            sessions[session_id]["last_access"] = _time.time()
            return session_id
        # 淘汰后再创建
        _evict_sessions()
        new_id = session_id or uuid.uuid4().hex[:12]
        if new_id not in sessions:
            history = db_load_history(new_id)
            config = Config()
            # 会话级 work_dir 隔离：每个用户在 workspace 下有自己的子目录
            import os as _os
            session_dir = _os.path.join(config.work_dir, f"session_{new_id}")
            _os.makedirs(session_dir, exist_ok=True)
            config.work_dir = session_dir
            sessions[new_id] = {
                "agent": Agent(config),
                "history": history,
                "last_access": _time.time(),
            }
            if history:
                logger.info(f"Restored session {new_id}: {len(history)} messages from DB")
        return new_id


# ── SSE 流式响应 ──────────────────────────────────────

def agent_stream(task: str, strategy: str, session_id: str):
    """Generator that yields SSE events as the agent runs."""
    # 在锁内安全获取 agent 引用
    with _sessions_lock:
        if session_id not in sessions:
            yield f"event: error\ndata: {json.dumps({'text': 'Session not found'})}\n\n"
            return
        agent = sessions[session_id]["agent"]
        sessions[session_id]["last_access"] = _time.time()

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

    # 流式发送事件（带心跳，防止浏览器超时断开）
    last_heartbeat = _time.time()
    while True:
        try:
            item = queue.get(timeout=3)  # 3 秒超时，没事件就发心跳
            last_item = item
            last_heartbeat = _time.time()
            if item is None:
                break
            event_type = item["event"]
            data = item["data"]
            yield f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
            if event_type in ("done", "error"):
                break
        except Empty:  # queue.Empty → 超时了，LLM 还在想
            if _time.time() - last_heartbeat > 2.5:
                yield ": heartbeat\n\n"  # SSE 注释，浏览器忽略但保持连接
                last_heartbeat = _time.time()

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

    # 访问控制：填了正确访问码 → 管理员不限次；不填/填错 → 普通用户有限次
    owner_code = os.getenv("WEB_ACCESS_CODE", "")
    is_owner = False
    if owner_code:
        user_code = body.get("code", "")
        if user_code == owner_code:
            is_owner = True  # 管理员，不限次
        # 不填或填错 → 普通用户，有限次（不拒绝）

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
        if session_id in sessions:
            return {"history": sessions[session_id]["history"]}
    # 不在内存中，从 DB 加载
    history = db_load_history(session_id)
    return {"history": history}


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
    html = html.replace("{{V}}", str(int(time.time())))
    return HTMLResponse(html, headers={"Cache-Control": "no-cache, no-store", "ngrok-skip-browser-warning": "1"})


# 静态资源（KaTeX、字体等本地文件）
app.mount("/static", StaticFiles(directory=str(STATIC_DIR), check_dir=False), name="static")


# ── 静态资源 ──────────────────────────────────────────

@app.get("/fox.png")
async def fox_icon():
    p = STATIC_DIR / "fox.png"
    if not p.exists():
        return Response(status_code=404)
    return FileResponse(p, media_type="image/png")

CHARTS_DIR = STATIC_DIR / "charts"
CHARTS_DIR.mkdir(exist_ok=True)


@app.get("/charts/{filename}")
async def serve_chart(filename: str):
    from fastapi.responses import FileResponse
    # 防止路径遍历：resolve 后检查是否在 CHARTS_DIR 内
    filepath = (CHARTS_DIR / filename).resolve()
    try:
        filepath.relative_to(CHARTS_DIR.resolve())
    except ValueError:
        return {"error": "Access denied"}
    if not filepath.exists():
        return {"error": "Chart not found"}
    # 根据扩展名自动检测 MIME 类型
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "png"
    mime_map = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                "gif": "image/gif", "webp": "image/webp", "svg": "image/svg+xml"}
    media_type = mime_map.get(ext, "image/png")
    return FileResponse(filepath, media_type=media_type, headers={"Cache-Control": "public, max-age=86400"})


# ── 文件下载 ──────────────────────────────────────────

@app.get("/api/download/{filename}")
async def download_file(filename: str, session_id: str = ""):
    """从会话工作目录下载文件到用户本地。"""
    import glob as _g

    # 如果没有指定 session，搜索所有活跃会话
    search_dirs = []
    if session_id:
        with _sessions_lock:
            if session_id in sessions:
                search_dirs.append(sessions[session_id]["agent"].config.work_dir)
    else:
        with _sessions_lock:
            for sid, s in sessions.items():
                search_dirs.append(s["agent"].config.work_dir)

    # 找到第一个匹配的文件
    for d in search_dirs:
        filepath = (Path(d) / filename).resolve()
        try:
            filepath.relative_to(Path(d).resolve())
        except ValueError:
            continue
        if filepath.exists():
            return FileResponse(
                filepath,
                filename=filename,
                headers={"Content-Disposition": f"attachment; filename=\"{filename}\""},
            )

    return JSONResponse({"error": "File not found"}, status_code=404)


# ── 启动 ──────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("WEB_PORT", "8080"))
    logger.info(f"Sleeping fox Web UI — http://localhost:{port}")
    logger.info(f"Model: {Config().model} | Provider: {Config().provider}")

    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
