# 迭代日志 — coding-agent

## v0.5（进行中，2026-09-05 启动）— 工具层错误分类与恢复策略

主 Issue #1，拆四个子任务：#3 分类器核心 → #4 策略路由 → #5 错误预算 → #6 收官。

- **#3 ✅（2026-09-05，PR #8 → 4412494）**：`errors.py` ToolErrorType 三分类 + ErrorKind(frozen，结论+依据) + classify_error 纯函数；规则四层命中即停（异常类型 > exit_code 124/126/127 > 消息模式中英双语 > 兜底）；兕底=方案 A（先生拍板：未知归 semantic 透传，默认动作不造成伤害）；新增 17 项单测（总 32 全绿）；设计评论已留档 Issue #3

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

## v0.4（2026-08-31）— 可靠性双修：Provider 故障切换 + 多行命令

**目标**：修复 v0.2/v0.3 暴露的两个缺陷，让“可靠性”从课程概念变成工程部件。

### Part A：LLM Provider 部件（bug#2）

- 新增 `provider.py`：ProviderChain（瞬时故障 429/5xx/超时 → 指数退避重试 1s→2s；重试耗尽或 401 等配置错 → 切下一个 provider；链耗尽抛最后异常）
- `.env` 升级双 provider：`LLM_PROVIDERS=glm,deepseek`，glm 主 / deepseek 备
- `ProviderChat`：继承 OpenAICompatChat 只覆写 get_client/invoke 两个单点，协议实现全复用
- **DeepSeek key 失效插曲**：做 failover 验证时发现旧 key 401（8-11 后未用过），换新 key 后验证通过
- **live 验证**：GLM 不可达 → 自动切 DeepSeek，完整任务正常完成；provider_attempts=[glm, deepseek, deepseek] 证明“生病记忆”生效（本 run 内不再撞墙）
- 调试插曲：首次从 agno Model 抽象类自建被 6 个抽象方法拦住 → 改继承 OpenAICompatChat；
  测试假异常构造错误（APIStatusError 不收 status_code kwarg）→ TypeError 被误判非瞬时 → 修正后 12/12

### Part B：多行命令 base64 单行化（bug#1）

- **完整机制解剖**（tmux 3.4 容器复现）：TB send_keys 把含 \n 的命令逐行敲入，
  第一行立即执行 → shell 进入 PS2 续行；TB 的完成信号 "; tmux wait -S done"
  被 heredoc 正文吞掉 → 完成通知永不到达 → 死等超时（pane 铁证：`> X; tmux wait -S done`）
- `wrap_multiline()`：纯函数，含换行 → base64 编码为单行 `echo <b64> | base64 -d | bash`，
  日志记 multi_line_wrapped + 原始命令预览；5 项回归测试，17/17 全绿
- 同一 TB 时序验证：包装后 wait done 正常返回

### 收官验证：sqlite-db-truncate 重考（18:58 run）

- **resolved: True**（is_resolved=True，agent_timeout 标记为陪跑细节）——
  基线 5/6 → **6/6**，v0.4 两个部件均在真实考试中生效：
  - multi_line_wrapped 触发 2 次（python3 -c 内嵌脚本、recover.py heredoc）
  - heredoc 写脚本 + 执行 → recover.json 产出 → pytest PASSED
- 27 个事件全程可回放，无一步卡死

### 遗留

- Agno 框架自带 ModelProviderError 重试层与外层 ProviderChain 的叠加语义待研究
- agent_timeout 标记的具体含义（是否影响平台计分）待确认

**下一步（v0.5 方向）**：计划-执行分离（plan/todo）或失败恢复与工具错误分类，二选一。

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
