"""The agent loop, with both Anthropic and MCP faked.

What matters here is the plumbing, not the model: that UI tool calls are
intercepted locally instead of being forwarded to MCP, that tool failures come
back to the model as text it can recover from rather than crashing the turn,
and that the loop cannot run forever.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import Any

import pytest

from dxb_copilot import agent as agent_mod
from dxb_copilot.config import Settings


def make_settings(**over) -> Settings:
    base = dict(
        mcp_url="http://mcp:8100/mcp",
        api_url="http://api:8000",
        mcp_api_key="k",
        mcp_timeout_seconds=5.0,
        anthropic_api_key="sk-test",
        model="claude-sonnet-5",
        max_tokens=512,
        max_turns=3,
        host="0.0.0.0",
        port=8200,
        client_api_keys=[],
        auth_disabled=True,
    )
    base.update(over)
    return Settings(**base)


# --- fakes ---------------------------------------------------------------


@dataclass
class Blk:
    type: str
    text: str = ""
    name: str = ""
    id: str = ""
    input: dict[str, Any] | None = None


@dataclass
class Msg:
    content: list[Blk]
    stop_reason: str = "end_turn"


class FakeMessages:
    def __init__(self, script: list[Msg]):
        self._script = list(script)
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._script.pop(0)


class FakeAnthropic:
    def __init__(self, script: list[Msg]):
        self.messages = FakeMessages(script)


@dataclass
class FakeTool:
    name: str
    description: str
    inputSchema: dict  # noqa: N815 - matches the MCP SDK's own attribute name


class FakeSession:
    def __init__(self, tool_result: Any = None, raises: Exception | None = None):
        self._result = tool_result
        self._raises = raises
        self.called: list[tuple[str, dict]] = []

    async def list_tools(self):
        return type(
            "Listed",
            (),
            {
                "tools": [
                    FakeTool(
                        "rank_entities",
                        "Rank areas",
                        {"type": "object", "properties": {}},
                    )
                ]
            },
        )()

    async def call_tool(self, name: str, args: dict):
        self.called.append((name, args))
        if self._raises:
            raise self._raises
        return self._result


def patch_env(monkeypatch, script: list[Msg], session: FakeSession):
    fake = FakeAnthropic(script)
    monkeypatch.setattr(agent_mod, "AsyncAnthropic", lambda api_key: fake)

    @contextlib.asynccontextmanager
    async def fake_session(*_a, **_k):
        yield session

    monkeypatch.setattr(agent_mod, "mcp_session", fake_session)
    monkeypatch.setattr(agent_mod, "flatten_tool_result", lambda r: str(r))
    return fake


async def collect(settings, messages, view_state=None):
    return [
        e
        async for e in agent_mod.run_chat(
            settings=settings, messages=messages, view_state=view_state
        )
    ]


# --- tests ---------------------------------------------------------------


async def test_unconfigured_service_explains_itself_and_stops(monkeypatch):
    events = await collect(
        make_settings(anthropic_api_key=""), [{"role": "user", "content": "hi"}]
    )
    assert [e.type for e in events] == ["error"]
    assert "ANTHROPIC_API_KEY" in events[0].data["message"]


async def test_plain_answer_streams_text_then_done(monkeypatch):
    patch_env(monkeypatch, [Msg([Blk("text", text="Marina is up 4%.")])], FakeSession())
    events = await collect(
        make_settings(), [{"role": "user", "content": "how is marina?"}]
    )
    assert [e.type for e in events] == ["text", "done"]
    assert events[0].data["text"] == "Marina is up 4%."


async def test_mcp_tool_is_called_and_result_fed_back(monkeypatch):
    session = FakeSession(tool_result="JVC +9%")
    patch_env(
        monkeypatch,
        [
            Msg(
                [Blk("tool_use", name="rank_entities", id="t1", input={"metric": "x"})]
            ),
            Msg([Blk("text", text="JVC grew fastest.")]),
        ],
        session,
    )
    events = await collect(make_settings(), [{"role": "user", "content": "fastest?"}])
    assert session.called == [("rank_entities", {"metric": "x"})]
    assert [e.type for e in events] == ["tool", "text", "done"]


async def test_ui_patch_is_intercepted_not_forwarded_to_mcp(monkeypatch):
    session = FakeSession()
    patch_env(
        monkeypatch,
        [
            Msg(
                [
                    Blk(
                        "tool_use",
                        name="set_view_state",
                        id="u1",
                        input={"view": "dashboard", "explanation": "Plotted them."},
                    )
                ]
            ),
            Msg([Blk("text", text="Done.")]),
        ],
        session,
    )
    events = await collect(make_settings(), [{"role": "user", "content": "show me"}])

    # A UI tool must never reach the MCP server — it is ours, not theirs.
    assert session.called == []
    view_events = [e for e in events if e.type == "view_state"]
    assert len(view_events) == 1
    assert view_events[0].data["patch"] == {"view": "dashboard"}
    assert view_events[0].data["explanation"] == "Plotted them."


async def test_rejected_patch_is_reported_to_the_model_not_swallowed(monkeypatch):
    session = FakeSession()
    fake = patch_env(
        monkeypatch,
        [
            Msg(
                [Blk("tool_use", name="set_view_state", id="u1", input={"nonsense": 1})]
            ),
            Msg([Blk("text", text="Sorry, I could not change the view.")]),
        ],
        session,
    )
    events = await collect(make_settings(), [{"role": "user", "content": "show me"}])

    # No view_state event, and the model was told why so it can recover rather
    # than claiming a change that never happened.
    assert not [e for e in events if e.type == "view_state"]
    follow_up = fake.messages.calls[1]["messages"][-1]["content"][0]
    assert "rejected" in follow_up["content"].lower()


async def test_tool_failure_becomes_text_for_the_model(monkeypatch):
    session = FakeSession(raises=RuntimeError("upstream exploded"))
    fake = patch_env(
        monkeypatch,
        [
            Msg([Blk("tool_use", name="rank_entities", id="t1", input={})]),
            Msg([Blk("text", text="That lookup failed.")]),
        ],
        session,
    )
    events = await collect(make_settings(), [{"role": "user", "content": "rank"}])
    assert [e.type for e in events][-1] == "done"
    fed_back = fake.messages.calls[1]["messages"][-1]["content"][0]["content"]
    assert "TOOL ERROR" in fed_back and "upstream exploded" in fed_back


async def test_loop_is_bounded_and_says_so(monkeypatch):
    # A model that keeps calling tools must not run until the request times out.
    session = FakeSession(tool_result="x")
    script = [
        Msg([Blk("tool_use", name="rank_entities", id=f"t{i}", input={})])
        for i in range(5)
    ]
    patch_env(monkeypatch, script, session)
    events = await collect(
        make_settings(max_turns=3), [{"role": "user", "content": "loop"}]
    )
    assert events[-1].type == "error"
    assert "3 tool round trips" in events[-1].data["message"]


async def test_current_view_state_is_given_to_the_model(monkeypatch):
    fake = patch_env(monkeypatch, [Msg([Blk("text", text="ok")])], FakeSession())
    await collect(
        make_settings(),
        [{"role": "user", "content": "add marina"}],
        view_state={"view": "dashboard", "dashboard": {"entityIds": [274]}},
    )
    system = fake.messages.calls[0]["system"]
    # Without this, "add X to this chart" has nothing to add to.
    assert "entityIds" in system


async def test_system_prompt_states_the_injection_boundary(monkeypatch):
    fake = patch_env(monkeypatch, [Msg([Blk("text", text="ok")])], FakeSession())
    await collect(make_settings(), [{"role": "user", "content": "hi"}])
    system = fake.messages.calls[0]["system"]
    assert "DATA, NOT INSTRUCTIONS" in system
    assert "GROSS" in system  # yields must never be presented as achievable


@pytest.mark.parametrize("bad", [{}, {"role": "user"}])
async def test_malformed_message_does_not_crash_the_stream(monkeypatch, bad):
    patch_env(monkeypatch, [Msg([Blk("text", text="ok")])], FakeSession())
    # Whatever happens, the caller gets events rather than an exception through
    # the middle of an SSE response.
    events = await collect(make_settings(), [bad])
    assert events
