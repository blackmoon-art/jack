"""
记忆模块 — 四层记忆的统一门面。

四层结构:
  1. Working Memory  (窗口):    最近 k 轮对话，内存淘汰
  2. Persistent Memory (文件):   任务历史，markdown 文件，自动轮转
  3. Reflection Memory (文件):   反思教训，跨任务复用
  4. Long-term Memory (SQLite):  全文检索 (FTS5)，跨会话语义匹配

Memory 门面统一管理四层，对外暴露简洁接口。
策略和 Agent 不需要知道记忆存储细节。
"""

import logging
import os
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("nano_agent.memory")

# ── 文件轮转锁：防止多线程并发写导致数据丢失 ──
_rotate_locks: dict[str, threading.Lock] = {}
_rotate_locks_guard = threading.Lock()
_ROTATE_LOCKS_WARN = 1024  # 超过此值记录警告（正常使用不会达到）


def _get_rotate_lock(file_path: str) -> threading.Lock:
    """获取文件对应的轮转锁（惰性创建，线程安全）。

    不做逐出：逐出可能造成持有旧锁的线程与新线程拿到不同的锁对象，
    导致两个线程同时写同一文件。64 个 Lock 对象仅 ~4KB，无需回收。
    """
    with _rotate_locks_guard:
        lock = _rotate_locks.get(file_path)
        if lock is not None:
            return lock
        if len(_rotate_locks) >= _ROTATE_LOCKS_WARN:
            logger.warning(
                f"Rotate lock count ({len(_rotate_locks)}) exceeded "
                f"{_ROTATE_LOCKS_WARN} — possible leak or unusual workload"
            )
        lock = threading.Lock()
        _rotate_locks[file_path] = lock
        return lock


