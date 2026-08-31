"""config: .env 加载 + 模型配置（provider 无关）。

.env 放项目根目录，键值对格式（支持 # 注释）。已存在的环境变量优先，
即 .env 只兜底、不覆盖 shell 导出。三个键：

  LLM_API_KEY    模型 API key
  LLM_BASE_URL   OpenAI 兼容 endpoint（不含 /chat/completions，SDK 会自动拼接）
  LLM_MODEL      模型 id
"""

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_env(path: Path | None = None) -> dict[str, str]:
    """解析 .env 并注入 os.environ（不覆盖已有变量）。返回本次加载的键值。"""
    env_path = path or PROJECT_ROOT / ".env"
    loaded: dict[str, str] = {}
    if not env_path.exists():
        return loaded
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key or key in os.environ:
            continue
        os.environ[key] = value
        loaded[key] = value
    return loaded


load_env()

# 兼容旧变量名：LLM_API_KEY > GLM_API_KEY > DEEPSEEK_API_KEY
LLM_API_KEY = (
    os.environ.get("LLM_API_KEY")
    or os.environ.get("GLM_API_KEY")
    or os.environ.get("DEEPSEEK_API_KEY", "")
)
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")
LLM_MODEL = os.environ.get("LLM_MODEL", "glm-5.3-flash")
