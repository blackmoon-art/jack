"""
记忆模块 — 窗口会话记忆 + 文件持久化记忆的统一接口。

融合:
  - demo_2: 窗口记忆 (ConversationBufferWindowMemory)
  - nanoAgent: markdown 文件持久化 (agent_memory.md)
"""

import os
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


class Memory:
    """
    双层记忆:
      1. 会话记忆 (window): 最近 k 轮对话，窗口淘汰
      2. 持久记忆 (file):  追加写入 markdown 文件，加载时读最近 50 行
    """

    def __init__(self, window_size: int = 10, file_path: Optional[str] = None):
        """
        Args:
            window_size: 会话窗口保留的对话轮数
            file_path:   持久化记忆文件路径 (None 则禁用)
        """
        self.window_size = window_size
        self.file_path = file_path
        self._messages: list[dict] = []  # [{"role": str, "content": str}]

    # ── 会话记忆 (window) ───────────────────────────────

    def save_context(self, user_input: str, assistant_output: str):
        """保存一轮对话到窗口记忆。"""
        self._messages.append({"role": "user", "content": user_input})
        self._messages.append({"role": "assistant", "content": assistant_output})

        # 窗口淘汰：保留最近 2*window_size 条消息
        max_msgs = self.window_size * 2
        if len(self._messages) > max_msgs:
            self._messages = self._messages[-max_msgs:]

    def get_window_messages(self) -> list[dict]:
        """返回窗口记忆中的消息列表。"""
        return list(self._messages)

    def clear(self):
        """清除会话记忆。"""
        self._messages.clear()

    @property
    def round_count(self) -> int:
        """当前会话轮数。"""
        return len(self._messages) // 2

    # ── 持久记忆 (file) ─────────────────────────────────

    def load_persistent(self) -> str:
        """从文件加载持久记忆（最近 50 行）。"""
        if not self.file_path or not os.path.exists(self.file_path):
            return ""
        try:
            content = Path(self.file_path).read_text(encoding="utf-8")
            lines = content.split("\n")
            return "\n".join(lines[-50:]) if len(lines) > 50 else content
        except Exception:
            return ""

    def save_persistent(self, task: str, result: str):
        """追加任务结果到持久记忆文件。"""
        if not self.file_path:
            return
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry = f"\n## {timestamp}\n**Task:** {task}\n**Result:** {result[:500]}\n"
        try:
            Path(self.file_path).parent.mkdir(parents=True, exist_ok=True)
            with open(self.file_path, "a", encoding="utf-8") as f:
                f.write(entry)
        except Exception:
            pass

    # ── 快捷方法 ────────────────────────────────────────

    def get_summary(self) -> str:
        """返回记忆状态摘要。"""
        parts = [f"会话轮数: {self.round_count} (窗口: {self.window_size})"]
        if self.file_path and os.path.exists(self.file_path):
            parts.append(f"持久记忆: {self.file_path}")
        return " | ".join(parts)
