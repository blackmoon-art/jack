#!/usr/bin/env python3
"""
Sleeping fox — Web UI (FastAPI + SSE Streaming)
"""

import atexit
import json
import logging
import os
import sqlite3
import sys
import threading
import time as _time
import uuid
from pathlib import Path
from queue import Empty, Queue
from threading import Thread
from typing import Optional

# Ensure project root in path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI, Request, UploadFile, File as FastAPIFile, Form
from fastapi.responses import HTMLResponse, StreamingResponse, FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from nano_agent import Agent, Config
from nano_agent.config import get_config

logger = logging.getLogger("nano_agent.web")

app = FastAPI(title="Sleeping fox")

# ── SQLite 持久化 ────────────────────────────────────

DB_PATH = Path(__file__).parent / "sessions.db"


_db_local = threading.local()
_db_all_conns: list[sqlite3.Connection] = []
_db_lock = threading.Lock()


def _get_db() -> sqlite3.Connection:
    """获取线程本地 SQLite 连接（复用，避免频繁 open/close）。"""
    conn = getattr(_db_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        _db_local.conn = conn
        with _db_lock:
            _db_all_conns.append(conn)
    return conn


@atexit.register
def _close_all_db():
    """进程退出时关闭所有线程本地连接，避免资源泄漏。"""
    with _db_lock:
        for c in _db_all_conns:
            try:
                c.close()
            except Exception:
                pass
        _db_all_conns.clear()


def _init_db():
    """初始化数据库表，启用 WAL 模式提升并发性能。

    注意：不关闭连接——_get_db() 返回线程本地复用连接，
    关闭后 _db_local.conn 仍指向已关闭的 conn，下次调用会报错。
    连接由 _close_all_db() 在进程退出时统一清理。
    """
    conn = _get_db()
    conn.execute("PRAGMA journal_mode=WAL")
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
    conn.commit()


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
        conn = _get_db()
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
        conn = _get_db()
        conn.execute(
            "DELETE FROM session_history WHERE session_id = ?",
            (session_id,),
        )
        conn.commit()
    except Exception as e:
        logger.warning(f"Failed to clear session: {e}")


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


def _cleanup_session_files(session_id: str, work_dir: str):
    """清理 session 的磁盘数据：工作目录 + DB 历史。
    在 _sessions_lock 内调用（调用者已持锁）。

    work_dir 必须由调用方传入，避免重新构建 Config 读到默认值导致清错目录。
    """
    import shutil
    # 1. 删除 session 工作目录
    session_dir = work_dir
    if os.path.isdir(session_dir):
        try:
            shutil.rmtree(session_dir)
            logger.info(f"Cleaned session dir: {session_dir}")
        except Exception as e:
            logger.warning(f"Failed to clean session dir {session_dir}: {e}")
    # 2. 删除 DB 历史
    try:
        db_clear_session(session_id)
    except Exception as e:
        logger.warning(f"Failed to clean session DB history {session_id}: {e}")


def _evict_sessions():
    """淘汰过期或超量的 session（需在 _sessions_lock 内调用）。"""
    now = _time.time()
    # 1. 淘汰超时的 session
    expired = [
        sid for sid, s in sessions.items()
        if now - s.get("last_access", 0) > _SESSION_TTL_SECONDS
    ]
    for sid in expired:
        _cleanup_session_files(sid, sessions[sid]["agent"].config.work_dir)
        del sessions[sid]
        logger.info(f"Evicted expired session {sid}")
    # 2. 如果还超量，按 LRU 淘汰
    if len(sessions) > _MAX_SESSIONS:
        sorted_sessions = sorted(
            sessions.items(), key=lambda x: x[1].get("last_access", 0)
        )
        for sid, _ in sorted_sessions[:len(sessions) - _MAX_SESSIONS]:
            _cleanup_session_files(sid, sessions[sid]["agent"].config.work_dir)
            del sessions[sid]
            logger.info(f"Evicted LRU session {sid}")


def get_or_create_session(session_id: Optional[str] = None) -> str:
    """获取或创建会话。新 session 或内存中不存在时从 DB 恢复历史。线程安全。

    Agent 构建在锁外完成（涉及 DB 初始化、工具注册，耗时不可控），
    锁内只做 dict 读写，避免阻塞并发请求。
    """
    # Fast path: session 已存在，锁内更新时间戳即可
    with _sessions_lock:
        if session_id and session_id in sessions:
            sessions[session_id]["last_access"] = _time.time()
            return session_id

    # 确定新 session ID
    new_id = session_id or uuid.uuid4().hex[:12]

    # Check-then-create: 可能其他线程已经创建了同一个 session
    with _sessions_lock:
        if new_id in sessions:
            sessions[new_id]["last_access"] = _time.time()
            return new_id
        _evict_sessions()

    # ── 锁外构建 Agent（耗时操作）──
    history = db_load_history(new_id)
    base_config = get_config()
    import os as _os
    session_dir = _os.path.join(base_config.work_dir, f"session_{new_id}")
    _os.makedirs(session_dir, exist_ok=True)
    # 用 with_overrides 创建隔离配置，不修改原始 Config
    session_config = base_config.with_overrides(work_dir=session_dir)
    agent = Agent(session_config)

    # ── 锁内写入 dict ──
    with _sessions_lock:
        # Double-check: 可能其他线程已经创建了
        if new_id in sessions:
            sessions[new_id]["last_access"] = _time.time()
            return new_id
        sessions[new_id] = {
            "agent": agent,
            "history": history,
            "last_access": _time.time(),
        }
        if history:
            logger.info(f"Restored session {new_id}: {len(history)} messages from DB")
    return new_id


# ── SSE 流式响应 ──────────────────────────────────────

def agent_stream(task: str, strategy: str, session_id: str,
                 model_override: str | None = None):
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
    cancelled = {"value": False}  # mutable flag for thread

    def run():
        nonlocal last_item
        try:
            agent.run(task, strategy=strategy, on_event=on_event,
                      model_override=model_override)
        except Exception as e:
            if not cancelled["value"]:
                logger.exception(f"Agent run failed: {e}")
                last_item = {"event": "error", "data": {"text": str(e)}}
                queue.put(last_item)

    thread = Thread(target=run, daemon=True)
    thread.start()

    try:
        # 流式发送事件（带心跳，防止浏览器超时断开）
        last_heartbeat = _time.time()
        while True:
            try:
                item = queue.get(timeout=3)
                last_item = item
                last_heartbeat = _time.time()
                if item is None:
                    break
                event_type = item["event"]
                data = item["data"]
                yield f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
                if event_type in ("done", "error"):
                    break
            except Empty:
                if _time.time() - last_heartbeat > 2.5:
                    yield ": heartbeat\n\n"
                    last_heartbeat = _time.time()
    except GeneratorExit:
        # 客户端断开连接
        cancelled["value"] = True
        logger.info(f"Client disconnected from session {session_id}")
    finally:
        # 确保线程结束，避免泄露
        thread.join(timeout=5)
        if thread.is_alive():
            logger.warning(f"Agent thread still alive for session {session_id} after disconnect")

        # 记录历史（内存 + SQLite）— 仅在有结果时
        if last_item and last_item.get("event") == "done":
            reply = last_item["data"].get("text", "")
            with _sessions_lock:
                if session_id in sessions:
                    sessions[session_id]["history"].append({"role": "user", "content": task})
                    sessions[session_id]["history"].append({"role": "assistant", "content": reply})
            db_save_message(session_id, "user", task)
            db_save_message(session_id, "assistant", reply)


# ── API 路由 ──────────────────────────────────────────

@app.post("/api/chat")
async def chat(request: Request):
    """SSE streaming chat endpoint."""
    body = await request.json()
    task = body.get("message", "").strip()
    strategy = body.get("strategy", "default")
    session_id = body.get("session_id", "")

    if not task:
        return {"error": "Empty message"}

    session_id = get_or_create_session(session_id)

    # 每日限流：owner 藉免
    owner_code = os.getenv("WEB_ACCESS_CODE", "")
    is_owner = bool(owner_code and body.get("code", "") == owner_code)
    if not is_owner:
        limit_msg = check_daily_limit(session_id)
        if limit_msg:
            return {"error": limit_msg}

    # 请求级模型覆盖（不修改 session 共享状态，线程安全）
    model = body.get("model", "")

    return StreamingResponse(
        agent_stream(task, strategy, session_id, model_override=model or None),
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
    """Clear session memory, history, and working directory."""
    import shutil

    with _sessions_lock:
        if session_id in sessions:
            sessions[session_id]["agent"].clear_memory()
            sessions[session_id]["history"] = []
            work_dir = Path(sessions[session_id]["agent"].config.work_dir)
        else:
            work_dir = None

    # 清理磁盘：session 工作目录 + DB 历史
    if work_dir and work_dir.is_dir():
        try:
            shutil.rmtree(work_dir)
            logger.info(f"Cleaned session dir: {work_dir}")
        except Exception as e:
            logger.warning(f"Failed to clean session dir {work_dir}: {e}")
    db_clear_session(session_id)
    return {"ok": True}


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "sessions": len(sessions),
        "model": get_config().model,
        "provider": get_config().provider,
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
_MAX_CHART_FILES = 200  # 最多保留的图片数量


def cleanup_old_charts():
    """启动时清理旧图表文件，保留最新的 N 个。"""
    try:
        files = sorted(CHARTS_DIR.glob("*"), key=lambda f: f.stat().st_mtime, reverse=True)
        for f in files[_MAX_CHART_FILES:]:
            f.unlink()
            logger.info(f"Cleaned up old chart: {f.name}")
    except Exception as e:
        logger.warning(f"Chart cleanup failed: {e}")


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


# ── 文件上传 ──────────────────────────────────────────

@app.post("/api/upload")
async def upload_file(file: UploadFile = FastAPIFile(...), session_id: str = Form("")):
    """上传文件到会话工作目录，agent 可通过 read 工具读取。"""
    if not file.filename:
        return JSONResponse({"error": "No file selected"}, status_code=400)

    # 确定目标目录（get_or_create_session 内部加锁，不可嵌套持有 _sessions_lock，否则死锁）
    session_id = get_or_create_session(session_id)
    with _sessions_lock:
        work_dir = Path(sessions[session_id]["agent"].config.work_dir)

    # 安全：只取文件名，防路径穿越
    safe_name = Path(file.filename).name
    filepath = work_dir / safe_name

    try:
        content = await file.read()
        filepath.write_bytes(content)
        return {
            "ok": True,
            "filename": safe_name,
            "path": str(filepath.relative_to(work_dir)),
            "size": len(content),
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── 文件下载 ──────────────────────────────────────────

@app.get("/api/download/{filename}")
async def download_file(filename: str, session_id: str = ""):
    """从会话工作目录下载文件到用户本地。

    安全：session_id 必传，不允许跨 session 访问其他用户文件。
    """
    if not session_id:
        return JSONResponse({"error": "session_id is required"}, status_code=400)

    with _sessions_lock:
        if session_id not in sessions:
            return JSONResponse({"error": "Session not found"}, status_code=404)
        work_dir = Path(sessions[session_id]["agent"].config.work_dir)

    filepath = (work_dir / filename).resolve()
    try:
        filepath.relative_to(work_dir.resolve())
    except ValueError:
        return JSONResponse({"error": "Access denied"}, status_code=403)

    if not filepath.exists():
        return JSONResponse({"error": "File not found"}, status_code=404)

    return FileResponse(
        filepath,
        filename=filename,
        headers={"Content-Disposition": f"attachment; filename=\"{filename}\""},
    )


# ── 启动 ──────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("WEB_PORT", "8080"))
    logger.info(f"Sleeping fox Web UI — http://localhost:{port}")
    logger.info(f"Model: {get_config().model} | Provider: {get_config().provider}")

    cleanup_old_charts()
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
