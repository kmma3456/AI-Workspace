"""Claude tool-use 루프. 도구 호출이 끝나고 남은 텍스트가 고객에게 보낼 답장이 된다."""

import json
import logging
from dataclasses import dataclass

from anthropic import AsyncAnthropic

from app.agent.tools import TOOLS, ToolExecutor

logger = logging.getLogger(__name__)

MAX_STEPS = 8
NO_REPLY = "[NO_REPLY]"


@dataclass
class AgentResult:
    reply: str | None
    steps: int


def to_claude_messages(history: list[dict]) -> list[dict]:
    """같은 역할이 연속되면 합치고, 첫 메시지는 user가 되도록 맞춘다 (API 제약)."""
    merged: list[dict] = []
    for item in history:
        if merged and merged[-1]["role"] == item["role"]:
            merged[-1]["content"] += "\n" + item["content"]
        else:
            merged.append({"role": item["role"], "content": item["content"]})
    if merged and merged[0]["role"] != "user":
        merged.insert(0, {"role": "user", "content": "(이전 대화 시작)"})
    return merged


async def run_agent(
    client: AsyncAnthropic,
    *,
    model: str,
    max_tokens: int,
    system: str,
    messages: list[dict],
    executor: ToolExecutor,
) -> AgentResult:
    messages = list(messages)
    response = None
    for step in range(1, MAX_STEPS + 1):
        response = await client.messages.create(
            model=model, max_tokens=max_tokens, system=system, tools=TOOLS, messages=messages,
        )
        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason != "tool_use":
            break

        results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            output, is_error = await executor.execute(block.name, dict(block.input))
            logger.info("tool %s -> %s", block.name, output)
            results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": json.dumps(output, ensure_ascii=False),
                "is_error": is_error,
            })
        messages.append({"role": "user", "content": results})
    else:
        logger.warning("도구 호출 단계 초과 (%s)", executor.phone)
        return AgentResult(reply=None, steps=MAX_STEPS)

    text = "".join(b.text for b in response.content if b.type == "text").strip()
    if not text or NO_REPLY in text:
        return AgentResult(reply=None, steps=step)
    return AgentResult(reply=text, steps=step)
