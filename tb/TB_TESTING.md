# Terminal-Bench 测试手册（本机）

用 Terminal-Bench 评测我们的 Coding Agent。本文档记录完整流程、本机网络适配、结果解读。

## 0. 前置条件（已就绪）

| 组件 | 状态 |
|---|---|
| tb CLI | `uv tool install terminal-bench --with agno`（--with agno 让 tb 环境能 import agno） |
| Docker | 29.2.0，daemon 运行中 |
| 任务集 | 官方：`/tmp/terminal-bench-1/original-tasks`（241 任务）；本地改造版：`/tmp/tb-tasks` |
| API Key / 模型 | 项目根 `.env`：LLM_API_KEY / LLM_BASE_URL / LLM_MODEL（当前 GLM glm-5.3-flash，OpenAI 兼容协议） |
| PYTHONPATH | 必须指向 lab 根（adapter 可导入） |

## 1. 网络适配（本机关键，缺一不可）

TB 的测试脚本默认要联网下载 uv（astral.sh，本机被墙）。**本机方案：直接挂载宿主的 uv 二进制进容器**（宿主机 ~/.local/bin/uv，Linux x86_64，容器同为 Ubuntu 24.04 x86_64，可直接运行）——测试阶段零 uv 下载，pytest 从阿里云镜像直连安装。

适配方案（任务副本 `tb/tasks/<任务>/`）：

- **docker-compose.yaml**：
  - `network_mode: host`（容器共享宿主网络）
  - `NO_PROXY=archive.ubuntu.com,...`（apt 直连快，不绕代理）
  - volume 挂载宿主 uv：`/home/you/.local/bin/uv:/usr/local/bin/uv:ro`
- **run-tests.sh**：不下载 uv，直接用挂载的 uv 建 venv + 从阿里云镜像装 pytest + 跑测试
- **Dockerfile**：只 COPY（构建零网络，秒级）

> 备选：如果你换了能快速访问 astral.sh 的代理节点，官方 run-tests.sh（下载 uv）原样可用——当时卡住是 FlClash 节点到 astral.sh 限速 ~16KB/s（15MB 二进制需 15 分钟+），不是 uv 本身的问题。挂载方案与代理快慢完全无关，更稳。

官方任务跑之前要先复制到 `tb/tasks/` 并套用上述改造。

## 2. 链路验证（oracle，不需要 LLM/API）

```bash
cd /tmp
env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u http_proxy -u https_proxy -u all_proxy \
  tb run --agent oracle --dataset-path /tmp/tb-tasks --task-id analyze-access-logs
```
预期：`Accuracy: 100.00%`（oracle 用参考解法，验证沙箱/执行/判定链路）。

## 3. 评测我们的 Coding Agent

用项目根的 `tb_run.sh`（自动 unset 代理 + 加载 .env + 设 PYTHONPATH）：

```bash
cd ~/cs/proj/coding-agent
env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u http_proxy -u https_proxy -u all_proxy \
  ./tb_run.sh run \
    --agent-import-path coding_agent.tb_adapter:CodingAgentTB \
    --dataset-path tb/tasks \
    --task-id analyze-access-logs
```
多任务：`--n-tasks 5` 或 `--task-id <glob> --task-id <glob2>`（task-id 支持多次）。

## 4. 读结果

每次运行生成一个 run 目录：`<cwd>/runs/<时间戳>/`

```
runs/<时间戳>/
├── results.json          # 汇总：accuracy / 每个 trial 的 is_resolved / failure_mode
├── run.log               # harness 流程日志
└── analyze-access-logs/
    └── <trial>/
        ├── agent-logs/events.jsonl   # ★ 我们的 RunLogger 轨迹（llm_call/tool_call/llm_response）
        ├── sessions/agent.log        # 沙箱终端交互文本
        ├── sessions/agent.cast       # asciinema 录像（回放：asciinema play）
        ├── sessions/tests.log        # 测试阶段输出（判定失败时看这里卡在哪）
        └── panes/post-test.txt       # 测试结束后的终端快照
```

**判定字段**（results.json 每个 trial）：
- `is_resolved: true` → 通过
- `failure_mode: test_timeout` → 测试超时（180s），看 tests.log 卡在哪
- `failure_mode: agent_error` → agent 侧出错
- `parser_results` → pytest 明细

**定位问题顺序**：① results.json 的 failure_mode → ② events.jsonl（我们的 agent 干了什么、哪步失败）→ ③ tests.log（判定侧）→ ④ agent.cast 录像（终端全程）。

## 5. 常见问题

| 现象 | 原因 | 处理 |
|---|---|---|
| `test_timeout` | 测试脚本联网卡住（uv/pypi 下载） | 检查 run-tests.sh 是否还在下载；确保用改造版任务 |
| `Unknown scheme for proxy URL` | 容器/CLI 读了 socks 代理 | tb 命令必须 `env -u ... all_proxy`；容器走 host + 127.0.0.1:7890 |
| `Failed to import agent` | PYTHONPATH 没设 / tb 环境缺 agno | export PYTHONPATH；确认 `uv tool install terminal-bench --with agno` |
| 镜像构建慢/卡 | Dockerfile 里有联网 RUN | 保持 Dockerfile 只 COPY，依赖挪到测试阶段 |
| `No module named pytest` | run-tests.sh 没装成功 | 确认 pip 源可用（阿里云镜像直连 200） |
| 模型调用失败 | key 没加载 | 检查项目根 `.env` 是否存在且含 LLM_API_KEY；或 shell 里直接 `export LLM_API_KEY=...`（优先生效） |

## 6. 本机网络事实（为什么这么适配）

- astral.sh（uv 下载站）：直连被墙；走 127.0.0.1:7890 代理可通但当前节点限速 ~16KB/s → **不下载 uv，改为挂载宿主 uv 二进制进容器**（同架构同 glibc，直接可跑）
- archive.ubuntu.com：容器默认网络直连快（NO_PROXY 排除后）
- pypi.org：直连被墙；**阿里云镜像**（mirrors.aliyun.com/pypi/simple/）直连快
- 清华 pypi 镜像：pytest 包 403（2026 年策略变化），弃用
- Ubuntu 24.04 pip：PEP 668 阻止系统安装 → 用 uv（挂载的宿主版）建 venv 装 pytest，天然规避
