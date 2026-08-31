# coding-agent

一个基于 **Agno + OpenAI 兼容 LLM** 的真实编程 Agent（当前接入 GLM glm-5.3-flash，此前为 DeepSeek）：能在项目目录里自主探索、写代码、跑命令、看错误、修复，并通过 **Terminal-Bench**（pytest 机器判定）做评测。

> 起源：个人 AI Agent 学习课程（A1–A7 自研最小 Agent 理解机制）之后，用框架实践的第一个真实应用。从 `minimal-agent-lab` 实验区迁出，长期建设。

## 快速开始

```bash
# 安装（uv 管理）
cd ~/cs/proj/coding-agent
uv sync

# 交互模式（像 Claude Code 一样多轮迭代）
uv run python -m coding_agent.cli --workdir /path/to/你的项目

# 一次性任务
uv run python -m coding_agent.cli --workdir /path/to/你的项目 --task "写一个脚本…并运行验证"
```

## 评测（Terminal-Bench）

```bash
# 链路验证（oracle，不需要 LLM/API）
./tb_run.sh run --agent oracle --dataset-path tb/tasks --task-id analyze-access-logs

# 评测 Coding Agent（真实 LLM）
./tb_run.sh run --agent-import-path coding_agent.tb_adapter:CodingAgentTB \
    --dataset-path tb/tasks --task-id analyze-access-logs
```

评测手册（含本机网络适配、结果解读、常见问题）：[tb/TB_TESTING.md](tb/TB_TESTING.md)

## 项目结构

```
coding-agent/
├── coding_agent/          # 核心包
│   ├── agent.py           # make_coding_agent：Agno + 4 工具（send_command/list_files/read_file/write_file）
│   ├── agno_compat.py     # OpenAICompatChat 适配器（role_map/trust_env/api_key/__deepcopy__/logger，全部踩坑收敛）
│   ├── config.py          # .env 加载 + LLM_API_KEY/BASE_URL/MODEL（provider 无关）
│   ├── terminal.py        # TerminalBackend 抽象：LocalBackend（本地）/ TmuxBackend（TB 沙箱）
│   ├── logging_util.py    # RunLogger：JSONL 全量事件（llm_call/tool_call/llm_response）
│   ├── cli.py             # 交互式 CLI（多轮迭代）
│   ├── demo.py            # 本地演示任务（access_log 分析）
│   ├── tb_adapter.py      # Terminal-Bench 接入（BaseAgent.perform_task）
│   └── test_agent.py      # 4 项确定性测试
├── tb/
│   ├── TB_TESTING.md      # 评测手册
│   └── tasks/             # 本机网络适配版任务集（官方任务需套用改造）
├── tb_run.sh              # 评测套壳脚本（代理/key/PYTHONPATH 自动处理）
├── runs/                  # 评测结果（results.json + agent 日志 + 终端录像）
└── pyproject.toml         # uv 项目定义
```

## 设计要点

- **后端抽象**：`LocalBackend`（本地 subprocess，开发用）与 `TmuxBackend`（TB Docker 沙箱，评测用）同接口——同一个 agent 代码两个环境直接切换。
- **权限边界**：文件工具限制在工作目录内（路径越界即拒）；`send_command` 是裸 shell（本地模式建议在 git 仓库用）。
- **日志系统**：每次运行落在 `<workdir>/.agent_logs/events.jsonl`；评测时额外写入 TB 的 `runs/<时间戳>/…/agent-logs/events.jsonl`。事件含 llm_call（请求）/tool_call（参数/结果/成败）/llm_response（最终），定位问题三步：failure_mode → events.jsonl → tests.log。
- **模型适配收敛**：OpenAICompatChat 内置 trust_env=False（代理隔离）、api_key/base_url/model 从项目根 `.env` 读取（LLM_API_KEY / LLM_BASE_URL / LLM_MODEL，provider 无关）、role_map（system 而非 developer）、__deepcopy__（MemoryManager 深拷贝丢 client 的坑）、logger 包装（LLM 调用进日志）。
- **GLM 接入事实（2026-08-31）**：glm-5.3-flash 是思考型模型（reasoning_content + reasoning_tokens），OpenAI 兼容协议正常；max_tokens 过小时思考吃掉全部预算导致 content 为空，Agent 的 max_tokens 必须留足。

## 当前状态

- ✅ 本地 CLI 可用（多轮迭代 + 一次性任务）
- ✅ Terminal-Bench 链路打通：oracle 100%、CodingAgentTB 通过 `analyze-access-logs`（Accuracy 100%）
- ✅ 日志系统完整（llm_call/tool_call/llm_response 事件序列）
- ⏳ 待办：更多 TB 任务验证（`--n-tasks`）；write_file 在沙箱的路径解析问题（agent 已能绕过，但应修复）；Token 统计接入 AgentResult

## 常用命令速查

```bash
uv run python -m coding_agent.cli --workdir <dir>            # 交互
uv run python -m coding_agent.cli --workdir <dir> --task "…" # 一次性
uv run python -m unittest discover -s coding_agent -p 'test_*.py' -v  # 测试
./tb_run.sh run --agent oracle --dataset-path tb/tasks --task-id analyze-access-logs  # 链路自检
asciinema play runs/<时间戳>/…/sessions/agent.cast           # 回放评测录像
```
