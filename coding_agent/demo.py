"""coding_agent v0.1: local demo — analyze-access-logs style task, no Docker.

This mirrors the Terminal-Bench task `analyze-access-logs` so the same
agent can later run inside the benchmark sandbox unchanged.
"""

import argparse
import tempfile
from pathlib import Path

from coding_agent.agent import make_coding_agent
from coding_agent.logging_util import RunLogger
from coding_agent.terminal import LocalBackend


ACCESS_LOG_SAMPLE = """127.0.0.1 - - [10/Oct/2026:13:55:36 -0700] "GET /index.html HTTP/1.1" 200 2326
127.0.0.1 - - [10/Oct/2026:13:55:37 -0700] "GET /about.html HTTP/1.1" 200 1500
192.168.1.1 - - [10/Oct/2026:13:55:38 -0700] "GET /index.html HTTP/1.1" 200 2326
127.0.0.1 - - [10/Oct/2026:13:55:39 -0700] "POST /api/login HTTP/1.1" 404 120
10.0.0.2 - - [10/Oct/2026:13:55:40 -0700] "GET /index.html HTTP/1.1" 200 2326
192.168.1.1 - - [10/Oct/2026:13:55:41 -0700] "GET /favicon.ico HTTP/1.1" 404 66
10.0.0.2 - - [10/Oct/2026:13:55:42 -0700] "GET /about.html HTTP/1.1" 200 1500
"""


def build_task_dir() -> Path:
    d = Path(tempfile.mkdtemp(prefix="coding_agent_task_"))
    (d / "access_log").write_text(ACCESS_LOG_SAMPLE, encoding="utf-8")
    return d


INSTRUCTION = (
    "分析 /app 目录下 access_log 文件（web 服务器访问日志），生成报告 report.txt：\n"
    "1. 总请求数，格式一行：Total requests: <number>\n"
    "2. 独立 IP 数，格式一行：Unique IP addresses: <number>\n"
    "3. Top 3 URL 段：标题行 'Top 3 URLs:'，每行 '  <url>: <count>'\n"
    "4. 404 错误数，格式一行：404 errors: <number>"
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workdir", default=None)
    args = parser.parse_args()

    workdir = Path(args.workdir) if args.workdir else build_task_dir()
    log_dir = workdir / "run_logs"
    logger = RunLogger(log_dir)
    backend = LocalBackend(workdir)
    agent = make_coding_agent(backend, logger, workdir=workdir)

    print(f"工作目录：{workdir}")
    print(f"任务：{INSTRUCTION[:80]}...\n")
    agent.print_response(INSTRUCTION, stream=False)

    print("\n=== 验证报告 ===")
    report = workdir / "report.txt"
    if report.exists():
        print(report.read_text(encoding="utf-8"))
    else:
        print("report.txt 不存在")
    print(f"\n=== 日志摘要：{logger.summary()} ===")
    print(f"日志文件：{log_dir / 'events.jsonl'}")


if __name__ == "__main__":
    main()
