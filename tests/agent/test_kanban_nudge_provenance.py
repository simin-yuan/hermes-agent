"""The kanban stop nudge is runtime scaffolding, not the user speaking.

``turn_stop_gates`` appends the nudge with ``role="user"`` and stamps it with its own
private flag, ``_kanban_stop_synthetic``. That flag was registered in the persistence list
(so a resumed session does not replay the nudge) but never in ``_SYNTHETIC_USER_FLAGS``,
which is what ``_is_real_user_message`` and the compression/anchor-restoration path read.
The result: a machine-authored reminder was indistinguishable from the human's own words.

These tests pin both directions — the nudge is scaffolding, and a plain user row still
counts as the user — plus the invariant that would have caught the drift.
"""
from __future__ import annotations

KANBAN_NUDGE = (
    "[System: You are a Hermes kanban worker. A plain-text reply ends the task. "
    "Use the kanban tools to move the card and to record what you did.]"
)


def _kanban_row() -> dict:
    """Shaped like the row ``turn_stop_gates`` actually appends."""
    return {
        "role": "user",
        "content": KANBAN_NUDGE,
        "_kanban_stop_synthetic": True,
        "finish_reason": "kanban_terminal_required",
    }


def test_kanban_stop_nudge_is_not_a_real_user_message():
    from agent.conversation_compression import _is_real_user_message

    assert _is_real_user_message(_kanban_row()) is False


def test_a_plain_user_row_is_still_a_real_user_message():
    from agent.conversation_compression import _is_real_user_message

    assert _is_real_user_message({"role": "user", "content": "please stop the task"}) is True


def test_scaffolding_flags_marking_user_rows_are_known_to_provenance():
    """The bug was drift between two lists, so assert the relation, not the list.

    Every flag in the persistence list that marks a *user* row must also be known to the
    provenance list, otherwise that row is read back as human intent. Two entries are
    exempt because they mark assistant-side rows rather than user rows:
    ``_thinking_prefill`` and ``_empty_terminal_sentinel``.
    """
    from agent.conversation_compression import _SYNTHETIC_USER_FLAGS
    from agent.session_persistence import _EPHEMERAL_SCAFFOLDING_FLAGS

    assistant_side = {"_thinking_prefill", "_empty_terminal_sentinel"}
    user_row_scaffolding = set(_EPHEMERAL_SCAFFOLDING_FLAGS) - assistant_side
    missing = user_row_scaffolding - set(_SYNTHETIC_USER_FLAGS)
    assert not missing, f"scaffolding flags not honoured by provenance: {sorted(missing)}"
