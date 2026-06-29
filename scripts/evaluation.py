"""
Evaluation 模块 — Agent 基准测试 + 策略对比。

功能:
  1. 标准任务集 (eval_tasks.json) — 统一测试用例
  2. 策略评分 — 对每个任务跑指定策略，自动评判结果
  3. 对比报告 — 不同策略的成功率/平均轮次/平均 token

使用:
  # CLI
  python run_eval.py --strategies default,react,reflexion --tasks all
  python run_eval.py --strategies default --tasks calc,read

  # 代码
  from nano_agent.evaluation import Benchmark
  bench = Benchmark()
  report = bench.run(strategies=["default", "react"], task_ids=["calc"])
  print(report.summary())
"""

import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from nano_agent.config import Config
from nano_agent.agent import Agent

logger = logging.getLogger("nano_agent.evaluation")


@dataclass
class TaskCase:
    """单个基准测试用例。"""
    id: str                    # 唯一标识 (如 "calc")
    description: str           # 任务描述 (发给 Agent 的内容)
    category: str = "general"  # 分类: general / coding / search / math
    difficulty: str = "easy"   # easy / medium / hard
    expected_keywords: list[str] = field(default_factory=list)  # 结果中应包含的关键词
    expected_tool: str = ""    # 期望调用的工具名 (空则不检查)
    timeout: int = 60          # 超时秒数


@dataclass
class TaskResult:
    """单个任务的执行结果。"""
    task_id: str
    strategy: str
    success: bool              # 是否通过 (关键词检查 + 工具检查)
    response: str              # Agent 最终回复
    duration_s: float          # 耗时
    tool_calls: int = 0        # 工具调用次数
    error: str = ""            # 错误信息 (如果有)

    def passed(self) -> str:
        return "✅" if self.success else "❌"


@dataclass
class EvalReport:
    """整轮评估报告。"""
    results: list[TaskResult] = field(default_factory=list)
    strategy: str = ""

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed_count(self) -> int:
        return sum(1 for r in self.results if r.success)

    @property
    def success_rate(self) -> float:
        return self.passed_count / self.total if self.total else 0.0

    @property
    def avg_duration(self) -> float:
        if not self.results:
            return 0.0
        return sum(r.duration_s for r in self.results) / len(self.results)

    @property
    def avg_tool_calls(self) -> float:
        if not self.results:
            return 0.0
        return sum(r.tool_calls for r in self.results) / len(self.results)

    def summary(self) -> str:
        """返回文本摘要。"""
        lines = [
            f"{'='*60}",
            f"Strategy: {self.strategy}",
            f"{'='*60}",
            f"Total: {self.total} | Passed: {self.passed_count} | "
            f"Success Rate: {self.success_rate:.1%}",
            f"Avg Duration: {self.avg_duration:.2f}s | "
            f"Avg Tool Calls: {self.avg_tool_calls:.1f}",
            f"{'-'*60}",
        ]
        for r in self.results:
            lines.append(
                f"  {r.passed()} {r.task_id:<15} "
                f"({r.duration_s:.1f}s, {r.tool_calls} tools) "
                f"{r.error or ''}"
            )
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "strategy": self.strategy,
            "total": self.total,
            "passed": self.passed_count,
            "success_rate": self.success_rate,
            "avg_duration": self.avg_duration,
            "avg_tool_calls": self.avg_tool_calls,
            "results": [asdict(r) for r in self.results],
        }


