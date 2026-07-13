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
from threading import Semaphore, Thread
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
    """获取线程本地 SQLite 连接（复用，避免频繁 open/close）。

    设置 busy_timeout=5000ms 防止并发写入时 SQLITE_BUSY 导致数据丢失。
    WAL 模式下写入者不阻塞读取者，但并发写入需要超时重试。
    """
    conn = getattr(_db_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=5.0)
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
        conn = _get_db()
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

# Agent 并发上限 — 防止内存爆 (每 Agent ~8MB 线程栈 + LLM 响应)
_MAX_CONCURRENT_AGENTS = int(os.getenv("MAX_CONCURRENT_AGENTS", "10"))
_agent_slots = Semaphore(_MAX_CONCURRENT_AGENTS)

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


def get_client_ip(request: Request) -> str:
    """从请求中提取客户端真实 IP。

    优先检查反向代理头（X-Forwarded-For, X-Real-IP），
    兜底使用直连 IP。
    """
    # X-Forwarded-For: "client, proxy1, proxy2" → 取第一个
    forwarded = request.headers.get("X-Forwarded-For", "").strip()
    if forwarded:
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("X-Real-IP", "").strip()
    if real_ip:
        return real_ip
    # 兜底：直连 IP（可能为 None，如测试环境）
    return getattr(request.client, "host", "unknown") if request.client else "unknown"


def check_daily_limit(ip: str) -> str:
    """检查每日使用次数，按 IP 限制。返回空字符串表示通过，否则返回错误信息。"""
    if DAILY_LIMIT <= 0:
        return ""
    today = _today()
    usage = load_usage()
    if today not in usage:
        usage[today] = {}
    count = usage[today].get(ip, 0)
    if count >= DAILY_LIMIT:
        return f"今日已达上限 ({DAILY_LIMIT} 次)，请明天再试"
    usage[today][ip] = count + 1
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
        try:
            _cleanup_session_files(sid, sessions[sid]["agent"].config.work_dir)
        except Exception as e:
            logger.warning(f"Failed to clean up expired session {sid}: {e}")
        del sessions[sid]
        logger.info(f"Evicted expired session {sid}")
    # 2. 如果还超量，按 LRU 淘汰
    if len(sessions) > _MAX_SESSIONS:
        sorted_sessions = sorted(
            sessions.items(), key=lambda x: x[1].get("last_access", 0)
        )
        for sid, _ in sorted_sessions[:len(sessions) - _MAX_SESSIONS]:
            try:
                _cleanup_session_files(sid, sessions[sid]["agent"].config.work_dir)
            except Exception as e:
                logger.warning(f"Failed to clean up LRU session {sid}: {e}")
            del sessions[sid]
            logger.info(f"Evicted LRU session {sid}")


_sessions_building: set[str] = set()  # 正在构建中的 session ID，防止 TOCTOU 重复构建


def get_or_create_session(session_id: Optional[str] = None) -> str:
    """获取或创建会话。新 session 或内存中不存在时从 DB 恢复历史。线程安全。

    用 _sessions_building 集合防止 TOCTOU 竞态：两个并发请求携带
    相同 session_id 时，第一个进入构建，第二个等待锁后复用已构建的实例。
    """
    # Fast path: session 已存在，锁内更新时间戳即可
    with _sessions_lock:
        if session_id and session_id in sessions:
            sessions[session_id]["last_access"] = _time.time()
            return session_id

    # 确定新 session ID
    new_id = session_id or uuid.uuid4().hex[:12]

    # 防 TOCTOU：记录"正在构建"，避免并发重复 agent 构建
    with _sessions_lock:
        if new_id in sessions:
            sessions[new_id]["last_access"] = _time.time()
            return new_id
        if new_id in _sessions_building:
            # 另一个线程正在构建同一个 session，等待它完成
            pass  # 循环重试
        _sessions_building.add(new_id)
        _evict_sessions()

    # ── 锁外构建 Agent（耗时操作）──
    try:
        history = db_load_history(new_id)
        base_config = get_config()
        import os as _os
        session_dir = _os.path.join(base_config.work_dir, f"session_{new_id}")
        _os.makedirs(session_dir, exist_ok=True)
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
            "cancel_event": threading.Event(),  # 用户可发送新消息打断当前任务
        }
        if history:
            logger.info(f"Restored session {new_id}: {len(history)} messages from DB")
    finally:
        with _sessions_lock:
            _sessions_building.discard(new_id)
    return new_id


# ── SSE 流式响应 ──────────────────────────────────────

