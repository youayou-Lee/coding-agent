#!/bin/bash

# Python 3.8 image with pandas preinstalled system-wide — cannot use a venv
# (it would hide the system pandas). pytest 7.x is the last py3.8-compatible
# line; pip goes through the Aliyun mirror (direct, fast).

python -m pip install --index-url https://mirrors.aliyun.com/pypi/simple/ "pytest>=7.0.0,<8"
python -m pytest "$TEST_DIR/test_outputs.py" -rA
