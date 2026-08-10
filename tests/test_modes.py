"""Selection modes: which privacy tiers auto-selection may pick, and what wins ties.

`balanced` is the default and reproduces the pre-modes behaviour exactly —
tests/test_selection.py is the gate for that and must pass unmodified. This
file covers the other three and the rules they share.
"""

import contextlib

import pytest

from hermes_openrouter_free_rotator.openrouter import (
    TIER_LOGS,
    TIER_PRIVATE,
    TIER_TRAINS,
    TIER_UNKNOWN,
)
from hermes_openrouter_free_rotator.selection import (
    DEFAULT_MODE,
    MODES,
    resolve_mode,
    select_models,
)

from test_selection import TODAY, _all_up, _model, _privacy_map  # noqa: E402 (tests/ is on sys.path)

# Rank order is collection order. a/one is the most popular, d/four the least.
COLLECTION = ["a/one", "b/two", "c/three", "d/four"]
MODELS = [_model(s) for s in COLLECTION]


def _select(tiers, mode_name, want=3):
    return select_models(
        MODELS,
        COLLECTION,
        _privacy_map(tiers),
        _all_up,
        today=TODAY,
        want=want,
        mode=MODES[mode_name],
    )


def _ids(result):
    return [c.id for c in result.selected]


# A logs model at rank #1, a private one at rank #2 — the case that separates
# `balanced` (privacy wins) from `relaxed` (rank wins).
LOGS_ON_TOP = {
    "a/one": TIER_LOGS,
    "b/two": TIER_PRIVATE,
    "c/three": TIER_TRAINS,
    "d/four": TIER_PRIVATE,
}


# ---- resolve_mode ---------------------------------------------------------

def test_resolve_mode_falls_back_to_the_default():
    # The name comes from state.json; a typo must not take rotation down.
    assert resolve_mode(None).name == DEFAULT_MODE
    assert resolve_mode("").name == DEFAULT_MODE
    assert resolve_mode("nonsense").name == DEFAULT_MODE
    assert resolve_mode("loose").name == "loose"


def test_default_mode_is_balanced():
    assert DEFAULT_MODE == "balanced"
    assert set(MODES) == {"strict", "balanced", "relaxed", "loose"}


# ---- strict ---------------------------------------------------------------

def test_strict_selects_only_private():
    result = _select(LOGS_ON_TOP, "strict")
    assert _ids(result) == ["b/two:free", "d/four:free"]   # both private, rank order
    assert all(c.tier == TIER_PRIVATE for c in result.selected)


def test_strict_returns_fewer_than_want_rather_than_widening():
    # The user explicitly wants no fallback rather than a logs one.
    result = _select({"a/one": TIER_PRIVATE, "b/two": TIER_LOGS,
                      "c/three": TIER_LOGS, "d/four": TIER_TRAINS}, "strict")
    assert _ids(result) == ["a/one:free"]
    assert result.ok


def test_strict_with_no_private_models_selects_nothing():
    # not ok → sync aborts and leaves config untouched, which is the safe path.
    result = _select(dict.fromkeys(COLLECTION, TIER_LOGS), "strict")
    assert result.selected == []
    assert not result.ok


# ---- balanced (the default) ----------------------------------------------

def test_balanced_puts_every_private_above_any_logs():
    result = _select(LOGS_ON_TOP, "balanced")
    # a/one is rank #1 but logs, so it lands behind both private models.
    assert _ids(result) == ["b/two:free", "d/four:free", "a/one:free"]
    assert [c.tier for c in result.selected] == [TIER_PRIVATE, TIER_PRIVATE, TIER_LOGS]


def test_balanced_never_selects_trains():
    result = _select(dict.fromkeys(COLLECTION, TIER_TRAINS), "balanced")
    assert result.selected == []


# ---- relaxed --------------------------------------------------------------

def test_relaxed_lets_a_higher_ranked_logs_model_win():
    result = _select(LOGS_ON_TOP, "relaxed")
    # Same tiers allowed as balanced, but rank decides: a/one now leads.
    assert _ids(result) == ["a/one:free", "b/two:free", "d/four:free"]
    assert result.selected[0].tier == TIER_LOGS


def test_relaxed_still_excludes_trains():
    result = _select(LOGS_ON_TOP, "relaxed")
    assert TIER_TRAINS not in [c.tier for c in result.selected]


def test_relaxed_and_balanced_pick_the_same_set_when_ranks_agree():
    # Private models already on top: the tiebreak can't matter.
    tiers = {"a/one": TIER_PRIVATE, "b/two": TIER_PRIVATE,
             "c/three": TIER_PRIVATE, "d/four": TIER_LOGS}
    assert _ids(_select(tiers, "relaxed")) == _ids(_select(tiers, "balanced"))


