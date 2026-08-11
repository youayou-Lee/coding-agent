#!/bin/bash
# tb_run.sh — Terminal-Bench 评测套壳脚本
#
# 自动处理本机环境的三个坑，让评测命令一行可跑：
#   1. unset socks 代理（socks://127.0.0.1:7890 会让 tb 的 LiteLLM 直接崩）
#   2. 加载 DEEPSEEK_API_KEY（从 ~/.zshrc 提取，不依赖交互 shell 环境）
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

# 2. API Key：从 ~/.zshrc 提取（若有则用之，不修改任何 shell 配置）
if [ -z "$DEEPSEEK_API_KEY" ]; then
  if [ -f "$HOME/.zshrc" ]; then
    export DEEPSEEK_API_KEY="$(sed -n 's/.*DEEPSEEK_API_KEY="\([^"]*\)".*/\1/p' "$HOME/.zshrc" | head -1)"
  fi
fi
if [ -z "$DEEPSEEK_API_KEY" ]; then
  echo "警告: 未找到 DEEPSEEK_API_KEY（~/.zshrc 中无 export）。模型调用会失败。" >&2
fi

# 3. Python 路径：让 tb 能 import coding_agent
export PYTHONPATH="$PROJECT_DIR${PYTHONPATH:+:$PYTHONPATH}"

echo "==> tb 工作目录: $PROJECT_DIR"
echo "==> DEEPSEEK_API_KEY: ${DEEPSEEK_API_KEY:+已加载}"
exec tb "$@"
