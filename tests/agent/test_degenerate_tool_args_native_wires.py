"""The whitespace-runaway cut (ToolArgsWhitespaceWatch) on the two wires with their own tool-input
accumulators: Anthropic Messages ``input_json_delta`` and Bedrock Converse ``toolUse.input``."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agent.bedrock_adapter import stream_converse_with_callbacks
from agent.error_classifier import classify_api_error
from agent.repetition_guard import DegenerateToolArgumentsError

RUNAWAY = ['{"command": "echo hi"'] + [" \n \n"] * 4000  # 16 KiB; the cut lands at 4 KiB
# 300 KiB of real content: 5,000-space runs inside a JSON string are content, not a runaway.
BIG_OK = [json.dumps({"command": "echo " + ("x" * 60 + " " * 5000) * 60})[i:i + 512]
          for i in range(0, 310_000, 512)]


def _anthropic_agent():
    from run_agent import AIAgent
    agent = AIAgent(api_key="k", base_url="https://example.com/v1", model="claude-opus-4-7", quiet_mode=True,
                    skip_context_files=True, skip_memory=True)
    agent.api_mode = "anthropic_messages"
    agent._anthropic_client = MagicMock()
    agent._create_request_anthropic_client = lambda *a, **k: agent._anthropic_client
    return agent


def _anthropic_attempt(arg_deltas, stop_reason, consumed):
    """One messages.stream() context manager; ``consumed`` counts the events the consumer pulled."""
    events = [SimpleNamespace(type="content_block_start", index=0,
                              content_block=SimpleNamespace(type="tool_use", name="terminal"))]
    events += [SimpleNamespace(type="content_block_delta", index=0,
                               delta=SimpleNamespace(type="input_json_delta", partial_json=d)) for d in arg_deltas]
    events += [SimpleNamespace(type="message_delta", delta=SimpleNamespace(stop_reason=stop_reason)),
               SimpleNamespace(type="message_stop")]

    def _iter(*_a):
        for event in events:
            consumed.append(event)
            yield event
    final = MagicMock(stop_reason=stop_reason, usage=SimpleNamespace(input_tokens=1, output_tokens=1),
                      content=[SimpleNamespace(type="tool_use", id="toolu_1", name="terminal",
                                               input=_loads_or_none("".join(arg_deltas)))])
    stream = MagicMock(get_final_message=MagicMock(return_value=final))
    stream.__iter__ = MagicMock(side_effect=_iter)
    return MagicMock(__enter__=MagicMock(return_value=stream), __exit__=MagicMock(return_value=False)), final


def _loads_or_none(text):
    try:
        return json.loads(text)
    except ValueError:
        return None


def _converse_events(arg_deltas, consumed):
    events = [{"messageStart": {"role": "assistant"}},
              {"contentBlockStart": {"contentBlockIndex": 0, "start": {"toolUse": {"toolUseId": "t1", "name": "terminal"}}}}]
    events += [{"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"toolUse": {"input": d}}}} for d in arg_deltas]
    events += [{"contentBlockStop": {"contentBlockIndex": 0}}, {"messageStop": {"stopReason": "tool_use"}}]
    for event in events:
        consumed.append(event)
        yield event


def test_whitespace_runaway_is_cut_on_the_anthropic_and_bedrock_live_streams(monkeypatch):
    # Anthropic: the live loop aborts the runaway mid-stream and its stream retry reopens the request.
    agent, runaway_seen = _anthropic_agent(), []
    runaway_cm, _ = _anthropic_attempt(RUNAWAY, "max_tokens", runaway_seen)
    clean_cm, clean_final = _anthropic_attempt(['{"command": "echo hi"}'], "tool_use", [])
    agent._anthropic_client.messages.stream = MagicMock(side_effect=[runaway_cm, clean_cm])
    assert agent._interruptible_streaming_api_call({"model": "claude-opus-4-7"}) is clean_final
    assert len(runaway_seen) < len(RUNAWAY) // 2

    # Bedrock Converse: the live consumer raises mid-stream; the turn loop retries it (no in-call retry).
    import agent.chat_completion_helpers as cch
    agent, converse_seen = _anthropic_agent(), []
    agent.api_mode = "bedrock_converse"
    monkeypatch.setattr(cch, "_bedrock_converse_call", lambda *a, **k: _converse_events(RUNAWAY, converse_seen))
    with pytest.raises(DegenerateToolArgumentsError) as raised:
        agent._interruptible_streaming_api_call({"modelId": "amazon.nova-pro-v1:0"})
    assert len(converse_seen) < len(RUNAWAY) // 2
    assert classify_api_error(raised.value, provider="bedrock", model="amazon.nova-pro-v1:0").retryable


def test_large_legitimate_tool_input_and_bedrock_replay_are_untouched():
    agent = _anthropic_agent()
    cm, final = _anthropic_attempt(BIG_OK, "tool_use", [])
    agent._anthropic_client.messages.stream = MagicMock(return_value=cm)
    assert agent._interruptible_streaming_api_call({"model": "claude-opus-4-7"}) is final
    live = stream_converse_with_callbacks({"stream": _converse_events(BIG_OK, [])}, abort_degenerate_args=True)
    assert json.loads(live.choices[0].message.tool_calls[0].function.arguments) == json.loads("".join(BIG_OK))
    # Relay's finalizer replays the observed events without the flag and never raises.
    replay = stream_converse_with_callbacks({"stream": _converse_events(RUNAWAY, [])})
    assert replay.choices[0].message.tool_calls[0].function.name == "terminal"