# ---- loose ----------------------------------------------------------------

def test_loose_lets_a_top_ranked_trains_model_become_the_default():
    result = _select(LOGS_ON_TOP, "loose")
    assert _ids(result) == ["a/one:free", "b/two:free", "c/three:free"]
    assert result.selected[2].tier == TIER_TRAINS
    assert result.selected[0].role_index == 0


def test_loose_is_pure_rank_order():
    result = _select(dict.fromkeys(COLLECTION, TIER_TRAINS), "loose")
    assert _ids(result) == ["a/one:free", "b/two:free", "c/three:free"]


# ---- rules shared by every mode ------------------------------------------

@pytest.mark.parametrize("mode_name", sorted(MODES))
def test_unknown_tier_is_never_selectable(mode_name):
    # `unknown` means the privacy scrape failed, not that a model is a kind.
    result = _select(dict.fromkeys(COLLECTION, TIER_UNKNOWN), mode_name)
    assert result.selected == []


@pytest.mark.parametrize("mode_name", sorted(MODES))
def test_catalog_is_identical_under_every_mode(mode_name):
    # A mode governs auto-selection only — the /model picker must not change.
    baseline = _select(LOGS_ON_TOP, "balanced")
    assert _select(LOGS_ON_TOP, mode_name).catalog_ids == baseline.catalog_ids


@pytest.mark.parametrize("mode_name", sorted(MODES))
def test_selected_models_are_stamped_with_roles(mode_name):
    result = _select(LOGS_ON_TOP, mode_name)
    for i, c in enumerate(result.selected):
        assert c.role_index == i
        assert c.outcome == "selected"


def test_reasons_describe_the_active_mode():
    by_id = {c.id: c for c in _select(LOGS_ON_TOP, "strict").candidates}
    assert "not selectable in strict mode" in by_id["a/one:free"].reason
    assert "not selectable in strict mode" in by_id["c/three:free"].reason

    # want=2 so the two private models fill the slots and the logs one is only
    # held — a selected candidate's reason is overwritten by the role stamp.
    by_id = {c.id: c for c in _select(LOGS_ON_TOP, "balanced", want=2).candidates}
    assert "used only if <2 private" in by_id["a/one:free"].reason

    by_id = {c.id: c for c in _select(LOGS_ON_TOP, "relaxed", want=1).candidates}
    assert by_id["b/two:free"].reason == "listed: private tier"   # no "held", rank decides


# ---- _do_mode -------------------------------------------------------------

@pytest.fixture
def modebox(monkeypatch):
    """cli._do_mode with the lock and state faked."""
    from hermes_openrouter_free_rotator import cli

    box = {"state": {}, "lock": True, "cli": cli}

    @contextlib.contextmanager
    def fake_lock():
        yield box["lock"]

    monkeypatch.setattr(cli.state_mod, "sync_lock", fake_lock)
    monkeypatch.setattr(cli.state_mod, "load_state", lambda: box["state"])
    monkeypatch.setattr(cli.state_mod, "save_state", lambda s: None)
    return box


def test_do_mode_sets_a_valid_mode(modebox):
    ok, msg = modebox["cli"]._do_mode("loose")
    assert ok and modebox["state"]["selection_mode"] == "loose"
    assert "train on your prompts" in msg      # the warning is not optional
    assert "sync" in msg                        # says it isn't applied yet


def test_do_mode_is_case_insensitive(modebox):
    ok, _ = modebox["cli"]._do_mode("RELAXED")
    assert ok and modebox["state"]["selection_mode"] == "relaxed"


def test_do_mode_rejects_an_unknown_name(modebox):
    ok, msg = modebox["cli"]._do_mode("yolo")
    assert not ok and "unknown mode 'yolo'" in msg
    assert "balanced" in msg                    # lists the valid ones
    assert "selection_mode" not in modebox["state"]


def test_bare_mode_reports_without_writing(modebox):
    modebox["state"]["selection_mode"] = "relaxed"
    ok, msg = modebox["cli"]._do_mode(None)
    assert ok and "relaxed" in msg
    assert modebox["state"]["selection_mode"] == "relaxed"   # unchanged


def test_bare_mode_reports_the_default_when_unset(modebox):
    ok, msg = modebox["cli"]._do_mode(None)
    assert ok and "balanced" in msg


def test_do_mode_backs_off_when_a_sync_holds_the_lock(modebox):
    modebox["lock"] = False
    ok, msg = modebox["cli"]._do_mode("strict")
    assert not ok and "sync is running" in msg
    assert "selection_mode" not in modebox["state"]


def test_strict_warns_about_losing_fallbacks(modebox):
    ok, msg = modebox["cli"]._do_mode("strict")
    assert ok and "fewer fallbacks" in msg
