"""Catalog manifest generation, config pointer, and legacy-key migration."""

import json

import pytest

from hermes_openrouter_free_rotator import configsync
from hermes_openrouter_free_rotator.configsync import (
    CATALOG_MAX_IDS,
    CatalogPlan,
    _legacy_ids,
    _mutate_catalog,
    _prune_empty,
    build_catalog_plan,
    build_manifest,
    read_declared_ids,
)
from hermes_openrouter_free_rotator.selection import Candidate


def _cand(model_id):
    return Candidate(id=model_id, base_slug=model_id.removesuffix(":free"))


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Point the manifest at a temp dir and pin the upstream curated list."""
    monkeypatch.setattr(configsync, "manifest_path", lambda: tmp_path / "model-catalog.json")
    monkeypatch.setattr(
        configsync, "manifest_url", lambda: (tmp_path / "model-catalog.json").as_uri()
    )
    monkeypatch.setattr(
        configsync, "upstream_curated",
        lambda: [{"id": "anthropic/claude-opus-5", "description": ""}],
    )
    return tmp_path


# ---- read_declared_ids: contract with hermes_cli.model_switch ------------

def test_read_declared_ids_shapes():
    assert read_declared_ids("a:free") == ["a:free"]
    assert read_declared_ids(["a", "b"]) == ["a", "b"]
    assert read_declared_ids([{"id": "a"}, {"name": "b"}]) == ["a", "b"]
    assert read_declared_ids({"a": {"context_length": 8192}, "b": {}}) == ["a", "b"]
    assert read_declared_ids(None) == []
    assert read_declared_ids(123) == []
    assert read_declared_ids([None, "", "  ", "a"]) == ["a"]
    assert read_declared_ids(["a", "A"]) == ["a"]


# ---- build_manifest ------------------------------------------------------

def test_manifest_puts_free_models_first():
    m = build_manifest(["a:free"], [{"id": "paid/x", "description": "recommended"}])
    ids = [x["id"] for x in m["providers"]["openrouter"]["models"]]
    assert ids == ["a:free", "paid/x"]
    assert m["version"] == 1


def test_manifest_keeps_curated_models_so_paid_ones_survive():
    curated = [{"id": f"paid/{i}", "description": ""} for i in range(5)]
    m = build_manifest(["a:free"], curated)
    ids = [x["id"] for x in m["providers"]["openrouter"]["models"]]
    assert len(ids) == 6 and ids[1:] == [c["id"] for c in curated]


def test_manifest_dedupes_a_free_model_that_is_also_curated():
    m = build_manifest(["a:free"], [{"id": "A:FREE", "description": "x"}])
    ids = [x["id"] for x in m["providers"]["openrouter"]["models"]]
    assert ids == ["a:free"]


# ---- removal of vanished models -----------------------------------------

def test_vanished_free_model_disappears_from_the_manifest(sandbox):
    state = {"managed_catalog": []}
    first = build_catalog_plan({}, [_cand("a:free"), _cand("gone:free")], state)
    configsync.write_manifest(first)
    state["managed_catalog"] = first.managed_ids

    # Next run: OpenRouter no longer offers gone:free, so it isn't a candidate.
    second = build_catalog_plan({}, [_cand("a:free")], state)
    assert second.changed
    assert "gone:free" not in second.new_ids
    assert second.removed_ids == ["gone:free"]


def test_manifest_never_accumulates_across_runs(sandbox):
    # The rebuild reads upstream, never our own previous file — otherwise a
    # dropped model would linger forever.
    state = {"managed_catalog": []}
    plan = build_catalog_plan({}, [_cand("a:free"), _cand("b:free")], state)
    configsync.write_manifest(plan)
    state["managed_catalog"] = plan.managed_ids

    plan2 = build_catalog_plan({}, [_cand("a:free")], state)
    configsync.write_manifest(plan2)
    on_disk = json.loads((sandbox / "model-catalog.json").read_text())
    ids = [m["id"] for m in on_disk["providers"]["openrouter"]["models"]]
    assert ids == ["a:free", "anthropic/claude-opus-5"]


def test_unchanged_catalog_is_not_a_change(sandbox):
    cfg = {"model_catalog": {"providers": {"openrouter": {"url": configsync.manifest_url()}}}}
    state = {"managed_catalog": []}
    plan = build_catalog_plan(cfg, [_cand("a:free")], state)
    configsync.write_manifest(plan)
    again = build_catalog_plan(cfg, [_cand("a:free")], state)
    assert again.changed is False


def test_truncates_at_catalog_max_ids(sandbox):
    cands = [_cand(f"m{i}:free") for i in range(CATALOG_MAX_IDS + 10)]
    plan = build_catalog_plan({}, cands, {})
    assert len(plan.managed_ids) == CATALOG_MAX_IDS


def test_plan_still_changed_when_provider_is_not_openrouter(sandbox):
    cfg = {"model": {"provider": "anthropic"}}
    plan = build_catalog_plan(cfg, [_cand("a:free")], {})
    assert plan.changed and plan.managed_ids == ["a:free"]


# ---- config pointer ------------------------------------------------------

def test_mutate_sets_the_url_pointer(sandbox):
    cfg = {}
    plan = build_catalog_plan(cfg, [_cand("a:free")], {})
    _mutate_catalog(plan, cfg)
    assert cfg["model_catalog"]["providers"]["openrouter"]["url"] == configsync.manifest_url()


def test_mutate_preserves_sibling_model_catalog_keys(sandbox):
    cfg = {"model_catalog": {"enabled": True, "ttl_hours": 1}}
    plan = build_catalog_plan(cfg, [_cand("a:free")], {})
    _mutate_catalog(plan, cfg)
    assert cfg["model_catalog"]["enabled"] is True
    assert cfg["model_catalog"]["ttl_hours"] == 1


def test_remove_drops_the_pointer_and_prunes_empties(sandbox):
    cfg = {"model_catalog": {"providers": {"openrouter": {"url": configsync.manifest_url()}}}}
    plan = build_catalog_plan(cfg, [], {"managed_catalog": ["a:free"]}, remove=True)
    _mutate_catalog(plan, cfg)
    assert "model_catalog" not in cfg


def test_remove_keeps_model_catalog_when_user_set_other_keys(sandbox):
    cfg = {
        "model_catalog": {
            "enabled": True,
            "providers": {"openrouter": {"url": configsync.manifest_url()}},
        }
    }
    plan = build_catalog_plan(cfg, [], {"managed_catalog": ["a:free"]}, remove=True)
    _mutate_catalog(plan, cfg)
    assert cfg["model_catalog"] == {"enabled": True}


def test_remove_deletes_the_manifest_file(sandbox):
    plan = build_catalog_plan({}, [_cand("a:free")], {})
    configsync.write_manifest(plan)
    assert (sandbox / "model-catalog.json").exists()
    removal = build_catalog_plan({}, [], {"managed_catalog": ["a:free"]}, remove=True)
    configsync.write_manifest(removal)
    assert not (sandbox / "model-catalog.json").exists()


# ---- legacy providers.openrouter.models migration ------------------------

def test_legacy_key_is_cleaned_up_on_next_sync(sandbox):
    cfg = {"providers": {"openrouter": {"models": ["a:free", "b:free"]}}}
    state = {"managed_catalog": ["a:free", "b:free"]}
    plan = build_catalog_plan(cfg, [_cand("a:free")], state)
    assert plan.clear_legacy
    _mutate_catalog(plan, cfg)
    assert "providers" not in cfg


def test_legacy_cleanup_spares_hand_added_entries(sandbox):
    cfg = {"providers": {"openrouter": {"models": ["mine", "a:free"]}}}
    state = {"managed_catalog": ["a:free"]}
    plan = build_catalog_plan(cfg, [_cand("a:free")], state)
    assert plan.user_ids == ["mine"]
    _mutate_catalog(plan, cfg)
    assert cfg["providers"]["openrouter"]["models"] == ["mine"]


def test_legacy_cleanup_spares_sibling_provider_keys(sandbox):
    cfg = {"providers": {"openrouter": {"models": ["a:free"], "api_key": "k"}}}
    plan = build_catalog_plan(cfg, [_cand("a:free")], {"managed_catalog": ["a:free"]})
    _mutate_catalog(plan, cfg)
    assert cfg["providers"]["openrouter"] == {"api_key": "k"}


def test_untouched_legacy_key_is_not_claimed(sandbox):
    # Models the user put there with no ledger entry are not ours to remove.
    cfg = {"providers": {"openrouter": {"models": ["mine"]}}}
    plan = build_catalog_plan(cfg, [_cand("a:free")], {})
    assert plan.clear_legacy is False
    _mutate_catalog(plan, cfg)
    assert cfg["providers"]["openrouter"]["models"] == ["mine"]


def test_legacy_ids_tolerates_junk():
    assert _legacy_ids({}) == []
    assert _legacy_ids({"providers": []}) == []
    assert _legacy_ids({"providers": {"openrouter": "nope"}}) == []


def test_prune_empty_stops_at_a_non_empty_ancestor():
    cfg = {"a": {"keep": 1, "b": {"c": 2}}}
    _prune_empty(cfg, ["a", "b", "c"])
    assert cfg == {"a": {"keep": 1}}
