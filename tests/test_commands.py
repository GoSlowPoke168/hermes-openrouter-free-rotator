"""The remaining command handlers: status, list, remove-catalog, pin's write."""

import json
import types

import pytest

from hermes_openrouter_free_rotator import cli, configsync, selection


def _args(**kw):
    base = {"json": False, "dry_run": False, "force": False, "no_catalog": False}
    base.update(kw)
    return types.SimpleNamespace(**base)


class _Log:
    def info(self, *a): pass
    def warning(self, *a): pass
    def error(self, *a): pass


@pytest.fixture(autouse=True)
def _quiet_logger(monkeypatch):
    monkeypatch.setattr(cli.state_mod, "get_logger", lambda: _Log())


def _cand(model_id, tier="private", rank=1):
    c = selection.Candidate(id=model_id, base_slug=model_id.removesuffix(":free"))
    c.tier, c.rank, c.available = tier, rank, True
    c.outcome, c.in_catalog = selection.OUTCOME_CATALOG, True
    c.endpoint_provider, c.uptime_1d = "Prov", 99.0
    return c


# ---- cmd_status -----------------------------------------------------------

def test_status_reports_mode_and_pin(host, capsys):
    host.state.update(selection_mode="loose", pinned="x/y:free", pinned_at="2026-01-01")
    cli.cmd_status(_args())
    out = capsys.readouterr().out
    assert "loose" in out
    assert "x/y:free" in out and "rotation paused" in out


def test_status_says_when_nothing_is_pinned(host, capsys):
    cli.cmd_status(_args())
    out = capsys.readouterr().out
    assert "none — rotating automatically" in out
    assert "balanced" in out                     # the default mode


def test_status_surfaces_a_cleared_pin_note(host, capsys):
    host.state["pin_cleared_note"] = "gone:free withdrawn — rotation resumed"
    cli.cmd_status(_args())
    assert "rotation resumed" in capsys.readouterr().out


def test_status_json_is_machine_readable(host, capsys):
    host.state.update(selection_mode="relaxed", pinned="x/y:free")
    cli.cmd_status(_args(json=True))
    info = json.loads(capsys.readouterr().out)
    assert info["selection_mode"] == "relaxed"
    assert info["pinned"] == "x/y:free"
    assert info["current_default"] == "old/model:free"


def test_status_survives_an_unreadable_config(host, monkeypatch, capsys):
    import sys
    broken = types.ModuleType("hermes_cli.config")
    broken.load_config_readonly = lambda: (_ for _ in ()).throw(OSError("nope"))
    monkeypatch.setitem(sys.modules, "hermes_cli.config", broken)
    cli.cmd_status(_args())                       # must not raise
    assert "current default:  None" in capsys.readouterr().out


# ---- cmd_list -------------------------------------------------------------

@pytest.fixture
def listing(host, monkeypatch):
    rows = [_cand("a/one:free", rank=1), _cand("b/two:free", "logs", 2)]
    result = selection.SelectionResult(
        selected=rows[:1], candidates=list(rows), catalog=list(rows)
    )
    monkeypatch.setattr(cli, "_run_selection", lambda s, l: (result, {}))
    return host


def test_list_renders_a_table(listing, capsys):
    cli.cmd_list(_args())
    out = capsys.readouterr().out
    assert "a/one:free" in out and "Rank" in out


def test_list_json_keeps_its_contract(listing, capsys):
    cli.cmd_list(_args(json=True))
    payload = json.loads(capsys.readouterr().out)
    assert set(payload) == {"used_fallback_ranking", "models", "catalog", "ranking"}
    first = payload["models"][0]
    for key in ("rank", "id", "tier", "tools", "expires", "uptime_1d",
                "endpoint_provider", "reason"):
        assert key in first, f"{key} is part of the published --json contract"


def test_list_persists_the_snapshot_for_slash_freemodels(listing):
    cli.cmd_list(_args())
    assert listing.state["catalog"]["rows"]
    assert listing.state["catalog"]["ranking"] in {"collection", "created"}


def test_list_writes_no_config(listing):
    cli.cmd_list(_args())
    assert not listing.config_written


def test_list_exits_1_when_the_network_fails(host, monkeypatch, capsys):
    monkeypatch.setattr(
        cli, "_run_selection", lambda s, l: (_ for _ in ()).throw(RuntimeError("down"))
    )
    with pytest.raises(SystemExit) as exc:
        cli.cmd_list(_args())
    assert exc.value.code == 1
    assert "could not fetch" in capsys.readouterr().err


# ---- cmd_remove_catalog ---------------------------------------------------

def _seed_catalog(host):
    """Land a catalog the way a real sync would, so removal has something to undo."""
    plan = configsync.build_catalog_plan(host.cfg, [_cand("a/one:free")], host.state)
    configsync.apply_all(host.cfg, catalog=plan)
    host.state["managed_catalog"] = list(plan.managed_ids)
    return plan


def test_remove_catalog_undoes_the_pointer_and_manifest(host, capsys):
    _seed_catalog(host)
    assert (host.dir / "model-catalog.json").exists()
    cli.cmd_remove_catalog(_args())
    assert not (host.dir / "model-catalog.json").exists()
    assert "model_catalog" not in host.cfg
    assert host.state["managed_catalog"] == []


def test_remove_catalog_leaves_rotation_alone(host):
    _seed_catalog(host)
    host.cfg["model"]["default"] = "keep/me:free"
    cli.cmd_remove_catalog(_args())
    assert host.cfg["model"]["default"] == "keep/me:free"


def test_remove_catalog_is_a_no_op_when_nothing_is_managed(host, capsys):
    cli.cmd_remove_catalog(_args())
    assert "nothing to do" in capsys.readouterr().out
    assert not host.config_written


def test_remove_catalog_exits_1_on_an_unreadable_config(host, monkeypatch, capsys):
    import sys
    broken = types.ModuleType("hermes_cli.config")
    broken.load_config = lambda: (_ for _ in ()).throw(OSError("nope"))
    monkeypatch.setitem(sys.modules, "hermes_cli.config", broken)
    with pytest.raises(SystemExit) as exc:
        cli.cmd_remove_catalog(_args())
    assert exc.value.code == 1
    assert "could not read config.yaml" in capsys.readouterr().err


# ---- apply_pin ------------------------------------------------------------

def test_apply_pin_backs_up_before_writing(host):
    configsync.apply_pin(host.cfg, "pinned/model:free")
    assert (host.dir / "config.yaml.pre-sync.bak").exists()
    assert host.default == "pinned/model:free"


def test_apply_pin_touches_only_the_default(host):
    host.cfg["fallback_providers"] = [{"provider": "google", "model": "g"}]
    before = list(host.cfg["fallback_providers"])
    configsync.apply_pin(host.cfg, "pinned/model:free")
    assert host.cfg["fallback_providers"] == before
