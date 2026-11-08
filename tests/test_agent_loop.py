import asyncio
from types import SimpleNamespace

import pytest

from app.agent.runner import run_agent, to_claude_messages
from app.batching import MessageBatcher


def test_history_merging():
    history = [
        {"role": "assistant", "content": "부재중 안내"},
        {"role": "user", "content": "견적"},
        {"role": "user", "content": "문의요"},
    ]
    assert to_claude_messages(history) == [
        {"role": "user", "content": "(이전 대화 시작)"},
        {"role": "assistant", "content": "부재중 안내"},
        {"role": "user", "content": "견적\n문의요"},
    ]


class FakeClaude:
    def __init__(self, responses):
        self._responses = iter(responses)
        self.calls = []
        self.messages = SimpleNamespace(create=self._create)

    async def _create(self, **kwargs):
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})
        return next(self._responses)


class FakeExecutor:
    phone = "01012345678"

    def __init__(self):
        self.calls = []

    async def execute(self, name, args):
        self.calls.append((name, args))
        return {"ok": True}, False


def _tool_use(name, args):
    return SimpleNamespace(type="tool_use", id=f"tu_{name}", name=name, input=args)


def _text(value):
    return SimpleNamespace(type="text", text=value)


@pytest.mark.asyncio
async def test_tool_loop_runs_tools_then_returns_text():
    client = FakeClaude([
        SimpleNamespace(stop_reason="tool_use", content=[_tool_use("classify_inquiry", {"category": "견적문의", "summary": "s"})]),
        SimpleNamespace(stop_reason="end_turn", content=[_text("어떤 서비스를 원하시나요?")]),
    ])
    executor = FakeExecutor()
    result = await run_agent(client, model="m", max_tokens=100, system="s",
                             messages=[{"role": "user", "content": "견적 문의"}], executor=executor)
    assert result.reply == "어떤 서비스를 원하시나요?"
    assert executor.calls == [("classify_inquiry", {"category": "견적문의", "summary": "s"})]
    tool_result = client.calls[1]["messages"][-1]["content"][0]
    assert tool_result["type"] == "tool_result" and tool_result["tool_use_id"] == "tu_classify_inquiry"


@pytest.mark.asyncio
async def test_no_reply_token():
    client = FakeClaude([SimpleNamespace(stop_reason="end_turn", content=[_text("[NO_REPLY]")])])
    result = await run_agent(client, model="m", max_tokens=100, system="s",
                             messages=[{"role": "user", "content": "네 감사합니다"}], executor=FakeExecutor())
    assert result.reply is None


@pytest.mark.asyncio
async def test_batcher_merges_messages():
    received = []

    async def handler(phone, text):
        received.append((phone, text))

    batcher = MessageBatcher(0.05, handler)
    batcher.add("010", "안녕하세요")
    batcher.add("010", "견적 문의요")
    await asyncio.sleep(0.15)
    assert received == [("010", "안녕하세요\n견적 문의요")]
