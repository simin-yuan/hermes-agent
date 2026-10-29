"""A /goal continuation is self-injected machinery, and must be persisted as such.

The authority path (``admit_route_wake``) already builds its wake with ``internal=True``,
so those rows carry a display kind. The remote-channel path enqueues through the adapter
FIFO with ``_synthetic_prompt_event(source, prompt)`` instead, which leaves ``internal``
False and stamps no provenance marker, so ``display_kind_for_event`` returns ``None`` and
the continuation row is indistinguishable from something the human typed.

It must stay *non-internal*: authorization and the emergency stop have to apply to a
continuation the same way they apply to a heartbeat, and ``internal=True`` would exempt it.
The heartbeat solves exactly this with a private marker, so a continuation gets one too.
"""
from __future__ import annotations


def _event(**attrs):
    from gateway.platforms.event import MessageEvent, MessageType

    event = MessageEvent(text="[Continuing: 3 steps left]", message_type=MessageType.TEXT)
    for key, value in attrs.items():
        setattr(event, key, value)
    return event


def test_goal_continuation_is_persisted_as_machinery():
    from gateway.response_filters import INTERNAL_NOTIFICATION_DISPLAY_KIND, display_kind_for_event

    event = _event()
    assert not event.internal, "a continuation must stay non-internal so authorization applies"
    event._goal_continuation = True
    assert display_kind_for_event(event) == INTERNAL_NOTIFICATION_DISPLAY_KIND


def test_heartbeat_provenance_is_unchanged():
    from gateway.response_filters import INTERNAL_NOTIFICATION_DISPLAY_KIND, display_kind_for_event

    event = _event()
    event._heartbeat_session_id = "sess-1"
    assert display_kind_for_event(event) == INTERNAL_NOTIFICATION_DISPLAY_KIND


def test_an_unmarked_row_is_still_a_human_turn():
    from gateway.response_filters import display_kind_for_event

    assert display_kind_for_event(_event()) is None
