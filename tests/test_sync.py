"""`sync` orchestration — the layer that decides whether to write your config.

Runs the real build_plan / build_catalog_plan / apply_all against the fake
host from conftest, so these cover the integration rather than the pure
functions the other suites already pin.
"""

import json
import types

import pytest

from hermes_openrouter_free_rotator import cli, selection
from hermes_openrouter_free_rotator.openrouter import (
    TIER_LOGS,
    TIER_PRIVATE,
    TIER_TRAINS,
)


def _args(**kw):
    base = {"dry_run": False, "force": False, "no_catalog": False, "json": False}
    base.update(kw)
    return types.SimpleNamespace(**base)


def _cand(model_id, tier=TIER_PRIVATE, rank=1, **kw):
    c = selection.Candidate(id=model_id, base_slug=model_id.removesuffix(":free"))
    c.tier, c.rank = tier, rank
    c.outcome = selection.OUTCOME_CATALOG
    c.in_catalog = True
    c.available = True
    for k, v in kw.items():
        setattr(c, k, v)
    return c


PICKS = ["a/one:free", "b/two:free", "c/three:free"]


def _result(selected=None, catalog=None):
    sel = [_cand(m, rank=i + 1) for i, m in enumerate(selected if selected is not None else PICKS)]
    for i, c in enumerate(sel):
        c.outcome = selection.OUTCOME_SELECTED
        c.role_index = i
    cat = catalog if catalog is not None else sel
    return selection.SelectionResult(selected=sel, candidates=list(cat), catalog=list(cat))


@pytest.fixture
def run(host, monkeypatch):
    """Drive _sync with a canned selection result; no network."""

    def go(result=None, by_id=None, args=None, raises=None):
        res = result if result is not None else _result()
        ids = by_id if by_id is not None else {
            c.id: {"id": c.id, "expiration_date": None} for c in res.candidates
        }

        def fake_selection(state, log):
            if raises:
                raise raises
            return res, ids

        monkeypatch.setattr(cli, "_run_selection", fake_selection)
        a = args or _args()
        try:
            cli._sync(a, dict(host.state), _Log())
        except SystemExit as exc:          # _fail exits 1
            return exc.code
        return 0

    return go


class _Log:
    def info(self, *a): pass
    def warning(self, *a): pass
    def error(self, *a): pass


# ---- the happy path -------------------------------------------------------

def test_sync_writes_the_new_default_and_chain(host, run):
    assert run() == 0
    assert host.config_written
    assert host.default == "a/one:free"
    assert host.chain == ["openrouter/b/two:free", "openrouter/c/three:free"]


def test_ledgers_record_exactly_what_was_applied(host, run):
    run()
    assert host.state["managed_default"] == host.default
    assert [e["model"] for e in host.state["managed_fallbacks"]] == [
        "b/two:free", "c/three:free"
    ]
    # The ledger must match the config, or a later sync can't reclaim its own
    # entries and orphans them.
    assert [f"openrouter/{e['model']}" for e in host.state["managed_fallbacks"]] == host.chain
    assert host.state["previous_default"] == "old/model:free"
    assert host.state["last_error"] is None
    assert host.state["last_sync"]


def test_hand_added_fallbacks_survive_and_stay_last(host, run):
    mine = {"provider": "google", "model": "gemini-3.1-flash-lite"}
    host.cfg["fallback_providers"] = [mine]
    run()
    assert host.cfg["fallback_providers"][-1] == mine
    assert mine["model"] not in [e["model"] for e in host.state["managed_fallbacks"]]


def test_catalog_manifest_is_written_alongside(host, run):
    run()
    manifest = json.loads((host.dir / "model-catalog.json").read_text())
    ids = [m["id"] for m in manifest["providers"]["openrouter"]["models"]]
    assert ids[:3] == PICKS                    # free models lead
    assert "paid/curated" in ids               # upstream curated preserved
    assert host.cfg["model_catalog"]["providers"]["openrouter"]["url"].startswith("file://")
    assert host.state["managed_catalog"] == PICKS


def test_a_config_backup_is_taken_before_writing(host, run):
    run()
    assert (host.dir / "config.yaml.pre-sync.bak").exists()


# ---- no-op and dry-run: must not write ------------------------------------

def test_no_change_writes_no_config(host, run):
    run()                                  # first sync applies
    host.saved_configs.clear()
    assert run() == 0                      # second is a no-op
    assert not host.config_written
    assert host.state["last_sync"]


def test_dry_run_never_touches_config(host, run, capsys):
    assert run(args=_args(dry_run=True)) == 0
    assert not host.config_written
    assert not (host.dir / "model-catalog.json").exists()
    assert "dry run" in capsys.readouterr().out
    # ...but does not claim ownership of anything either
    assert not host.state.get("managed_default")


