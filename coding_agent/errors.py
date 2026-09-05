"""errors.py — 工具执行错误的确定性分类器（v0.5 Issue #3）。

设计主轴：事实与决策分离。
- 本模块只回答"这是什么错"（事实层，纯函数、确定性、不碰 LLM/shell）
- "该怎么办"（重试/换路/透传）是决策层，归策略路由（Issue #4）

分类规则按优先级命中即停：
  1. 异常类型   2. exit_code   3. 消息模式（中英双语）   4. 兜底
兜底策略（先生 2026-09-05 拍板 = 方案 A）：未知错误归 SEMANTIC 透传给模型，
不自动重试——默认动作必须选"不会造成伤害"的那个。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class ToolErrorType(Enum):
    """三类错误：决策层据此路由（transient 重试 / permanent 换路 / semantic 透传）。"""

    TRANSIENT = "transient"
    PERMANENT = "permanent"
    SEMANTIC = "semantic"


@dataclass(frozen=True)
class ErrorKind:
    """一次分类的结果：结论 + 依据。frozen = 分类是事实，不可变。"""

    type: ToolErrorType
    rule: str  # 命中的规则名，trace 回放时回答"为什么判成它"
    detail: str = ""  # 人类可读解释


# --- 第 1 层：异常类型（最可信——类型就是语义） ---

_EXC_RULES: list[tuple[type[BaseException], ToolErrorType, str]] = [
    (TimeoutError, ToolErrorType.TRANSIENT, "exc:TimeoutError"),
    (FileNotFoundError, ToolErrorType.PERMANENT, "exc:FileNotFoundError"),
    (PermissionError, ToolErrorType.PERMANENT, "exc:PermissionError"),
    (IsADirectoryError, ToolErrorType.PERMANENT, "exc:IsADirectoryError"),
    (NotADirectoryError, ToolErrorType.PERMANENT, "exc:NotADirectoryError"),
    (SyntaxError, ToolErrorType.PERMANENT, "exc:SyntaxError"),
]

# --- 第 2 层：exit_code（进程级事实；124 = timeout 命令专属，异常对象里没有） ---

_EXIT_CODE_RULES: dict[int, tuple[ToolErrorType, str]] = {
    124: (ToolErrorType.TRANSIENT, "exit:124-timeout"),  # coreutils timeout 的"到点杀"
    126: (ToolErrorType.PERMANENT, "exit:126-not-executable"),  # 有文件但不可执行
    127: (ToolErrorType.PERMANENT, "exit:127-not-found"),  # 命令不存在
}

# --- 第 3 层：消息模式（中英双语；subprocess 异常文本化后也走这里） ---

# (正则, 判定, 规则名)。转小写后匹配。
_MESSAGE_RULES: list[tuple[str, ToolErrorType, str]] = [
    # -- transient：资源/时序类，重试有意义 --
    (r"\btimeout\b|timed?\s*out|超时", ToolErrorType.TRANSIENT, "msg:timeout"),
    (r"connection\s*(refused|reset|timed?\s*out)|连接(被)?(拒绝|重置|超时)", ToolErrorType.TRANSIENT, "msg:connection"),
    (r"\b(locked|lock\s*wait|busy)\b|锁|被占用", ToolErrorType.TRANSIENT, "msg:lock"),
    (r"temporar(y|ily)|临时|稍后重试|too\s*many\s*(requests|connections)|429|rate\s*limit", ToolErrorType.TRANSIENT, "msg:temporary"),
    (r"resource\s*temporarily\s*unavailable|资源暂不可用", ToolErrorType.TRANSIENT, "msg:resource"),
    # -- permanent：事实/资格类，重试无意义 --
    (r"no\s*such\s*file|not\s*found|没有那个文件|不存在|无法找到", ToolErrorType.PERMANENT, "msg:not-found"),
    (r"permission\s*denied|权限(拒绝|不够)|access\s*denied|不允许的操作", ToolErrorType.PERMANENT, "msg:permission"),
    (r"syntax\s*error|语法错误|parse\s*error|解析失败", ToolErrorType.PERMANENT, "msg:syntax"),
    (r"command\s*not\s*found|无法将.*识别|不是内部或外部命令", ToolErrorType.PERMANENT, "msg:cmd-not-found"),
    (r"address\s*already\s*in\s*use|端口(已被)?占用", ToolErrorType.PERMANENT, "msg:port-in-use"),
]

_COMPILED = [(re.compile(p), t, r) for p, t, r in _MESSAGE_RULES]


def classify_error(
    error: BaseException | str | None,
    *,
    exit_code: int | None = None,
    stderr: str | None = None,
) -> ErrorKind:
    """把一次工具执行失败分类为 ErrorKind。

    优先级：异常类型 > exit_code > 消息模式 > 兜底 semantic（方案 A）。
    纯函数：不碰 LLM、不碰 shell、无副作用，可秒级测试。

    Args:
        error: 异常对象或错误文本（异常对象优先按类型查表，再文本化查消息模式）
        exit_code: 进程退出码（124/126/127 有专属语义）
        stderr: 命令标准错误输出（与 error 文本合并后查消息模式）
    """
    # 1) 异常类型
    if isinstance(error, BaseException):
        for exc_type, verdict, rule in _EXC_RULES:
            if isinstance(error, exc_type):
                return ErrorKind(verdict, rule, str(error)[:200])
        # 类型未命中 → 文本化后继续走 exit_code / 消息层
        text = f"{type(error).__name__}: {error}"
    else:
        text = error or ""

    # 2) exit_code（只在无更强信号时生效——124/126/127 是进程级事实）
    if exit_code in _EXIT_CODE_RULES:
        verdict, rule = _EXIT_CODE_RULES[exit_code]
        return ErrorKind(verdict, rule, f"exit_code={exit_code}")

    # 3) 消息模式（error 文本 + stderr 合并，转小写后双语正则）
    combined = f"{text}\n{stderr or ''}".lower()
    for pattern, verdict, rule in _COMPILED:
        if pattern.search(combined):
            return ErrorKind(verdict, rule, combined[:200].strip())

    # 4) 兜底（方案 A）：不归类 = 交给模型判断
    return ErrorKind(ToolErrorType.SEMANTIC, "fallback:unknown", combined[:200].strip())