def agent_stream(task: str, strategy: str, session_id: str,
                 model_override: str | None = None):
    """Generator that yields SSE events as the agent runs."""
    # 在锁内安全获取 agent 引用和 cancel_event（避免锁外访问 sessions dict 的 race condition）
    with _sessions_lock:
        if session_id not in sessions:
            yield f"event: error\ndata: {json.dumps({'text': 'Session not found'})}\n\n"
            return
        agent = sessions[session_id]["agent"]
        sessions[session_id]["last_access"] = _time.time()
        cancel_event = sessions[session_id].get("cancel_event")  # 锁内获取引用

    # 用队列收集 agent 事件
    queue: Queue = Queue()

    def on_event(event_type: str, data: dict):
        queue.put({"event": event_type, "data": data})

    # ── 并发控制：拿不到槽位就拒绝，防止线程数爆炸 ──
    if not _agent_slots.acquire(timeout=60):
        yield f"event: error\ndata: {json.dumps({'text': 'Server busy. Please try again later.'})}\n\n"
        return

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
        finally:
            _agent_slots.release()

    thread = Thread(target=run, daemon=True)
    thread.start()

    try:
        # 流式发送事件（带心跳，防止浏览器超时断开）
        # cancel_event 已在锁内获取，此处直接使用
        last_heartbeat = _time.time()
        while True:
            # 用户发送新消息打断 → 取消事件被设置
            if cancel_event and cancel_event.is_set():
                cancelled["value"] = True
                yield f"event: done\ndata: {json.dumps({'text': '⏹ 任务已被用户打断'}, ensure_ascii=False)}\n\n"
                break
            try:
                item = queue.get(timeout=1)
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

    # 打断正在执行的任务（用户发新消息 = 取消旧任务）
    with _sessions_lock:
        if session_id in sessions:
            old_event = sessions[session_id].get("cancel_event")
            if old_event and not old_event.is_set():
                old_event.set()
                logger.info(f"Cancelled running task in session {session_id}")

    # 每日限流：owner 藉免，外部用户按 IP 限制
    owner_code = os.getenv("WEB_ACCESS_CODE", "")
    import hmac as _hmac
    is_owner = bool(owner_code and _hmac.compare_digest(
        body.get("code", ""), owner_code))
    if not is_owner:
        client_ip = get_client_ip(request)
        limit_msg = check_daily_limit(client_ip)
        if limit_msg:
            return {"error": limit_msg}

    with _sessions_lock:
        if session_id in sessions:
            sessions[session_id]["cancel_event"] = threading.Event()

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