def test_force_rewrites_an_unchanged_selection(host, run):
    run()
    host.saved_configs.clear()
    assert run(args=_args(force=True)) == 0
    assert host.config_written


# ---- failure paths: config must survive intact ----------------------------

def test_network_failure_leaves_config_untouched(host, run):
    assert run(raises=RuntimeError("boom")) == 1
    assert not host.config_written
    assert "could not fetch" in host.state["last_error"]


def test_wrong_provider_aborts_rotation_but_still_lands_the_catalog(host, run):
    host.cfg["model"]["provider"] = "anthropic"
    assert run() == 1
    assert host.default == "old/model:free"          # rotation refused
    assert (host.dir / "model-catalog.json").exists()  # picker list still useful
    assert "not 'openrouter'" in host.state["last_error"]


def test_no_qualifying_models_names_the_mode_and_the_fix(host, run):
    host.state["selection_mode"] = "strict"
    assert run(result=_result(selected=[])) == 1
    assert not host.config_written
    err = host.state["last_error"]
    assert "strict mode" in err and "hermes freemodels mode" in err


def test_loose_mode_failure_does_not_suggest_widening_further(host, run):
    host.state["selection_mode"] = "loose"
    run(result=_result(selected=[]))
    assert "try a wider" not in host.state["last_error"]


def test_a_failed_sync_clears_last_error_on_the_next_success(host, run):
    run(raises=RuntimeError("boom"))
    assert host.state["last_error"]
    run()
    assert host.state["last_error"] is None


# ---- --no-catalog ---------------------------------------------------------

def test_no_catalog_rotates_without_touching_the_picker(host, run):
    assert run(args=_args(no_catalog=True)) == 0
    assert host.default == "a/one:free"
    assert not (host.dir / "model-catalog.json").exists()
    assert "model_catalog" not in host.cfg


# ---- pin interaction ------------------------------------------------------

def test_a_pin_survives_sync(host, run):
    host.cfg["model"]["default"] = "b/two:free"
    host.state["pinned"] = "b/two:free"
    run()
    assert host.default == "b/two:free"                 # not rotated to a/one
    assert "b/two:free" not in [e["model"] for e in host.state["managed_fallbacks"]]


def test_a_withdrawn_pin_is_cleared_and_rotation_resumes(host, run):
    host.cfg["model"]["default"] = "ghost/gone:free"
    host.state["pinned"] = "ghost/gone:free"
    run()
    assert host.default == "a/one:free"
    assert host.state["pinned"] is None
    assert "no longer offered" in host.state["pin_cleared_note"]


def test_pin_state_is_untouched_by_a_dry_run(host, run):
    host.cfg["model"]["default"] = "ghost/gone:free"
    host.state["pinned"] = "ghost/gone:free"
    run(args=_args(dry_run=True))
    assert host.state["pinned"] == "ghost/gone:free"   # nothing settled yet


# ---- mode interaction -----------------------------------------------------

@pytest.mark.parametrize("stored,expected", [
    ("loose", "loose"), ("strict", "strict"), (None, "balanced"), ("junk", "balanced"),
])
def test_run_selection_passes_the_stored_mode_through(monkeypatch, stored, expected):
    # The wiring from state.json to select_models, including the fallback for
    # a name that isn't a mode.
    seen = {}
    monkeypatch.setattr(cli.openrouter, "fetch_free_models", lambda: [])
    monkeypatch.setattr(cli.openrouter, "fetch_collection_order", lambda: [])
    monkeypatch.setattr(
        selection, "select_models",
        lambda *a, **kw: seen.update(mode=kw["mode"]) or selection.SelectionResult(),
    )
    cli._run_selection({"selection_mode": stored}, _Log())
    assert seen["mode"].name == expected


# ---- json output ----------------------------------------------------------

def test_json_output_is_parseable_and_reports_the_change(host, run, capsys):
    run(args=_args(json=True))
    out = json.loads(capsys.readouterr().out)
    assert out["changed"] is True
    assert out["default"] == {"before": "old/model:free", "after": "a/one:free"}
    assert [s["id"] for s in out["selected"]] == PICKS


def test_json_output_on_a_no_op(host, run, capsys):
    run()
    capsys.readouterr()
    run(args=_args(json=True))
    out = json.loads(capsys.readouterr().out)
    assert out["changed"] is False


# ---- cmd_sync's lock ------------------------------------------------------

def test_cmd_sync_skips_when_another_sync_holds_the_lock(host, monkeypatch, capsys):
    called = []
    monkeypatch.setattr(cli, "_sync", lambda *a: called.append(a))
    monkeypatch.setattr(cli.state_mod, "get_logger", lambda: _Log())
    host.lock_free = False
    cli.cmd_sync(_args())
    assert not called
    assert "already running" in capsys.readouterr().out
