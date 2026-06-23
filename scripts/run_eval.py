#!/usr/bin/env python3
"""
Agent 基准测试 CLI — 对比不同策略的表现。

用法:
  python run_eval.py                              # 所有策略, 所有任务
  python run_eval.py --strategies default,react   # 指定策略
  python run_eval.py --tasks calc,bash_ls         # 指定任务
  python run_eval.py --output eval_report.json    # 保存报告
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from nano_agent.evaluation import Benchmark


def main():
    parser = argparse.ArgumentParser(description="Nano Agent Benchmark")
    parser.add_argument(
        "--strategies", type=str, default="default,react,plan-execute,reflexion,tree-of-thought",
        help="逗号分隔的策略列表 (default,react,plan-execute,reflexion,tree-of-thought)"
    )
    parser.add_argument(
        "--tasks", type=str, default="all",
        help="逗号分隔的任务 ID, 或 'all'"
    )
    parser.add_argument(
        "--output", type=str, default="",
        help="报告输出 JSON 路径 (可选)"
    )
    parser.add_argument(
        "--tasks-file", type=str, default="",
        help="自定义任务集 JSON 文件 (可选)"
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="静默模式, 只输出最终结果"
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    strategies = [s.strip() for s in args.strategies.split(",")]
    task_ids = None if args.tasks == "all" else [t.strip() for t in args.tasks.split(",")]

    bench = Benchmark(tasks_file=args.tasks_file or None)
    reports = bench.run(strategies=strategies, task_ids=task_ids, verbose=not args.quiet)

    # 对比表
    print(bench.compare(reports))

    # 保存
    if args.output:
        bench.save_report(reports, args.output)


if __name__ == "__main__":
    main()
