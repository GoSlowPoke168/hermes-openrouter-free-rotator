"""Pinning: sync must leave model.default alone while a pin is valid.

These are the first tests over the *rotation* half of build_plan (the catalog
half lives in test_configsync_catalog.py), so they also stand up the cfg/state
fixtures that half never needed.
"""

import datetime as _dt
import json
import sys
import types

import pytest

from hermes_openrouter_free_rotator import configsync
from hermes_openrouter_free_rotator.configsync import build_plan
from hermes_openrouter_free_rotator.selection import Candidate

TODAY = _dt.date(2026, 8, 9)


@pytest.fixture(autouse=True)
def fake_host(monkeypatch):
    """Stand in for hermes_cli.fallback_config, which build_plan imports lazily."""
    mod = types.ModuleType("hermes_cli.fallback_config")
    mod.get_fallback_chain = lambda cfg: list(cfg.get("fallback_providers") or [])
    pkg = sys.modules.setdefault("hermes_cli", types.ModuleType("hermes_cli"))
    monkeypatch.setitem(sys.modules, "hermes_cli", pkg)
    monkeypatch.setitem(sys.modules, "hermes_cli.fallback_config", mod)


def _cand(model_id):
    return Candidate(id=model_id, base_slug=model_id.removesuffix(":free"))


def _cfg(default="a/one:free", chain=None):
    return {
        "model": {"provider": "openrouter", "default": default},
        "fallback_providers": list(chain or []),
    }


def _by_id(*ids, expires=None):
    """The live free-model map, keyed by id, as the OpenRouter API returns it."""
    return {i: {"id": i, "expiration_date": (expires or {}).get(i)} for i in ids}


SELECTED = ["a/one:free", "b/two:free", "c/three:free"]


def _plan(cfg, *, pinned=None, selected=None, by_id=None, state=None, force=False):
    ids = SELECTED if selected is None else selected
    return build_plan(
        cfg,
        [_cand(i) for i in ids],
        state if state is not None else {},
        force=force,
        today=TODAY,
        free_models_by_id=_by_id(*ids) if by_id is None else by_id,
        pinned=pinned,
    )


# ---- the pin is honoured --------------------------------------------------

def test_pin_becomes_the_new_default_instead_of_the_top_pick():
    plan = _plan(_cfg(default="b/two:free"), pinned="b/two:free")
    assert plan.new_default == "b/two:free"   # not a/one:free
    assert plan.pinned == "b/two:free"
    assert plan.pin_cleared is None


def test_pinned_model_is_dropped_from_its_own_fallback_chain():
    # A fallback identical to the default is a wasted retry.
    plan = _plan(_cfg(default="b/two:free"), pinned="b/two:free")
    models = [e["model"] for e in plan.managed_fallbacks]
    assert "b/two:free" not in models
    assert models == ["a/one:free", "c/three:free"]


def test_fallbacks_still_rotate_while_pinned():
    # The whole point of pinning only the default: the safety net stays live.
    cfg = _cfg(
        default="b/two:free",
        chain=[{"provider": "openrouter", "model": "stale/old:free"}],
    )
    state = {"managed_fallbacks": [{"provider": "openrouter", "model": "stale/old:free"}]}
    plan = _plan(cfg, pinned="b/two:free", state=state)
    assert plan.changed                                    # chain moved
    assert plan.new_default == "b/two:free"                # default did not
    assert "stale/old:free" not in [e["model"] for e in plan.new_chain]


def test_unmanaged_fallbacks_are_still_preserved_while_pinned():
    hand_added = {"provider": "google", "model": "gemini-3.1-flash-lite"}
    plan = _plan(_cfg(default="b/two:free", chain=[hand_added]), pinned="b/two:free")
    assert plan.new_chain[-1] == hand_added


def test_no_change_when_pinned_and_the_chain_already_matches():
    chain = [
        {"provider": "openrouter", "model": "a/one:free"},
        {"provider": "openrouter", "model": "c/three:free"},
    ]
    state = {"managed_fallbacks": chain}
    plan = _plan(_cfg(default="b/two:free", chain=chain), pinned="b/two:free", state=state)
    assert plan.changed is False


