# coding-agent

从零构建一个 Coding Agent 的学习项目。

不是"做一个产品"，也不是"写一个工具"——是**把 Agent 应用的每一层机制亲手造一遍**：模型怎么接入、工具怎么执行、错误怎么处理、上下文怎么管理、行为怎么评测。造出来的东西是否优雅不重要，**每一层为什么这样设计、踩了什么坑、怎么验证它工作**才是这个项目的产出。

## 为什么从零造

AI 编程工具（Claude Code / OpenCode / Cursor Agent）已经很好用了。但"会用"和"理解"之间隔着一整层：为什么 Agent 循环要有硬边界？错误该重试还是该换路？上下文爆了怎么办？这些问题只有自己造一遍才能回答。

本项目是《AI Agent 应用开发实践》课程的实践出口：课程实验区（A1–A8 自研最小 Agent + 框架对照）解决了"机制是什么"，这里解决"造出来会碰到什么"。

## 学习主线（按时间序，每步一个主题）

| 版本 | 主题 | 学到了什么 |
|---|---|---|
| v0.1–v0.3 | 起步：Agent 组装 + 评测链路 | Agno 组装、LLM 适配的坑（代理/role/深拷贝）、TB 接入、oracle 基线 |
| v0.4 | 可靠性初探 | LLM 调用会偶发 500 → 重试+failover 部件；tmux 敲键盘的多行命令坑 → base64 单行化 |
| v0.5（进行中） | 错误处理体系 | 工具失败三分法（transient/permanent/semantic）、事实与决策分离、TB 评测通道的契约对齐 |
| 后续 | 见毕业标准 | 计划-执行分离、上下文预算、权限模式…… |

每一步的完整决策记录见 [docs/CHANGELOG.md](docs/CHANGELOG.md)（含失败、踩坑与修复过程，真实优先于好看）；工程流程规范见 [docs/WORKFLOW.md](docs/WORKFLOW.md)。

## 毕业标准（训练场的出口）

对齐主流终端 Coding Agent 的基本功能面（多轮会话、可靠性、计划执行、持久化、上下文压缩、文件工具、权限模式），并通过 Terminal-Bench 官方 80 题的量化达标线（方案 C：v0.5 后跑全量基线定分）。达标后项目转入下一阶段。

## 快速开始

```bash
cd ~/cs/proj/coding-agent
uv sync

# 交互模式（多轮迭代）
uv run python -m coding_agent.cli --workdir /path/to/你的项目

# 一次性任务
uv run python -m coding_agent.cli --workdir /path/to/你的项目 --task "写一个脚本…并运行验证"

# 跑测试
uv run python -m unittest discover -s coding_agent -p 'test_*.py' -v
```

## 评测

```bash
# 链路验证（oracle，不需要 LLM/API）
./tb_run.sh run --agent oracle --dataset-path tb/tasks --task-id analyze-access-logs

# 评测 Coding Agent（真实 LLM）
./tb_run.sh run --agent-import-path coding_agent.tb_adapter:CodingAgentTB \
    --dataset-path tb/tasks --n-tasks 6
```

评测手册（本机网络适配、结果解读、常见问题）：[tb/TB_TESTING.md](tb/TB_TESTING.md)

## 当前状态

- v0.4 已发布：Provider 故障切换 + 多行命令修复，TB 6/6
- v0.5 进行中：错误分类与恢复策略（分类器/路由已落地，TmuxBackend 通道重构审核整改中）
- 全量测试 51 项绿；完整进度见 CHANGELOG 与 GitHub Milestones

## 环境说明

本机（Ubuntu 24.04 / GLM via OpenAI 兼容协议）的网络适配细节——pypi/docker/ghcr 的镜像源、socks 代理处理——都收敛在 `tb_run.sh` 与 `tb/TB_TESTING.md`，不污染代码。
