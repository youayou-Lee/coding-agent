#!/bin/bash
# tb_run.sh — Terminal-Bench 评测套壳脚本
#
# 自动处理本机环境的三个坑，让评测命令一行可跑：
#   1. unset socks 代理（socks://127.0.0.1:7890 会让 tb 的 LiteLLM 直接崩）
#   2. 加载 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL（从项目根 .env，不覆盖已有环境变量）
#   3. PYTHONPATH 指向项目根（tb 进程才能 import coding_agent.tb_adapter）
#
# 用法（与 tb 命令完全一致）：
#   ./tb_run.sh run --agent oracle --dataset-path tb/tasks --task-id analyze-access-logs
#   ./tb_run.sh run --agent-import-path coding_agent.tb_adapter:CodingAgentTB \
#       --dataset-path tb/tasks --task-id analyze-access-logs
#
# 结果输出到项目根 runs/ 目录（tb 默认写到 cwd/runs）。

set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

# 1. 代理：socks 代理会破坏 httpx/litellm
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy

# 2. 配置：解析项目根 .env（KEY=VALUE，忽略注释/空行），不覆盖已有环境变量
_seen=":"
if [ -f .env ]; then
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%$'\r'}"
    # 去空白
    line="$(printf '%s' "$line" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
    case "$line" in ""|\#*) continue ;; esac
    case "$line" in *=*) ;; *) continue ;; esac
    key="${line%%=*}"; value="${line#*=}"
    # 去掉 value 两侧引号与行内注释（value 中含 # 且带引号的情况不支持——保持简单）
    value="${value%\"}"; value="${value#\"}"; value="${value%\'}"; value="${value#\'}"
    case ":$_seen:" in *":$key:"*) continue ;; esac
    _seen="$_seen$key:"
    if [ -z "$(eval "echo \${$key+x}")" ]; then
      eval "export $key=\"\$value\"" 2>/dev/null || export "$key=$value"
    fi
  done < .env
fi
if [ -z "${LLM_API_KEY+x}" ]; then
  echo "警告: 未找到 LLM_API_KEY（项目根 .env 缺失或无该键）。模型调用会失败。" >&2
fi

# 3. Python 路径：让 tb 能 import coding_agent
export PYTHONPATH="$PROJECT_DIR${PYTHONPATH:+:$PYTHONPATH}"

echo "==> tb 工作目录: $PROJECT_DIR"
echo "==> LLM_API_KEY: ${LLM_API_KEY:+已加载}"
echo "==> LLM_MODEL: ${LLM_MODEL:-未设置}"
echo "==> LLM_BASE_URL: ${LLM_BASE_URL:-未设置}"
exec tb "$@"