def test_pin_reason_is_recorded():
    chain = [{"provider": "openrouter", "model": "zz/old:free"}]
    state = {"managed_fallbacks": chain}
    plan = _plan(_cfg(default="b/two:free", chain=chain), pinned="b/two:free", state=state)
    assert any("pinned to b/two:free" in r for r in plan.reasons)


def test_pinning_a_model_you_are_not_on_switches_the_default():
    plan = _plan(_cfg(default="a/one:free"), pinned="c/three:free")
    assert plan.changed and plan.new_default == "c/three:free"


# ---- the pin auto-clears --------------------------------------------------

def test_withdrawn_free_pin_is_cleared_and_rotation_resumes():
    # ghost:free is pinned but no longer in the live free list.
    plan = _plan(_cfg(default="ghost/gone:free"), pinned="ghost/gone:free")
    assert plan.pinned is None
    assert plan.pin_cleared == "ghost/gone:free is no longer offered by OpenRouter"
    assert plan.new_default == "a/one:free"          # rotation took over
    assert any("pin cleared" in r for r in plan.reasons)


def test_expired_free_pin_is_cleared():
    by_id = _by_id(*SELECTED, expires={"c/three:free": "2026-08-01"})
    plan = _plan(_cfg(default="c/three:free"), pinned="c/three:free", by_id=by_id)
    assert plan.pinned is None
    assert plan.pin_cleared == "c/three:free has expired"
    assert plan.new_default == "a/one:free"


def test_a_pin_expiring_later_survives():
    by_id = _by_id(*SELECTED, expires={"c/three:free": "2099-01-01"})
    plan = _plan(_cfg(default="c/three:free"), pinned="c/three:free", by_id=by_id)
    assert plan.pinned == "c/three:free"
    assert plan.pin_cleared is None


def test_paid_pin_survives_being_absent_from_the_free_list():
    # free_models_by_id only ever holds :free ids, so a paid model's absence
    # is not evidence it was withdrawn.
    plan = _plan(_cfg(default="anthropic/claude-opus-5"), pinned="anthropic/claude-opus-5")
    assert plan.pinned == "anthropic/claude-opus-5"
    assert plan.pin_cleared is None
    assert plan.new_default == "anthropic/claude-opus-5"


# ---- no pin: behaviour is exactly as before -------------------------------

def test_without_a_pin_the_top_pick_wins():
    plan = _plan(_cfg(default="old/thing:free"))
    assert plan.new_default == "a/one:free"
    assert plan.pinned is None
    assert [e["model"] for e in plan.managed_fallbacks] == ["b/two:free", "c/three:free"]


def test_pin_does_not_bypass_the_provider_abort():
    cfg = {"model": {"provider": "anthropic", "default": "x"}}
    plan = _plan(cfg, pinned="b/two:free")
    assert plan.abort_reason and plan.pinned is None


def test_pin_does_not_bypass_the_empty_selection_abort():
    plan = _plan(_cfg(), pinned="b/two:free", selected=[])
    assert plan.abort_reason and plan.pinned is None


# ---- _do_pin / _do_unpin --------------------------------------------------

@pytest.fixture
def pinbox(monkeypatch):
    """cli._do_pin/_do_unpin with the lock, state, config and manifest faked."""
    from hermes_openrouter_free_rotator import cli

    box = {
        "state": {"catalog": {"rows": []}},
        "cfg": {"model": {"provider": "openrouter", "default": "a/one:free"}},
        "manifest": ["a/one:free", "b/two:free", "paid/x"],
        "written": None,
        "lock": True,
    }

    import contextlib

    @contextlib.contextmanager
    def fake_lock():
        yield box["lock"]

    def fake_apply_pin(cfg, model_id):
        box["written"] = model_id
        cfg.setdefault("model", {})["default"] = model_id

    host = types.ModuleType("hermes_cli.config")
    host.load_config = lambda: box["cfg"]
    monkeypatch.setitem(sys.modules, "hermes_cli.config", host)

    monkeypatch.setattr(cli.state_mod, "sync_lock", fake_lock)
    monkeypatch.setattr(cli.state_mod, "load_state", lambda: box["state"])
    monkeypatch.setattr(cli.state_mod, "save_state", lambda s: box.update(saved=s))
    monkeypatch.setattr(cli.configsync, "read_manifest_ids", lambda: box["manifest"])
    monkeypatch.setattr(cli.configsync, "apply_pin", fake_apply_pin)
    box["cli"] = cli
    return box


