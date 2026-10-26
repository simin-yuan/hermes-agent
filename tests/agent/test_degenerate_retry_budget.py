"""A degenerate tool-call stream spends one reconnect, and says what happened (#144745 follow-up).

Both tests are behaviour contracts: they drive the stream-error entry point, the detector, and the
exhausted-turn copy, and assert what the user is told and how many reconnects a turn costs. No
source is read, and no symbol added by the fix is imported, so on the base each one fails on its
assertion rather than at import.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agent import chat_completion_helpers as cch
from agent.repetition_guard import (
    TOOL_ARGS_WHITESPACE_LIMIT,
    DegenerateToolArgumentsError,
    ToolArgsWhitespaceWatch,
)


def _bare_call(agent):
    call = object.__new__(cch._StreamingCall)
    call.agent = agent
    call.api_kwargs = {}
    call.result = {}
    call.clients = SimpleNamespace(diag={}, close_once=MagicMock())
    call._request_cancelled = {"value": False}
    call.deltas_were_sent = {"yes": False}
    call.first_delta_fired = {"done": False}
    call.provider_tool_in_flight = {"yes": False}
    call._cancel_current_stream_attempt = MagicMock()
    call.last_chunk_time = {"t": 0.0}
    return call


def _agent():
    return SimpleNamespace(
        _interrupt_requested=False,
        _stream_options_unsupported=False,
        _emit_stream_drop=MagicMock(),
        _buffer_diagnostic_status=MagicMock(),
        _is_provider_stream_parse_error=lambda e: False,
        _warning_presentation_enabled=MagicMock(return_value=False),
        _fire_stream_delta=MagicMock(),
        _reset_stream_delivery_tracking=MagicMock(),
        _log_stream_retry=MagicMock(),
        base_url="http://127.0.0.1:9/v1",
    )


def test_a_degenerate_abort_spends_one_reconnect_and_blames_no_network():
    """The abort subclasses RemoteProtocolError, so it inherits three connect attempts.

    One resend is a fresh sample, which is what usually ends a degenerate spell; the two attempts
    after it pay for streams that mostly repeat it. Spending the budget must also not append "the
    endpoint looks unreachable" — the stream connected and delivered whitespace before the cut.
    """
    call = _bare_call(_agent())
    err = DegenerateToolArgumentsError("degenerate")

    with patch.object(cch, "_wait_stream_retry_backoff", lambda agent, delay: True):
        assert call._handle_stream_error(err, 0, max_retries=2) is True
        assert call._handle_stream_error(err, 1, max_retries=2) is False

    said = " ".join(str(c.args[0]) for c in call.agent._buffer_diagnostic_status.call_args_list)
    assert "unreachable" not in said


def test_the_abort_and_the_last_line_name_the_blank_call_not_the_provider():
    """Every line the user can read must name the whitespace and what did not run.

    Two surfaces say it: the raised error the retry ladder quotes, and the copy
    ``agent.turn_recovery`` renders through ``exhausted_copy()`` once attempts run out. Quoting an
    attempt count is a trap — the layers above decide how many requests a turn really spends — and
    "temporarily unavailable" / "add a backup provider" are the wrong diagnosis for model output.
    """
    watch = ToolArgsWhitespaceWatch()
    with pytest.raises(DegenerateToolArgumentsError) as caught:
        watch.feed(" " * TOOL_ARGS_WHITESPACE_LIMIT)

    msg = str(caught.value)
    assert "arguments degenerated into whitespace" in msg
    assert "nothing was run" in msg.lower()
    assert "unreachable" not in msg.lower()
    assert str(TOOL_ARGS_WHITESPACE_LIMIT) not in msg

    from agent.turn_failure_copy import exhausted_copy

    copy = exhausted_copy(
        "degenerate_tool_args", label="OpenRouter", attempts=9,
        summary="tool-call arguments degenerated into 4096 consecutive whitespace characters",
    )
    last_line = copy.strip().splitlines()[-1]
    assert last_line == (
        "The model sent a tool call whose arguments degenerated into whitespace — nothing was run. "
        "Sending the same request again is unlikely to help: ask for a smaller step, or switch models with /model."
    )
    assert "OpenRouter" not in last_line
    assert "nothing was run" in copy
    assert "temporarily unavailable" not in copy
    assert "backup provider" not in copy
    assert "9 attempts" not in copy
