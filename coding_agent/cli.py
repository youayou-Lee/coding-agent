"""coding_agent v0.2: interactive CLI — use the agent for real dev work.

Two modes:
  one-shot:  uv run python -m course.coding_agent.cli --workdir <dir> --task "任务"
  repl:      uv run python -m course.coding_agent.cli --workdir <dir>
             (multi-turn: the same agent instance keeps session history,
              so you can iterate — "改一下", "再加个功能", "测试怎么不过")

Logs land in <workdir>/.agent_logs/events.jsonl.
"""

import argparse
import sys
from pathlib import Path

from coding_agent.agent import make_coding_agent
from coding_agent.logging_util import RunLogger
from coding_agent.terminal import LocalBackend


def main() -> None:
    parser = argparse.ArgumentParser(description="Coding Agent CLI")
    parser.add_argument("--workdir", required=True, help="项目目录（agent 只能操作这里）")
    parser.add_argument("--task", default=None, help="一次性任务；不填则进入交互模式")
    parser.add_argument("--max-steps", type=int, default=20)
    args = parser.parse_args()

    workdir = Path(args.workdir).resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    logger = RunLogger(workdir / ".agent_logs")
    backend = LocalBackend(workdir)
    agent = make_coding_agent(backend, logger, max_steps=args.max_steps, workdir=workdir)

    if args.task:
        agent.print_response(args.task, stream=False)
        print(f"\n[日志] {logger.events_path}")
        return

    print(f"Coding Agent 交互模式 | 工作目录: {workdir}")
    print("输入任务回车执行；exit/quit 退出；Ctrl-D 退出。")
    print("注意：本地模式非沙箱，agent 能在工作目录内执行任意命令。")
    while True:
        try:
            task = input("\n任务> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not task:
            continue
        if task in ("exit", "quit"):
            break
        agent.print_response(task, stream=False)
        print(f"\n[日志摘要] {logger.summary()} | {logger.events_path}")


if __name__ == "__main__":
    sys.exit(main())
