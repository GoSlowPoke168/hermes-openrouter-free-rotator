"""state.json shape — catalog ledger + snapshot, and v1 back-fill."""

import json

from hermes_openrouter_free_rotator import state as state_mod


def test_default_state_has_catalog_keys():
    state = state_mod.default_state()
    assert state["managed_catalog"] == []
    assert state["managed_catalog_written"] is None
    assert state["catalog"] == {
        "generated": None, "ranking": None, "warning": None, "rows": []
    }


def test_load_state_backfills_new_keys_from_v1_file(tmp_path, monkeypatch):
    # A state.json written by v1 must gain the catalog keys without a
    # migration step — load_state merges over the defaults.
    v1 = {
        "version": 1,
        "managed_default": "a/b:free",
        "managed_fallbacks": [{"provider": "openrouter", "model": "c/d:free"}],
        "privacy_cache": {"a/b": {"tier": "private", "checked": "2026-07-17T00:00:00+00:00"}},
    }
    path = tmp_path / "state.json"
    path.write_text(json.dumps(v1), encoding="utf-8")
    monkeypatch.setattr(state_mod, "state_path", lambda: path)

    state = state_mod.load_state()
    assert state["managed_default"] == "a/b:free"          # preserved
    assert state["privacy_cache"]                           # preserved
    assert state["managed_catalog"] == []                   # back-filled
    assert state["catalog"]["rows"] == []                   # back-filled


def test_corrupt_state_falls_back_to_defaults(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    path.write_text("not json", encoding="utf-8")
    monkeypatch.setattr(state_mod, "state_path", lambda: path)
    assert state_mod.load_state() == state_mod.default_state()


def test_catalog_snapshot_round_trips_through_json():
    snapshot = {
        "generated": "2026-08-08T00:01:03+00:00",
        "ranking": "collection",
        "warning": None,
        "rows": [{"rank": 1, "id": "a/b:free", "tier": "private", "in_catalog": True}],
    }
    state = state_mod.default_state()
    state["catalog"] = snapshot
    assert json.loads(json.dumps(state))["catalog"] == snapshot
