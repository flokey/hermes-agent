"""Recall windows show only rows search itself may match.

Every search route hides rewind/undo rows (active=0, compacted=0) and
display_kind='hidden' scaffolding. The context around a hit (scroll window,
discovery window, bookends) must hide them too, or an undone turn comes back
through the side door.
"""
import json

import pytest

from hermes_state import SessionDB
from tools.session_search_tool import session_search


@pytest.fixture
def db(tmp_path):
    return SessionDB(tmp_path / "state.db")


def _seed(db, turns=6):
    db.create_session("s1", source="cli")
    ids = []
    for i in range(turns):
        ids.append(db.append_message("s1", role="user", content=f"turn {i} asks about the quarterly widget report"))
        ids.append(db.append_message("s1", role="assistant", content=f"reply {i} on widgets"))
    return ids


def _contents(rows):
    return [m["content"] for m in rows]


def test_window_skips_rewound_and_hidden_neighbours(db):
    ids = _seed(db)
    scaffold = db.append_message("s1", role="user", content="model-only scaffold", display_kind="hidden")
    db.append_message("s1", role="assistant", content="after the scaffold")
    db.rewind_to_message("s1", ids[8])  # undoes turns 4 and 5 and everything after them

    view = db.get_messages_around("s1", ids[6], window=5)
    shown = {m["id"] for m in view["window"]}
    assert shown == set(ids[1:8])
    assert scaffold not in shown
    assert view["messages_after"] == 1


def test_hidden_scaffold_is_not_a_neighbour(db):
    ids = _seed(db, turns=2)
    db.append_message("s1", role="user", content="model-only scaffold", display_kind="hidden")
    view = db.get_messages_around("s1", ids[-1], window=3)
    assert "model-only scaffold" not in _contents(view["window"])


def test_anchor_is_returned_even_when_rewound(db):
    ids = _seed(db, turns=3)
    db.rewind_to_message("s1", ids[4])
    view = db.get_messages_around("s1", ids[4], window=2)
    assert ids[4] in [m["id"] for m in view["window"]]
    assert view["messages_after"] == 0


def test_bookends_skip_rewound_rows(db):
    ids = _seed(db, turns=12)
    db.rewind_to_message("s1", ids[20])
    view = db.get_anchored_view("s1", ids[8], window=2, bookend=3)
    assert max(m["id"] for m in view["bookend_end"]) < ids[20]


def test_discovery_never_returns_undone_turn_text(db):
    db.create_session("s1", source="cli")
    db.append_message("s1", role="user", content="draft the zebra launch plan")
    db.append_message("s1", role="assistant", content="here is a zebra plan")
    undone = db.append_message("s1", role="user", content="actually cancel everything, secret phrase plum")
    db.append_message("s1", role="assistant", content="cancelled")
    db.rewind_to_message("s1", undone)

    out = json.loads(session_search(query="zebra", db=db, limit=3, detail="full"))
    assert out["success"] and out["results"]
    assert "plum" not in json.dumps(out)