@app.post("/api/survey")
async def submit_survey(request: Request):
    """Store user survey feedback."""
    import json as _json
    from pathlib import Path as _Path
    try:
        body = await request.json()
    except Exception:
        return {"ok": False, "error": "Invalid JSON"}
    rating = body.get("rating", 0)
    comment = body.get("comment", "")
    model = body.get("model", "")

    survey_file = _Path(__file__).parent / "survey_results.jsonl"
    record = {
        "timestamp": datetime.now().isoformat(),
        "rating": rating,
        "comment": comment,
        "model": model,
        "ip": request.client.host if request.client else "",
    }
    try:
        with open(survey_file, "a") as f:
            f.write(_json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning(f"Failed to write survey: {e}")
        return {"ok": False, "error": str(e)}
    return {"ok": True}


@app.get("/api/survey/stats")
async def survey_stats():
    """Get survey statistics."""
    import json as _json
    from pathlib import Path as _Path
    survey_file = _Path(__file__).parent / "survey_results.jsonl"
    if not survey_file.exists():
        return {"total": 0, "avg_rating": 0, "ratings": []}
    records = []
    with open(survey_file) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(_json.loads(line))
                except Exception:
                    pass
    total = len(records)
    avg = sum(r.get("rating", 0) for r in records) / max(total, 1)
    return {"total": total, "avg_rating": round(avg, 2),
            "recent": records[-20:]}


@app.get("/api/stats")
async def stats():
    """返回在线人数统计。"""
    return {"sessions": len(sessions)}


@app.get("/api/health")
async def health():
    # 每小时触发一次 chart 清理
    if _time.time() - _last_chart_cleanup > 3600:
        cleanup_temp_files()
    return {
        "status": "ok",
        "sessions": len(sessions),
        "model": get_config().model,
        "provider": get_config().provider,
    }


@app.get("/changelog")
async def changelog():
    """Serve the project changelog."""
    from fastapi.responses import PlainTextResponse
    changelog_path = Path(__file__).parent.parent / "CHANGELOG.md"
    if changelog_path.exists():
        return PlainTextResponse(
            changelog_path.read_text(encoding="utf-8"),
            media_type="text/plain; charset=utf-8",
        )
    return {"error": "CHANGELOG.md not found"}


# ── Digital Circuit API ─────────────────────────────

@app.post("/api/digital-circuit")
async def digital_circuit(request: Request):
    """Run the full digital circuit pipeline: NL→Verilog→Sim→Synth→SVG."""
    body = await request.json()
    description = body.get("description", "").strip()
    if not description:
        return {"error": "description is required"}

    from nano_agent.tools.digital import DigitalCircuit
    from nano_agent.strategies.meta import MetaStrategy
    from pathlib import Path

    charts_dir = str(STATIC_DIR / "charts")
    dc = DigitalCircuit(charts_dir=charts_dir)

    try:
        result = dc.design_digital(description)
    except ValueError as e:
        return {"error": str(e), "available": "half_adder, full_adder, mux_2to1, dff, counter_4bit"}
    except Exception as e:
        return {"error": str(e)}

    # Extract SVG URL
    import re
    svg_match = re.search(r'/charts/(logic_\S+\.svg)', result)
    svg_url = svg_match.group(0) if svg_match else ""

    # Run Meta evaluation
    try:
        verdict = MetaStrategy._extract_circuit_verdict(result)
        tech_score = verdict.get("technical_score", 0)
        layout_score = verdict.get("layout_score", 10)
        layout_summary = verdict.get("layout_summary", "")
        assertions_passed = verdict.get("assertions_passed", 0)
        assertions_failed = verdict.get("assertions_failed", 0)
        gate_count = verdict.get("gate_count", 0)
        wire_crossings = verdict.get("wire_crossings", 0)
        wire_overlaps = verdict.get("wire_overlaps", 0)
    except Exception:
        tech_score = 0
        layout_score = 10
        layout_summary = ""
        assertions_passed = assertions_failed = gate_count = 0
        wire_crossings = wire_overlaps = 0

    return {
        "result": result,
        "svg_url": svg_url,
        "technical_score": tech_score,
        "layout_score": layout_score,
        "layout_summary": layout_summary,
        "gate_count": gate_count,
        "assertions_passed": assertions_passed,
        "assertions_failed": assertions_failed,
        "wire_crossings": wire_crossings,
        "wire_overlaps": wire_overlaps,
    }


@app.get("/digital")
async def digital_page():
    """Digital circuit playground page."""
    return FileResponse(str(STATIC_DIR / "digital.html"))


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
_last_chart_cleanup = _time.time()


_last_chart_cleanup = _time.time()
_cleanup_thread_started = False


def cleanup_temp_files():
    """清理用户临时生成的文件：charts、session 残留、iverilog/yosys tmp。"""
    # 1. 清理旧 charts（保留最新 _MAX_CHART_FILES 个）
    try:
        patterns = ("*.png", "*.pptx", "*.jpg", "*.jpeg", "*.svg", "*.csv")
        all_files = []
        for pat in patterns:
            all_files.extend(CHARTS_DIR.glob(pat))
        files = sorted(all_files, key=lambda f: f.stat().st_mtime, reverse=True)
        for f in files[_MAX_CHART_FILES:]:
            f.unlink()
            logger.info(f"Cleaned up old chart: {f.name}")
    except Exception as e:
        logger.warning(f"Chart cleanup failed: {e}")

    # 2. 清理过期的 session 工作目录（服务器重启后内存 session 丢失）
    try:
        work_root = Path(Config().work_dir)
        for session_dir in work_root.glob("session_*"):
            if not session_dir.is_dir():
                continue
            # 检查是否有运行的 session（理论上重启后不会有）
            sid = session_dir.name.replace("session_", "")
            if sid not in sessions:
                try:
                    import shutil as _shutil2
                    _shutil2.rmtree(session_dir)
                    logger.info(f"Cleaned up stale session dir: {session_dir.name}")
                except Exception:
                    pass
    except Exception as e:
        logger.warning(f"Session dir cleanup failed: {e}")

    # 3. 清理 iverilog / yosys 残留临时目录
    try:
        for tmp_root in (Path("/tmp"), CHARTS_DIR):
            if not tmp_root.exists():
                continue
            for tmpdir in tmp_root.glob("iverilog_*"):
                try:
                    import shutil as _shutil3
                    _shutil3.rmtree(tmpdir)
                except Exception:
                    pass
            for tmpdir in tmp_root.glob("yosys_*"):
                try:
                    import shutil as _shutil4
                    _shutil4.rmtree(tmpdir)
                except Exception:
                    pass
    except Exception as e:
        logger.warning(f"Temp dir cleanup failed: {e}")

    global _last_chart_cleanup
    _last_chart_cleanup = _time.time()


def _start_cleanup_thread():
    """后台线程定期清理临时文件（每 30 分钟）。"""
    global _cleanup_thread_started
    if _cleanup_thread_started:
        return
    _cleanup_thread_started = True

    def _loop():
        while True:
            _time.sleep(1800)  # 30 min
            try:
                cleanup_temp_files()
            except Exception:
                pass

    t = Thread(target=_loop, daemon=True)
    t.start()


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
                "gif": "image/gif", "webp": "image/webp", "svg": "image/svg+xml",
                "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                "pdf": "application/pdf",
                "mp3": "audio/mpeg", "wav": "audio/wav",
                "html": "text/html; charset=utf-8", "csv": "text/csv",
                "json": "application/json", "txt": "text/plain; charset=utf-8",
                "md": "text/markdown",
                "py": "text/x-python", "js": "text/javascript", "ts": "text/typescript",
                "sh": "text/x-shellscript", "css": "text/css",
                "xml": "text/xml", "yml": "text/yaml", "yaml": "text/yaml",
                "sql": "text/x-sql", "java": "text/x-java",
                "c": "text/x-c", "cpp": "text/x-c++",
                "go": "text/x-go", "rs": "text/x-rust"}
    media_type = mime_map.get(ext, "application/octet-stream")
    # 非图片文件强制下载（HTML 除外，直接在浏览器打开）
    from urllib.parse import quote
    headers = {"Cache-Control": "public, max-age=86400"}
    if ext not in ("png", "jpg", "jpeg", "gif", "webp", "svg"):
        if ext == "html":
            pass  # HTML 直接在浏览器渲染
        else:
            encoded = quote(filename, safe=".")
            headers["Content-Disposition"] = (
                f'attachment; filename="{encoded}"; '
                f"filename*=UTF-8''{quote(filename, safe='')}"
            )
    return FileResponse(filepath, media_type=media_type, headers=headers)


