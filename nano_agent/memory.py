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
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("nano_agent.memory")


class LongTermMemory:
    """
    长期记忆 — SQLite FTS5 全文检索。

    存储所有任务的历史记录，支持语义检索。
    零依赖（sqlite3 是 Python stdlib），FTS5 是 SQLite 内置全文搜索引擎。

    未来可替换为向量数据库（Chroma/Milvus），接口不变。
    """

    def __init__(self, db_path: str = "long_term_memory.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        """初始化 FTS5 全文搜索表。"""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS memories USING fts5(
                    task,
                    result,
                    content,
                    created_at UNINDEXED,
                    tokenize='unicode61'
                )
            """)
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"Failed to init long-term memory DB: {e}")
            self.db_path = None  # 禁用

    def add(self, task: str, result: str):
        """添加一条记忆。"""
        if not self.db_path:
            return
        try:
            content = f"Task: {task}\nResult: {result}"
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                "INSERT INTO memories (task, result, content, created_at) VALUES (?, ?, ?, ?)",
                (task, result[:2000], content, datetime.now().isoformat())
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"Failed to add long-term memory: {e}")

    def search(self, query: str, top_k: int = 3) -> list[dict]:
        """全文检索相关记忆。返回最相关的 top_k 条。

        Returns:
            [{"task": str, "result": str, "score": float, "created_at": str}]
        """
        if not self.db_path:
            return []
        try:
            conn = sqlite3.connect(self.db_path)
            # 转义特殊字符，用 OR 连接查询词（更宽松匹配）
            words = query.replace('"', '').split()
            safe_query = " OR ".join(w for w in words if len(w) > 1)[:200]
            if not safe_query:
                return []
            rows = conn.execute(
                """SELECT task, result, created_at, bm25(memories) as score
                   FROM memories
                   WHERE memories MATCH ?
                   ORDER BY score
                   LIMIT ?""",
                (safe_query, top_k)
            ).fetchall()
            conn.close()
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
            conn = sqlite3.connect(self.db_path)
            count = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
            conn.close()
            return count
        except Exception:
            return 0

    def clear(self):
        """清空所有长期记忆。"""
        if not self.db_path:
            return
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute("DELETE FROM memories")
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"Failed to clear long-term memory: {e}")


class Memory:
    """
    四层记忆门面:
      1. Working Memory  (窗口):    最近 k 轮对话
      2. Persistent Memory (文件):   任务历史，自动轮转 200 行
      3. Reflection Memory (文件):   反思教训，自动轮转 200 行
      4. Long-term Memory (SQLite):  FTS5 全文检索，跨会话语义匹配
    """

    def __init__(self, window_size: int = 10, file_path: Optional[str] = None,
                 reflection_path: Optional[str] = None,
                 long_term_db: Optional[str] = None):
        """
        Args:
            window_size:    会话窗口保留的对话轮数
            file_path:      持久化记忆文件路径 (None 则禁用)
            reflection_path: 反思记忆文件路径 (None 则禁用)
            long_term_db:   长期记忆 SQLite 路径 (None 则禁用)
        """
        self.window_size = window_size
        self.file_path = file_path
        self.reflection_path = reflection_path
        self._messages: list[dict] = []  # [{"role": str, "content": str}]
        self._long_term = LongTermMemory(long_term_db) if long_term_db else None

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
        """从文件加载持久记忆（最近 200 行）。"""
        if not self.file_path or not os.path.exists(self.file_path):
            return ""
        try:
            content = Path(self.file_path).read_text(encoding="utf-8")
            lines = content.split("\n")
            return "\n".join(lines[-200:]) if len(lines) > 200 else content
        except Exception as e:
            logger.warning(f"Failed to load persistent memory: {e}")
            return ""

    def save_persistent(self, task: str, result: str):
        """追加任务结果到持久记忆文件 + 长期记忆 DB。"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # 写入文件持久记忆
        if self.file_path:
            entry = f"\n## {timestamp}\n**Task:** {task}\n**Result:** {result[:500]}\n"
            self._append_with_rotation(self.file_path, entry, max_lines=200)
        # 写入长期记忆（全文检索）
        if self._long_term:
            self._long_term.add(task, result)

    # ── 3. Reflection Memory (文件) ─────────────────────

    def load_reflections(self) -> str:
        """加载反思记忆（最近 200 行）。供策略注入上下文。"""
        if not self.reflection_path or not os.path.exists(self.reflection_path):
            return ""
        try:
            content = Path(self.reflection_path).read_text(encoding="utf-8")
            lines = content.split("\n")
            return "\n".join(lines[-200:]) if len(lines) > 200 else content
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
        self._append_with_rotation(self.reflection_path, entry, max_lines=200)

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
        """追加内容到文件，超过 max_lines 时截断保留最近条目。"""
        try:
            path = Path(file_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(entry)
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
