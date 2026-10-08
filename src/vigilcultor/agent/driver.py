"""执行器无头驱动：argv 组装 + stream-json 事件解析。

源码核实的 v0.19.35 契约（上游与帮助文本的分歧点已按实现处理）：
- stream-json 必须搭配 --verbose，否则直接退出；
- ``-p`` 路径忽略 ``--session-id``，会话 id 由执行器内部生成并仅在
  init 事件中报告 —— 续接必须捕获该 id 后用 ``--resume``；
- ``-p`` 引擎权限模式恒为 bypassPermissions（隔离由容器承担）；
- result 事件的 ``total_cost_usd`` 恒为 0（上游无定价表），预算跟踪以
  token 用量为准，硬上限交给执行器 ``--max-budget-usd``。
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field

STREAM_JSON_ARGS = ("--output-format", "stream-json", "--verbose")


@dataclass
class AgentRequest:
    prompt: str
    system_prompt: str
    resume_session: str | None = None
    model: str | None = None
    budget_usd: float | None = None
    timeout_sec: int = 7200


def build_argv(executor: str, request: AgentRequest) -> list[str]:
    argv = [
        executor,
        "-p",
        request.prompt,
        *STREAM_JSON_ARGS,
        "--append-system-prompt",
        request.system_prompt,
    ]
    if request.resume_session:
        argv += ["--resume", request.resume_session]
    if request.model:
        argv += ["--model", request.model]
    if request.budget_usd is not None:
        argv += ["--max-budget-usd", f"{request.budget_usd:.2f}"]
    return argv


@dataclass
class AgentEvent:
    type: str
    subtype: str | None = None
    text: str = ""
    session_id: str | None = None


@dataclass
class AgentOutcome:
    ok: bool
    final_text: str = ""
    session_id: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    turns: int = 0
    stop_reason: str | None = None
    events: list[AgentEvent] = field(default_factory=list)
    tail: str = ""


def parse_stream_line(line: str) -> AgentEvent | None:
    """单行 JSONL → 归一事件；非 JSON/无关类型返回 None。"""
    line = line.strip()
    if not line:
        return None
    try:
        payload = json.loads(line)
    except ValueError:
        return None
    etype = payload.get("type")
    if not isinstance(etype, str):
        return None
    event = AgentEvent(
        type=etype,
        subtype=payload.get("subtype"),
        session_id=payload.get("session_id"),
    )
    if etype == "assistant":
        content = payload.get("message", {}).get("content", [])
        event.text = "".join(
            block.get("text", "") for block in content if isinstance(block, dict)
        )
    elif etype == "result":
        event.text = str(payload.get("result") or "")
    return event


def parse_stream(stdout: str) -> AgentOutcome:
    """聚合整段 stream-json 输出（result 事件为最终权威态）。"""
    outcome = AgentOutcome(ok=False)
    for line in stdout.splitlines():
        event = parse_stream_line(line)
        if event is None:
            continue
        outcome.events.append(event)
        if event.session_id:
            outcome.session_id = event.session_id
        if event.type == "result":
            outcome.final_text = event.text
            outcome.ok = True  # result 事件存在即进程完成；错误态由调用方结合退出码判定
    outcome.tail = stdout[-4000:]
    return outcome


def fill_usage_from_result(outcome: AgentOutcome, stdout: str) -> None:
    """从 result 事件提取 token 用量与轮次（容错：字段缺失保持默认）。"""
    for line in reversed(stdout.splitlines()):
        try:
            payload = json.loads(line)
        except ValueError:
            continue
        if payload.get("type") != "result":
            continue
        usage = payload.get("usage") or {}
        outcome.input_tokens = int(usage.get("input_tokens") or 0)
        outcome.output_tokens = int(usage.get("output_tokens") or 0)
        outcome.turns = int(payload.get("num_turns") or 0)
        outcome.stop_reason = payload.get("stop_reason")
        return


def run_local(
    executor: str,
    request: AgentRequest,
    *,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
    runner=None,
) -> tuple[AgentOutcome, int]:
    """宿主直跑（开发/测试）；生产路径在容器内执行（M4.a5 接线）。

    runner 可注入：(argv, timeout, env, cwd) → CompletedProcess。
    """
    import os

    merged_env = dict(os.environ)
    if env:
        merged_env.update(env)
    run = runner or subprocess.run
    argv = build_argv(executor, request)
    proc = run(argv, timeout=request.timeout_sec, env=merged_env, cwd=cwd)
    outcome = parse_stream(proc.stdout if isinstance(proc.stdout, str) else "")
    fill_usage_from_result(outcome, proc.stdout if isinstance(proc.stdout, str) else "")
    if proc.returncode != 0:
        outcome.ok = False
    return outcome, proc.returncode