# ── 文件上传 ──────────────────────────────────────────

@app.post("/api/upload")
async def upload_file(file: UploadFile = FastAPIFile(...), session_id: str = Form("")):
    """上传文件到会话工作目录，agent 可通过 read 工具读取。"""
    if not file.filename:
        return JSONResponse({"error": "No file selected"}, status_code=400)

    # 大小限制：防止磁盘被撑爆
    max_size = int(os.getenv("MAX_UPLOAD_SIZE", str(50 * 1024 * 1024)))  # 默认 50MB
    content_length = file.size or 0
    if content_length > max_size:
        return JSONResponse({"error": f"File too large ({content_length/1024/1024:.1f}MB, max {max_size/1024/1024:.0f}MB)"}, status_code=413)

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

    session_id 可选：指定则只搜该 session；不指定则搜所有 session。
    """
    import shutil as _shutil
    filepath = None

    with _sessions_lock:
        if session_id:
            if session_id not in sessions:
                return JSONResponse({"error": "Session not found"}, status_code=404)
            work_dir = Path(sessions[session_id]["agent"].config.work_dir)
            fp = (work_dir / filename).resolve()
            try:
                fp.relative_to(work_dir.resolve())
                if fp.exists():
                    filepath = fp
            except ValueError:
                pass
        else:
            # 搜索所有 session
            for sid, s in sessions.items():
                work_dir = Path(s["agent"].config.work_dir)
                fp = (work_dir / filename).resolve()
                try:
                    fp.relative_to(work_dir.resolve())
                    if fp.exists():
                        filepath = fp
                        break
                except ValueError:
                    continue

    # 也搜 charts 目录（PPT、图表等工具生成的静态文件）
    if filepath is None:
        charts_dir = STATIC_DIR / "charts"
        fp = (charts_dir / filename).resolve()
        try:
            fp.relative_to(charts_dir.resolve())
            if fp.exists():
                filepath = fp
        except ValueError:
            pass

    # 最后：直接搜文件系统（服务器重启后 session 内存丢失，但文件还在磁盘上）
    if filepath is None:
        work_root = Path(Config().work_dir)
        for session_dir in work_root.glob("session_*"):
            fp = (session_dir / filename).resolve()
            try:
                fp.relative_to(work_root.resolve())
                if fp.exists():
                    filepath = fp
                    break
            except ValueError:
                continue

    if filepath is None:
        return JSONResponse({"error": "File not found"}, status_code=404)

    from urllib.parse import quote
    encoded_filename = quote(filename, safe="")
    return FileResponse(
        str(filepath),
        media_type="application/octet-stream",
        headers={"Content-Disposition":
                 f"attachment; filename=\"{encoded_filename}\"; "
                 f"filename*=UTF-8''{quote(filename, safe='')}"},
    )


# ── 启动 ──────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("WEB_PORT", "8080"))
    logger.info(f"Sleeping fox Web UI — http://localhost:{port}")
    logger.info(f"Model: {get_config().model} | Provider: {get_config().provider}")

    cleanup_temp_files()
    _start_cleanup_thread()
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
