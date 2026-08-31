#!/bin/bash

# Uses the host's uv binary (mounted read-only) — no uv download needed.
# pytest comes from the Aliyun PyPI mirror (direct, fast).

if [ "$PWD" = "/" ]; then
    echo "Error: No working directory set."
    exit 1
fi

uv venv .tbench-testing
source .tbench-testing/bin/activate
uv pip install --index-url https://mirrors.aliyun.com/pypi/simple/ pytest==8.4.1 pandas pyarrow
uv run pytest "$TEST_DIR/test_outputs.py" -rA
