"""`/freemodels` formatting: chat-vs-terminal rendering and length limits."""

import pytest

from hermes_openrouter_free_rotator import cli


def _row(i=1, **kw):
    base = {
        "rank": i,
        "id": f"vendor/model-{i}:free",
        "tier": "private",
        "tools": True,
        "expires": None,
        "uptime_1d": 99.0,
        "endpoint_provider": "Chutes",
        "reason": "",
        "outcome": "catalog",
        "role_index": None,
        "in_catalog": True,
        "available": True,
    }
    base.update(kw)
    return base


@pytest.fixture
def state(monkeypatch):
    """A loaded state.json with `n` catalog rows, plus chat/terminal toggle."""
    data = {
        "last_sync": "2099-01-01T00:00:00+00:00",  # never stale
        "last_error": None,
        "catalog": {"generated": "2099-01-01T00:00:00+00:00", "ranking": "collection",
                    "warning": None, "rows": [_row(1)]},
    }
    monkeypatch.setattr(cli.state_mod, "load_state", lambda: data)

    def use(rows=None, chat=True):
        if rows is not None:
            data["catalog"]["rows"] = rows
        monkeypatch.setattr(cli, "_in_chat_client", lambda: chat)
        return data

    return use


def test_chat_client_wraps_the_table_in_a_code_fence(state):
    state(chat=True)
    out = cli.slash_freemodels("")
    assert "```" in out
    assert out.count("```") == 2  # opened and closed


def test_terminal_gets_no_code_fence(state):
    state(chat=False)
    assert "```" not in cli.slash_freemodels("")


def test_header_is_present_and_capitalized(state):
    state(chat=False)
    out = cli.slash_freemodels("")
    assert out.startswith("**Free OpenRouter Models**")
    assert "Available in /model: 1" in out
    assert "Last synced" in out


def test_full_arg_relabels_the_scope(state):
    state(rows=[_row(1), _row(2, in_catalog=False, outcome="skipped", tools=False)], chat=False)
    assert "All candidates: 2" in cli.slash_freemodels("full")
    # Default view hides non-catalog rows.
    assert "Available in /model: 1" in cli.slash_freemodels("")


def test_chat_output_stays_under_the_discord_limit(state):
    # Long ids + many rows: the budget must trim rather than let Discord
    # truncate mid-message and eat the closing fence.
    rows = [
        _row(i, id=f"somevendor/really-long-model-name-v{i}-instruct:free")
        for i in range(1, 61)
    ]
    state(rows=rows, chat=True)
    out = cli.slash_freemodels("")
    assert len(out) < 2000
    assert out.count("```") == 2
    assert "more — run `hermes freemodels list`" in out


def test_terminal_is_not_char_limited(state):
    rows = [_row(i) for i in range(1, 61)]
    state(rows=rows, chat=False)
    out = cli.slash_freemodels("")
    assert out.count(":free") == cli.MAX_ROWS  # only the row cap applies


def test_stale_and_error_banners(state):
    data = state(chat=False)
    data["last_sync"] = "2000-01-01T00:00:00+00:00"
    data["last_error"] = "boom"
    out = cli.slash_freemodels("")
    assert "Data is stale" in out
    assert "Last sync failed: boom" in out


def test_created_ranking_note(state):
    data = state(chat=False)
    data["catalog"]["ranking"] = "created"
    assert "Ranked by release date" in cli.slash_freemodels("")


def test_empty_state_points_at_sync(state):
    state(rows=[], chat=False)
    out = cli.slash_freemodels("")
    assert "No data yet" in out and "hermes freemodels sync" in out


def test_json_arg_returns_the_raw_snapshot(state):
    import json

    state(chat=False)
    parsed = json.loads(cli.slash_freemodels("json"))
    assert parsed["ranking"] == "collection"