def test_do_pin_writes_config_and_state(pinbox):
    ok, msg = pinbox["cli"]._do_pin("b/two:free", force=False)
    assert ok and "Pinned b/two:free" in msg
    assert pinbox["written"] == "b/two:free"        # it switches, not just records
    assert pinbox["state"]["pinned"] == "b/two:free"
    assert pinbox["state"]["pinned_at"]


def test_bare_pin_keeps_the_current_default(pinbox):
    ok, _ = pinbox["cli"]._do_pin(None, force=False)
    assert ok and pinbox["state"]["pinned"] == "a/one:free"


def test_pin_rejects_an_id_outside_the_catalog(pinbox):
    ok, msg = pinbox["cli"]._do_pin("ghost/nope:free", force=False)
    assert not ok and "isn't in the /model catalog" in msg
    assert pinbox["written"] is None
    assert "pinned" not in pinbox["state"]


def test_force_pins_an_unknown_id(pinbox):
    ok, _ = pinbox["cli"]._do_pin("ghost/nope:free", force=True)
    assert ok and pinbox["state"]["pinned"] == "ghost/nope:free"


def test_pin_allows_an_unverifiable_id_when_no_manifest_exists(pinbox):
    pinbox["manifest"] = []
    ok, msg = pinbox["cli"]._do_pin("whatever:free", force=False)
    assert ok and "Couldn't verify" in msg


def test_pin_warns_about_a_training_model_without_blocking(pinbox):
    pinbox["state"]["catalog"]["rows"] = [{"id": "b/two:free", "tier": "trains"}]
    ok, msg = pinbox["cli"]._do_pin("b/two:free", force=False)
    assert ok and "trains on your prompts" in msg


def test_pin_refuses_a_non_openrouter_setup(pinbox):
    pinbox["cfg"]["model"]["provider"] = "anthropic"
    ok, msg = pinbox["cli"]._do_pin("b/two:free", force=False)
    assert not ok and "not 'openrouter'" in msg
    assert pinbox["written"] is None


def test_pin_backs_off_when_a_sync_holds_the_lock(pinbox):
    pinbox["lock"] = False
    ok, msg = pinbox["cli"]._do_pin("b/two:free", force=False)
    assert not ok and "sync is running" in msg
    assert pinbox["written"] is None


def test_pin_clears_a_stale_cleared_note(pinbox):
    pinbox["state"]["pin_cleared_note"] = "old news"
    pinbox["cli"]._do_pin("b/two:free", force=False)
    assert pinbox["state"]["pin_cleared_note"] is None


def test_unpin_clears_state_but_leaves_config_alone(pinbox):
    pinbox["state"]["pinned"] = "b/two:free"
    ok, msg = pinbox["cli"]._do_unpin()
    assert ok and "Unpinned b/two:free" in msg
    assert pinbox["state"]["pinned"] is None
    assert pinbox["written"] is None                # model.default untouched


def test_unpin_is_a_no_op_when_nothing_is_pinned(pinbox):
    ok, msg = pinbox["cli"]._do_unpin()
    assert ok and "No model is pinned" in msg


# ---- read_manifest_ids ----------------------------------------------------

def test_read_manifest_ids_returns_every_offered_id(tmp_path, monkeypatch):
    path = tmp_path / "model-catalog.json"
    path.write_text(json.dumps({
        "version": 1,
        "providers": {"openrouter": {"models": [{"id": "a:free"}, {"id": "paid/x"}]}},
    }), encoding="utf-8")
    monkeypatch.setattr(configsync, "manifest_path", lambda: path)
    assert configsync.read_manifest_ids() == ["a:free", "paid/x"]


@pytest.mark.parametrize("body", ["", "not json", '{"providers": {}}', '{"providers": []}'])
def test_read_manifest_ids_tolerates_junk(tmp_path, monkeypatch, body):
    path = tmp_path / "model-catalog.json"
    path.write_text(body, encoding="utf-8")
    monkeypatch.setattr(configsync, "manifest_path", lambda: path)
    assert configsync.read_manifest_ids() == []


def test_read_manifest_ids_returns_empty_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(configsync, "manifest_path", lambda: tmp_path / "nope.json")
    assert configsync.read_manifest_ids() == []