class Benchmark:
    """Agent 基准测试运行器。"""

    # 内置默认任务集
    DEFAULT_TASKS = [
        TaskCase(
            id="calc",
            description="Calculate 123 * 456 + 789",
            category="math",
            difficulty="easy",
            expected_keywords=["56877"],
            expected_tool="calculate",
        ),
        TaskCase(
            id="bash_ls",
            description="Run `ls` in the current directory to list files",
            category="general",
            difficulty="easy",
            expected_keywords=[],  # ls 输出不确定，只检查工具调用
            expected_tool="bash",
        ),
        TaskCase(
            id="write_read",
            description="Write 'hello world' to /tmp/eval_test.txt, then read it back",
            category="coding",
            difficulty="easy",
            expected_keywords=["hello world"],
            expected_tool="read",
        ),
        TaskCase(
            id="grep_search",
            description="Search for 'import' in the current directory using grep",
            category="coding",
            difficulty="easy",
            expected_keywords=[],
            expected_tool="grep",
        ),
    ]

    def __init__(self, tasks_file: Optional[str] = None, config: Optional[Config] = None):
        """
        Args:
            tasks_file: 自定义任务 JSON 文件路径 (None 则用内置任务)
            config:     Agent 配置 (None 则用默认 Config)
        """
        self.tasks: list[TaskCase] = []
        self.config = config or Config()

        if tasks_file and os.path.exists(tasks_file):
            self._load_tasks(tasks_file)
        else:
            self.tasks = list(self.DEFAULT_TASKS)

        logger.info(f"Benchmark initialized with {len(self.tasks)} tasks")

    def _load_tasks(self, path: str):
        """从 JSON 文件加载任务集。"""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.tasks = [TaskCase(**t) for t in data]

    def run(self, strategies: list[str], task_ids: Optional[list[str]] = None,
            verbose: bool = True) -> list[EvalReport]:
        """
        运行基准测试。

        Args:
            strategies: 要测试的策略列表 ["default", "react", ...]
            task_ids:   要跑的任务 ID 列表 (None 则全部)
            verbose:    是否打印进度

        Returns:
            每个策略一个 EvalReport
        """
        tasks = self.tasks
        if task_ids:
            tasks = [t for t in self.tasks if t.id in task_ids]

        reports = []
        for strategy in strategies:
            report = EvalReport(strategy=strategy)
            if verbose:
                logger.info(f"\n{'='*60}\nEvaluating strategy: {strategy}\n{'='*60}")

            for task in tasks:
                result = self._run_single(task, strategy)
                report.results.append(result)
                if verbose:
                    logger.info(f"  {result.passed()} {task.id} ({result.duration_s:.1f}s)")

            if verbose:
                logger.info(report.summary())
            reports.append(report)

        return reports

    def _run_single(self, task: TaskCase, strategy: str) -> TaskResult:
        """执行单个测试用例。"""
        start = time.time()
        tool_call_count = 0

        try:
            # 每个任务用新 Agent（隔离状态）
            agent = Agent(self.config)

            # 拦截事件统计工具调用次数
            original_emit = agent._emit
            def counting_emit(event_type, data):
                nonlocal tool_call_count
                if event_type == "tool_call":
                    tool_call_count += 1
                if original_emit:
                    pass  # 静默，不外发
            agent._emit = counting_emit

            response = agent.run(task.description, strategy=strategy)
            duration = time.time() - start

            # 评判
            success = self._evaluate(task, response, tool_call_count)

            return TaskResult(
                task_id=task.id,
                strategy=strategy,
                success=success,
                response=response[:500],
                duration_s=round(duration, 2),
                tool_calls=tool_call_count,
            )

        except Exception as e:
            duration = time.time() - start
            return TaskResult(
                task_id=task.id,
                strategy=strategy,
                success=False,
                response="",
                duration_s=round(duration, 2),
                tool_calls=tool_call_count,
                error=str(e)[:200],
            )

    def _evaluate(self, task: TaskCase, response: str, tool_calls: int) -> bool:
        """
        评判结果是否通过。

        判定规则:
          1. 如果有 expected_keywords，response 必须包含所有关键词
          2. 如果有 expected_tool，必须至少调用过 1 次工具
             (工具调用次数 > 0 即认为调用了工具)
          3. response 不能是 "Max iterations reached"
        """
        if "Max iterations reached" in response:
            return False

        # 关键词检查
        for kw in task.expected_keywords:
            if kw.lower() not in response.lower():
                return False

        # 工具调用检查（只检查是否调用了工具，不检查具体名称）
        if task.expected_tool and tool_calls == 0:
            return False

        return True

    def compare(self, reports: list[EvalReport]) -> str:
        """生成多策略对比表。"""
        lines = [
            f"\n{'='*70}",
            f"Strategy Comparison",
            f"{'='*70}",
            f"{'Strategy':<20} {'Total':>6} {'Passed':>8} {'Rate':>8} "
            f"{'Avg Time':>10} {'Avg Tools':>10}",
            f"{'-'*70}",
        ]
        for r in reports:
            lines.append(
                f"{r.strategy:<20} {r.total:>6} {r.passed_count:>8} "
                f"{r.success_rate:>7.1%} {r.avg_duration:>9.2f}s "
                f"{r.avg_tool_calls:>10.1f}"
            )
        return "\n".join(lines)

    def save_report(self, reports: list[EvalReport], path: str):
        """保存报告到 JSON 文件。"""
        data = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "reports": [r.to_dict() for r in reports],
        }
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        logger.info(f"Report saved to {path}")
