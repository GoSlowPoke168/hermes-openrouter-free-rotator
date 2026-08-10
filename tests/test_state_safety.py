"""Concurrency around state.json — `list` and `sync` can overlap.

`list` spends minutes on the network between loading and saving state, and
cron fires `sync` on its own schedule, so these two really do interleave.
"""

import contextlib

import pytest

from hermes_openrouter_free_rotator import cli


@pytest.fixture
def statebox(monkeypatch):
    """Fake the lock and state.json, recording what ends up 'on disk'."""
    box = {"disk": {}, "lock": True, "saved": None}

    @contextlib.contextmanager
    def fake_lock():
        yield box["lock"]

    monkeypatch.setattr(cli.state_mod, "sync_lock", fake_lock)
    monkeypatch.setattr(cli.state_mod, "load_state", lambda: dict(box["disk"]))
    monkeypatch.setattr(cli.state_mod, "save_state", lambda s: box.update(saved=s))
    return box


class _Log:
    def info(self, *a): pass
    def warning(self, *a): pass


# ---- _persist_snapshot ----------------------------------------------------

def test_list_does_not_clobber_a_ledger_written_while_it_ran():
    """The regression this function exists for.

    `list` holds a state copy from before the sync ran; saving it wholesale
    would roll back the ownership ledgers and orphan the entries sync had just
    written into config.yaml.
    """
    box = {}

    @contextlib.contextmanager
    def fake_lock():
        yield True

    # What sync wrote while `list` was busy on the network.
    disk = {
        "managed_default": "new/model:free",
        "managed_fallbacks": [{"provider": "openrouter", "model": "new/fb:free"}],
        "managed_catalog": ["new/model:free"],
        "pinned": "new/model:free",
        "privacy_cache": {"old": {}},
        "catalog": {"rows": ["stale"]},
    }
    # What `list` loaded before that, plus its own fresh work.
    stale = {
        "managed_default": "old/model:free",
        "managed_fallbacks": [],
        "managed_catalog": [],
        "pinned": None,
        "privacy_cache": {"fresh": {}},
        "catalog": {"rows": ["fresh"]},
    }

    import types
    fake_state = types.SimpleNamespace(
        sync_lock=fake_lock,
        load_state=lambda: dict(disk),
        save_state=lambda s: box.update(saved=s),
    )
    orig = cli.state_mod
    cli.state_mod = fake_state
    try:
        cli._persist_snapshot(stale, _Log())
    finally:
        cli.state_mod = orig

    saved = box["saved"]
    # Sync's ledgers survive untouched...
    assert saved["managed_default"] == "new/model:free"
    assert saved["managed_catalog"] == ["new/model:free"]
    assert saved["pinned"] == "new/model:free"
    # ...while the two keys `list` owns are the fresh ones.
    assert saved["privacy_cache"] == {"fresh": {}}
    assert saved["catalog"] == {"rows": ["fresh"]}


def test_list_skips_persisting_while_a_sync_holds_the_lock(statebox):
    statebox["lock"] = False
    cli._persist_snapshot({"catalog": {"rows": ["x"]}}, _Log())
    assert statebox["saved"] is None      # nothing written at all


def test_list_persists_normally_when_uncontended(statebox):
    statebox["disk"] = {"managed_default": "keep:free"}
    cli._persist_snapshot({"privacy_cache": {"a": {}}, "catalog": {"rows": []}}, _Log())
    assert statebox["saved"]["managed_default"] == "keep:free"
    assert statebox["saved"]["privacy_cache"] == {"a": {}}


# ---- reading never blocks -------------------------------------------------

def test_reading_the_mode_works_while_a_sync_holds_the_lock(statebox):
    # Reporting is a pure read; a sync can hold the lock for minutes and
    # "which mode am I on?" must still answer.
    statebox["lock"] = False
    statebox["disk"] = {"selection_mode": "loose"}
    ok, msg = cli._do_mode(None)
    assert ok and "loose" in msg
    assert statebox["saved"] is None      # and it wrote nothing


def test_setting_the_mode_still_requires_the_lock(statebox):
    statebox["lock"] = False
    ok, msg = cli._do_mode("loose")
    assert not ok and "sync is running" in msg
    assert statebox["saved"] is None
