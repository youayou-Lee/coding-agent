# 迭代日志 — coding-agent

> 本文件是 coding-agent 长期共建的正式迭代记录。每个迭代周期一条：目标、改动、证据（live 测试结果）、发现的问题、下一步。
> 原则：**真实优先于好看**——失败照记，未验证的不写"已完成"。

---

## v0.1（2026-08-11）— 起点：A 系列课程的出口

**目标**：把 A1-A8 教学代码收敛成第一个真实可用的编程 Agent。

**架构落地**：

| 部件 | 文件 | 职责 |
|---|---|---|
| 模型适配 | `agno_compat.py` | Agno→OpenAI 兼容 API 的所有坑位收敛 |
| 工具集 | `agent.py` | 4 工具：send_command / list_files / read_file / write_file |
| 终端后端 | `terminal.py` | LocalBackend（本机子进程）/ TmuxBackend（沙箱）双实现 |
| 可观测 | `logging_util.py` | RunLogger：JSONL 事件流（每步 LLM 调用/工具调用全记录） |
| 评测 | `tb_adapter.py` + `tb_run.sh` | 接入 Terminal-Bench |

**证据**：TB 任务 `analyze-access-logs` live Accuracy 100%（DeepSeek）。

---

## v0.2（2026-08-31）— 模型层重构：GLM 接入 + .env 配置化

**目标**：模型可替换（DeepSeek → GLM），配置出代码。

**改动**：
- 新增 `config.py`：项目根 `.env` 加载（LLM_API_KEY / LLM_BASE_URL / LLM_MODEL / LLM_THINKING），不覆盖已有环境变量
- `DeepSeekChat` → `OpenAICompatChat`（保留别名），provider 无关
- GLM 事实：glm-5.3-flash 始终思考（error 1210 拒绝 disabled），`thinking={type:enabled, thinking_budget:low|high|max|数值}` 可控深度，默认 low

**证据**：端点直连 ✓；Agent 读文件任务 ✓；unittest 4/4。

**暴露的问题**（→ v0.3）：
- #2 GLM 偶发 500（`网络错误 id 1234`），无重试，一次抖动整个 run 终止
- 教训：模型层缺**故障切换**设计——单一 provider 是单点故障

---

## v0.3（2026-08-31）— C1 评测基线：TB 任务 1→6

**目标**：建立可回归的评测基线，让后续每次改动有数字对照。

**改动**：
- 官方题库 terminal-bench-core==0.1.1 持久化到 `tb/datasets/`（80 题，不再放 /tmp）
- 新增 5 题改造版（网络适配）：hello-world / csv-to-parquet / fix-permissions / sqlite-db-truncate / fix-pandas-version
- compose 统一：host 网络 + 宿主 uv 挂载 + 阿里云 pip/uv 镜像
- 基础镜像 pinned `:20250624`（ghcr :latest 拉不到）；python:3.8 走 daocloud

**证据**：
- oracle 6/6（题目环境全部可信）
- **live 基线（GLM）：5/6**——hello-world ✓ / analyze-access-logs ✓ / fix-permissions ✓ / csv-to-parquet ✓ / fix-pandas-version ✓(侥幸) / sqlite-db-truncate ✗ agent_timeout

**暴露的问题**（→ v0.4）：
- **#1 TmuxBackend 多行命令卡死**：heredoc/内嵌脚本触发 shell 续行提示符 `>`，tmux wait 死等超时，后续命令级联失效（sqlite-db-truncate 20 步预算只跑成 4 条命令的根因）
- #2 同上（GLM 500 无重试，sqlite-db-truncate 的最后一击）

**下一步（v0.4 方向）**：
1. 修 #1：多行命令 base64 单行化传输（或写临时文件执行）
2. 修 #2：**LLM Provider 部件化**——重试（指数退避）+ 故障切换（GLM 挂了自动切 DeepSeek 等备胎），provider 列表进 .env
3. 重跑 6 题基线验证，对照 5/6

---

## 附：网络适配速查（本机，2026-08-31 实测）

| 目标 | 状态 | 解法 |
|---|---|---|
| pypi.org | 被墙 | 阿里云镜像（UV_DEFAULT_INDEX / PIP_INDEX_URL） |
| docker.io | 被墙（auth 端点 TLS 超时） | daocloud 镜像源拉取后 retag |
| ghcr.io :latest | 间歇 EOF | pin 本地已有 tag `:20250624` |
| astral.sh (uv) | 被墙 | 宿主 uv 二进制挂载进容器 |