class LongTermMemory:
    """
    长期记忆 — SQLite FTS5 全文检索。

    存储所有任务的历史记录，支持语义检索。
    零依赖（sqlite3 是 Python stdlib），FTS5 是 SQLite 内置全文搜索引擎。

    连接管理：使用 threading.local() 实现线程局部连接，
    避免每次操作开关连接，也避免多线程共享连接的线程安全问题。
    """

    def __init__(self, db_path: str = "long_term_memory.db"):
        self.db_path = db_path
        self._local = threading.local()
        self._init_db()

    @property
    def _conn(self) -> sqlite3.Connection:
        """获取当前线程的 SQLite 连接（懒创建）。"""
        conn = getattr(self._local, 'conn', None)
        if conn is None:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.row_factory = None
            self._local.conn = conn
        return conn

    def _init_db(self):
        """初始化 FTS5 全文搜索表。"""
        try:
            self._conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS memories USING fts5(
                    task,
                    result,
                    content,
                    created_at UNINDEXED,
                    tokenize='unicode61'
                )
            """)
            self._conn.commit()
        except Exception as e:
            logger.warning(f"Failed to init long-term memory DB: {e}")
            self.db_path = None  # 禁用

    def add(self, task: str, result: str):
        """添加一条记忆。"""
        if not self.db_path:
            return
        try:
            content = f"Task: {task}\nResult: {result}"
            self._conn.execute(
                "INSERT INTO memories (task, result, content, created_at) VALUES (?, ?, ?, ?)",
                (task, result[:2000], content, datetime.now().isoformat())
            )
            self._conn.commit()
        except Exception as e:
            logger.warning(f"Failed to add long-term memory: {e}")

    def search(self, query: str, top_k: int = 3) -> list[dict]:
        """全文检索相关记忆。返回最相关的 top_k 条。

        中文查询使用二字滑窗分词（CJK bigram），英文用整词。
        """
        if not self.db_path:
            return []
        try:
            import re as _re
            # 英文整词 + CJK 二字滑窗
            ascii_words = _re.findall(r'[a-zA-Z][a-zA-Z0-9]+', query)
            cjk_chars = _re.findall(r'[\u4e00-\u9fff]', query)
            cjk_bigrams = [cjk_chars[i] + cjk_chars[i+1]
                           for i in range(len(cjk_chars) - 1)]
            tokens = ascii_words + cjk_bigrams
            # 用 OR 连接，转义双引号
            safe_query = " OR ".join(
                w.replace('"', '""') for w in tokens if len(w) > 1
            )[:200]
            if not safe_query:
                return []
            rows = self._conn.execute(
                """SELECT task, result, created_at, bm25(memories) as score
                   FROM memories
                   WHERE memories MATCH ?
                   ORDER BY score
                   LIMIT ?""",
                (safe_query, top_k)
            ).fetchall()
            return [
                {"task": r[0], "result": r[1], "created_at": r[2], "score": r[3]}
                for r in rows
            ]
        except Exception as e:
            logger.warning(f"Long-term memory search failed: {e}")
            return []

    def count(self) -> int:
        """返回记忆总数。"""
        if not self.db_path:
            return 0
        try:
            return self._conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        except Exception:
            return 0

    def clear(self):
        """清空所有长期记忆。"""
        if not self.db_path:
            return
        try:
            self._conn.execute("DELETE FROM memories")
            self._conn.commit()
        except Exception as e:
            logger.warning(f"Failed to clear long-term memory: {e}")


class ReflexionTrace:
    """Reflexion 轨迹持久化 — SQLite 存储。

    存储每次 Reflexion 任务的完整轨迹：
      - task: 原始任务
      - attempts: 每次尝试的 steps / result / evaluation / reflection
      - lessons: 提取的精准教训

    用途:
      - 跨任务复用教训（替代纯文本 reflection 文件）
      - 调试：回溯某次任务的完整推理过程
      - 统计：分析策略成功率、常见失败模式
    """

    def __init__(self, db_path: str = "reflexion_trace.db"):
        self.db_path = db_path
        self._local = threading.local()
        self._init_db()

    @property
    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, 'conn', None)
        if conn is None:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return conn

    def _init_db(self):
        try:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS trace (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    final_status TEXT DEFAULT '',
                    final_score INTEGER DEFAULT 0,
                    best_result TEXT DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS attempt (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trace_id INTEGER NOT NULL,
                    attempt_num INTEGER NOT NULL,
                    result TEXT DEFAULT '',
                    eval_status TEXT DEFAULT '',
                    eval_score INTEGER DEFAULT 0,
                    eval_reason TEXT DEFAULT '',
                    reflection TEXT DEFAULT '',
                    lesson TEXT DEFAULT '',
                    FOREIGN KEY (trace_id) REFERENCES trace(id)
                );
                CREATE TABLE IF NOT EXISTS lesson (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_trace_id INTEGER,
                    lesson TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    use_count INTEGER DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_attempt_trace ON attempt(trace_id);
                CREATE INDEX IF NOT EXISTS idx_lesson_created ON lesson(created_at);
            """)
            self._conn.commit()
        except Exception as e:
            logger.warning(f"Failed to init reflexion trace DB: {e}")
            self.db_path = None

    def start_trace(self, task: str) -> int:
        """开始一条新的轨迹，返回 trace_id。"""
        if not self.db_path:
            return 0
        try:
            cur = self._conn.execute(
                "INSERT INTO trace (task, created_at) VALUES (?, ?)",
                (task, datetime.now().isoformat())
            )
            self._conn.commit()
            return cur.lastrowid
        except Exception as e:
            logger.warning(f"Failed to start trace: {e}")
            return 0

    def save_attempt(self, trace_id: int, attempt_num: int,
                     result: str, eval_result: dict,
                     reflection: str = "", lesson: str = ""):
        """保存一次尝试的结果。"""
        if not self.db_path or not trace_id:
            return
        try:
            self._conn.execute(
                """INSERT INTO attempt
                   (trace_id, attempt_num, result, eval_status, eval_score,
                    eval_reason, reflection, lesson)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (trace_id, attempt_num, result[:3000],
                 eval_result.get("status", ""), eval_result.get("score", 0),
                 eval_result.get("reason", ""), reflection[:1000], lesson[:500])
            )
            # 更新 trace 的最终状态
            self._conn.execute(
                """UPDATE trace SET final_status=?, final_score=?, best_result=?
                   WHERE id=? AND (final_score IS NULL OR final_score < ?)""",
                (eval_result.get("status", ""), eval_result.get("score", 0),
                 result[:3000], trace_id, eval_result.get("score", 0))
            )
            self._conn.commit()
        except Exception as e:
            logger.warning(f"Failed to save attempt: {e}")

    def save_lesson(self, lesson: str, trace_id: int = 0):
        """保存一条教训。"""
        if not self.db_path:
            return
        try:
            self._conn.execute(
                "INSERT INTO lesson (source_trace_id, lesson, created_at) VALUES (?, ?, ?)",
                (trace_id, lesson[:500], datetime.now().isoformat())
            )
            self._conn.commit()
        except Exception as e:
            logger.warning(f"Failed to save lesson: {e}")

    def load_lessons(self, limit: int = 20) -> list[str]:
        """加载最近的教训。"""
        if not self.db_path:
            return []
        try:
            rows = self._conn.execute(
                "SELECT lesson FROM lesson ORDER BY created_at DESC LIMIT ?",
                (limit,)
            ).fetchall()
            return [r["lesson"] for r in rows]
        except Exception as e:
            logger.warning(f"Failed to load lessons: {e}")
            return []

    def search_lessons(self, query: str, top_k: int = 5) -> list[str]:
        """搜索与 query 相关的教训。CJK 二字滑窗 + 英文整词 LIKE 匹配。"""
        if not self.db_path:
            return []
        try:
            import re as _re
            # 提取关键词 token
            ascii_words = _re.findall(r'[a-zA-Z][a-zA-Z0-9]+', query)
            cjk_chars = _re.findall(r'[\u4e00-\u9fff]', query)
            cjk_bigrams = [cjk_chars[i] + cjk_chars[i+1]
                           for i in range(len(cjk_chars) - 1)]
            tokens = ascii_words + cjk_bigrams
            if not tokens:
                # Fallback: 加载最近的
                return self.load_lessons(top_k)
            # 用 AND 缩小范围，转义 LIKE 通配符
            def _escape_like(t: str) -> str:
                return t.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')[:50]
            conditions = " AND ".join(
                f"lesson LIKE '%{_escape_like(t)}%' ESCAPE '\\'"
                for t in tokens[:6]  # 最多 6 个 token
            )
            rows = self._conn.execute(
                f"""SELECT lesson FROM lesson
                   WHERE {conditions}
                   ORDER BY created_at DESC LIMIT ?""",
                (top_k,)
            ).fetchall()
            return [r["lesson"] for r in rows]
        except Exception as e:
            logger.warning(f"Failed to search lessons: {e}")
            return []

    def get_trace(self, trace_id: int) -> dict | None:
        """获取一条轨迹的完整信息（含所有 attempts）。"""
        if not self.db_path:
            return None
        try:
            row = self._conn.execute(
                "SELECT * FROM trace WHERE id=?", (trace_id,)
            ).fetchone()
            if not row:
                return None
            attempts = self._conn.execute(
                "SELECT * FROM attempt WHERE trace_id=? ORDER BY attempt_num",
                (trace_id,)
            ).fetchall()
            return {
                "id": row["id"],
                "task": row["task"],
                "created_at": row["created_at"],
                "final_status": row["final_status"],
                "final_score": row["final_score"],
                "best_result": row["best_result"],
                "attempts": [dict(a) for a in attempts],
            }
        except Exception as e:
            logger.warning(f"Failed to get trace: {e}")
            return None

    def recent_traces(self, limit: int = 10) -> list[dict]:
        """获取最近的轨迹列表。"""
        if not self.db_path:
            return []
        try:
            rows = self._conn.execute(
                """SELECT id, task, created_at, final_status, final_score
                   FROM trace ORDER BY created_at DESC LIMIT ?""",
                (limit,)
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.warning(f"Failed to get recent traces: {e}")
            return []

    def stats(self) -> dict:
        """返回轨迹统计。"""
        if not self.db_path:
            return {}
        try:
            total = self._conn.execute("SELECT COUNT(*) FROM trace").fetchone()[0]
            success = self._conn.execute(
                "SELECT COUNT(*) FROM trace WHERE final_score >= 7"
            ).fetchone()[0]
            lessons = self._conn.execute("SELECT COUNT(*) FROM lesson").fetchone()[0]
            return {
                "total_traces": total,
                "success_count": success,
                "success_rate": round(success / total, 2) if total else 0,
                "total_lessons": lessons,
            }
        except Exception:
            return {}


class Memory:
    """
    四层记忆门面:
      1. Working Memory  (窗口):    最近 k 轮对话
      2. Persistent Memory (文件):   任务历史，自动轮转 200 行
      3. Reflection Memory (文件):   反思教训，自动轮转 200 行
      4. Long-term Memory (SQLite):  FTS5 全文检索，跨会话语义匹配
    """

    def __init__(self, window_size: int = 10, file_path: str | None = None,
                 reflection_path: str | None = None,
                 long_term_db: str | None = None,
                 reflexion_db: str | None = None,
                 max_lines: int = 200):
        """
        Args:
            window_size:    会话窗口保留的对话轮数
            file_path:      持久化记忆文件路径 (None 则禁用)
            reflection_path: 反思记忆文件路径 (None 则禁用)
            long_term_db:   长期记忆 SQLite 路径 (None 则禁用)
            reflexion_db:   Reflexion 轨迹 SQLite 路径 (None 则禁用)
            max_lines:      持久/反思记忆文件最大行数
        """
        self.window_size = window_size
        self.file_path = file_path
        self.reflection_path = reflection_path
        self.max_lines = max_lines
        self._messages: list[dict] = []  # [{"role": str, "content": str}]
        self._long_term = LongTermMemory(long_term_db) if long_term_db else None
        self._reflexion_trace = ReflexionTrace(reflexion_db) if reflexion_db else None

        # Persistent memory cache: avoid re-reading file on every agent_loop iteration
        self._persistent_cache: str | None = None
        self._persistent_dirty: bool = True

    # ── 1. Working Memory (窗口) ────────────────────────

    def save_context(self, user_input: str, assistant_output: str):
        """保存一轮对话到窗口记忆。"""
        self._messages.append({"role": "user", "content": user_input})
        self._messages.append({"role": "assistant", "content": assistant_output})

        max_msgs = self.window_size * 2
        if len(self._messages) > max_msgs:
            self._messages = self._messages[-max_msgs:]

    def get_window_messages(self) -> list[dict]:
        """返回窗口记忆中的消息列表。"""
        return list(self._messages)

    def clear(self):
        """清除会话记忆（窗口）。持久记忆和反思记忆不受影响。"""
        self._messages.clear()

    @property
    def round_count(self) -> int:
        """当前会话轮数。"""
        return len(self._messages) // 2

    # ── 2. Persistent Memory (文件) ─────────────────────

    def load_persistent(self) -> str:
        """从文件加载持久记忆（最近 N 行）。带缓存，避免每轮读磁盘。"""
        if not self.file_path:
            return ""
        if not self._persistent_dirty and self._persistent_cache is not None:
            return self._persistent_cache
        try:
            content = Path(self.file_path).read_text(encoding="utf-8")
            lines = content.split("\n")
            result = "\n".join(lines[-self.max_lines:]) if len(lines) > self.max_lines else content
            self._persistent_cache = result
            self._persistent_dirty = False
            return result
        except Exception as e:
            logger.warning(f"Failed to load persistent memory: {e}")
            return ""

    def save_persistent(self, task: str, result: str):
        """追加任务结果到持久记忆文件 + 长期记忆 DB。"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # 写入文件持久记忆
        if self.file_path:
            entry = f"\n## {timestamp}\n**Task:** {task}\n**Result:** {result[:500]}\n"
            self._append_with_rotation(self.file_path, entry, max_lines=self.max_lines)
            self._persistent_dirty = True  # invalidate cache
        # 写入长期记忆（全文检索）
        if self._long_term:
            self._long_term.add(task, result)

    # ── 3. Reflection Memory (文件) ─────────────────────

    def load_reflections(self) -> str:
        """加载反思记忆（最近 N 行）。供策略注入上下文。"""
        if not self.reflection_path or not os.path.exists(self.reflection_path):
            return ""
        try:
            content = Path(self.reflection_path).read_text(encoding="utf-8")
            lines = content.split("\n")
            return "\n".join(lines[-self.max_lines:]) if len(lines) > self.max_lines else content
        except Exception as e:
            logger.warning(f"Failed to load reflection memory: {e}")
            return ""

    def save_reflection(self, task: str, reflection: str, eval_result: dict):
        """追加反思教训到文件。"""
        if not self.reflection_path:
            return
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry = (
            f"\n## {timestamp}\n"
            f"**Task:** {task}\n"
            f"**Status:** {eval_result.get('status', 'unknown')}\n"
            f"**Score:** {eval_result.get('score', 'N/A')}\n"
            f"**Reflection:** {reflection[:500]}\n"
        )
        self._append_with_rotation(self.reflection_path, entry, max_lines=self.max_lines)

    # ── 4. Long-term Memory (SQLite FTS5) ───────────────

    def load_relevant(self, query: str, top_k: int = 3) -> str:
        """全文检索与 query 最相关的历史记忆。供 Agent 注入上下文。

        Returns:
            格式化的记忆文本，或空字符串。
        """
        if not self._long_term:
            return ""
        results = self._long_term.search(query, top_k=top_k)
        if not results:
            return ""
        parts = []
        for r in results:
            parts.append(f"- [{r['created_at'][:10]}] {r['task'][:100]}\n  → {r['result'][:200]}")
        return "\n".join(parts)

    # ── 文件轮转工具 ────────────────────────────────────

    @staticmethod
    def _append_with_rotation(file_path: str, entry: str, max_lines: int = 200):
        """追加内容到文件，超过 max_lines 时截断保留最近条目。

        性能优化：先用文件大小估算行数，只在可能超限时才读取+截断。
        避免每次写入都读取整个文件（对于 Reflexion 多次重试场景尤为重要）。

        线程安全：使用文件级锁确保 append + read + truncate 原子执行，
        防止并发写入导致数据丢失。
        """
        try:
            path = Path(file_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            lock = _get_rotate_lock(file_path)

            # 快速路径：先 append，再检查是否需要截断
            with lock:
                # 追加写入
                with open(path, "a", encoding="utf-8") as f:
                    f.write(entry)

                # 快速估算：用文件大小 / 平均行宽 估算行数
                # 避免每次都读取整个文件
                file_size = path.stat().st_size
                # 估算平均行宽（含换行符），取保守值
                avg_line_bytes = 80
                estimated_lines = file_size // avg_line_bytes

                if estimated_lines <= max_lines:
                    return  # 快速路径：估算没超限，不需要截断

                # 慢路径：估算超限，读取实际内容截断
                content = path.read_text(encoding="utf-8")
                lines = content.split("\n")
                if len(lines) > max_lines:
                    truncated = "\n".join(lines[-max_lines:])
                    path.write_text(truncated, encoding="utf-8")
                    logger.info(f"{path.name} truncated to {max_lines} lines "
                                f"(was {len(lines)} lines)")
        except Exception as e:
            logger.warning(f"Failed to write {file_path}: {e}")

    # ── 快捷方法 ────────────────────────────────────────

    def get_summary(self) -> str:
        """返回记忆状态摘要。"""
        parts = [f"Working: {self.round_count} rounds (window: {self.window_size})"]
        if self.file_path and os.path.exists(self.file_path):
            parts.append(f"Persistent: {self.file_path}")
        if self.reflection_path and os.path.exists(self.reflection_path):
            parts.append(f"Reflection: {self.reflection_path}")
        if self._long_term:
            parts.append(f"Long-term: {self._long_term.count()} entries")
        return " | ".join(parts)
