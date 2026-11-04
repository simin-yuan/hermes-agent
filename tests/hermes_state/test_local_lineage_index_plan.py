"""The lineage receipt scan must be an index seek, not a JSON parse of every receipt.

Every sidebar and session-list read calls ``local_lineage_index``, and on a store that has
thousands of ``gateway.local_policy.v1:*`` receipts -- each a full config snapshot, tens of KB --
the scan parses hundreds of MB of JSON to usually find nothing (#146643). The fix is an
expression index, and an expression index only helps while the query text stays structurally
identical to the indexed expression: SQLite silently falls back to the scan otherwise. So the
plan tests below plan the statement the function *actually emits* (captured through sqlite3's
trace hook) instead of restating the query here, where a later edit could drift unnoticed.
"""
import json

import pytest

from hermes_state import SessionDB
from hermes_state_local import POLICY_PREFIX, local_lineage_index

INDEX_NAME = 'idx_state_meta_local_lineage'


@pytest.fixture
def db(tmp_path):
    database = SessionDB(db_path=tmp_path / 'state.db')
    database.create_session('owner', source='cli')
    database.create_session('head', source='cli')
    database.create_session('tail', source='cli')
    yield database
    database.close()


def _receipt(db, key, value, ended=()):
    conn = db._conn
    conn.execute('INSERT OR REPLACE INTO state_meta(key, value) VALUES(?, ?)', (key, value))
    for session_id in ended:
        conn.execute("UPDATE sessions SET end_reason='session_reset' WHERE id=?", (session_id,))
    conn.commit()


def _emitted_receipt_sql(db):
    """The ``state_meta`` statement ``local_lineage_index`` actually runs."""
    statements = []
    conn = db._conn
    conn.set_trace_callback(statements.append)
    try:
        local_lineage_index(conn)
    finally:
        conn.set_trace_callback(None)
    emitted = [sql for sql in statements if 'FROM state_meta' in sql]
    assert len(emitted) == 1, emitted
    return emitted[0]


def test_receipt_scan_is_an_index_seek(db):
    _receipt(db, POLICY_PREFIX + 'owner',
             json.dumps({'session_id': 'owner', 'lineage': ['owner', 'head']}))
    plan = [row[3] for row in db._conn.execute('EXPLAIN QUERY PLAN ' + _emitted_receipt_sql(db))]
    assert any(INDEX_NAME in step for step in plan), plan


def test_receipt_scan_does_not_walk_every_policy_key(db):
    _receipt(db, POLICY_PREFIX + 'owner',
             json.dumps({'session_id': 'owner', 'lineage': ['owner', 'head']}))
    plan = [row[3] for row in db._conn.execute('EXPLAIN QUERY PLAN ' + _emitted_receipt_sql(db))]
    # A key-range scan of the primary key visits every receipt; the index seek does not.
    assert not any('sqlite_autoindex_state_meta_1' in step for step in plan), plan


RESET_THREE = json.dumps({'session_id': 'owner', 'lineage': ['owner', 'head', 'tail']})

# Any shape that the old ``key GLOB ? AND json_valid AND json_array_length > 1`` scan excluded
# must still be excluded, and anything it included must still be included with the same
# (superseded, heads) result.
SHAPES = [
    pytest.param(
        [(POLICY_PREFIX + 'owner', json.dumps({'session_id': 'owner', 'lineage': ['owner', 'head']}),
          ('owner',))],
        ({'owner'}, {'head': ('owner', ['owner', 'head'])}), id='two-segments-owner-reset'),
    pytest.param(
        [(POLICY_PREFIX + 'owner', json.dumps({'session_id': 'owner', 'lineage': ['owner']}), ())],
        (set(), {}), id='single-segment'),
    pytest.param(
        [(POLICY_PREFIX + 'owner', RESET_THREE, ('owner',))],
        ({'owner'}, {'head': ('owner', ['owner', 'head', 'tail'])}), id='three-segments-one-reset'),
    pytest.param(
        [(POLICY_PREFIX + 'owner', RESET_THREE, ('owner', 'head'))],
        ({'owner', 'head'}, {'tail': ('owner', ['owner', 'head', 'tail'])}), id='three-segments-two-resets'),
    pytest.param(
        [(POLICY_PREFIX + 'nolineage', json.dumps({'session_id': 'owner'}), ())],
        (set(), {}), id='lineage-key-absent'),
    pytest.param(
        [(POLICY_PREFIX + 'null', json.dumps({'session_id': 'owner', 'lineage': None}), ())],
        (set(), {}), id='lineage-null'),
    pytest.param(
        [(POLICY_PREFIX + 'scalar', json.dumps({'session_id': 'owner', 'lineage': 'owner'}), ())],
        (set(), {}), id='lineage-scalar'),
    pytest.param(
        [(POLICY_PREFIX + 'object', json.dumps({'session_id': 'owner', 'lineage': {'a': 1}}), ())],
        (set(), {}), id='lineage-object'),
    pytest.param(
        [(POLICY_PREFIX + 'nullvalue', None, ())],
        (set(), {}), id='value-null'),
    pytest.param(
        [(POLICY_PREFIX + 'emptyvalue', '', ())],
        (set(), {}), id='value-empty'),
    pytest.param(
        [(POLICY_PREFIX + 'badjson', '{"session_id": "owner", "lineage": [1, 2', ())],
        (set(), {}), id='value-malformed-json'),
    pytest.param(
        [('unrelated.prefix:owner', json.dumps({'session_id': 'owner', 'lineage': ['owner', 'head']}), ())],
        (set(), {}), id='other-key-prefix'),
    pytest.param(
        [('gateway.local_policy.v1', json.dumps({'session_id': 'owner', 'lineage': ['owner', 'head']}), ())],
        (set(), {}), id='prefix-without-colon'),
    pytest.param(
        [(POLICY_PREFIX, json.dumps({'session_id': 'owner', 'lineage': ['owner', 'head']}), ('owner',))],
        ({'owner'}, {'head': ('owner', ['owner', 'head'])}), id='bare-prefix-key'),
    pytest.param(
        [(POLICY_PREFIX + 'nonstr-owner', json.dumps({'session_id': 5, 'lineage': ['owner', 'head']}), ())],
        (set(), {}), id='owner-not-a-string'),
    pytest.param(
        [(POLICY_PREFIX + 'nonstr-lineage',
          json.dumps({'session_id': 'owner', 'lineage': ['owner', 7]}), ())],
        (set(), {}), id='lineage-entry-not-a-string'),
]


@pytest.mark.parametrize('rows, expected', SHAPES)
def test_receipt_shapes_resolve_as_before(db, rows, expected):
    for key, value, ended in rows:
        _receipt(db, key, value, ended)
    superseded, heads = local_lineage_index(db._conn)
    assert (superseded, heads) == expected
