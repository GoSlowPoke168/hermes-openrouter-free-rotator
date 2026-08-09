"""Catalog ordering — the list written to Hermes's /model picker."""

import datetime as dt

from hermes_openrouter_free_rotator.openrouter import (
    TIER_LOGS,
    TIER_PRIVATE,
    TIER_TRAINS,
    TIER_UNKNOWN,
    Availability,
    Privacy,
)
from hermes_openrouter_free_rotator.selection import (
    OUTCOME_UNCLASSIFIED,
    select_models,
)

TODAY = dt.date(2026, 7, 17)


def _model(slug, tools=True, expires=None, created=100):
    return {
        "id": f"{slug}:free",
        "name": slug,
        "context_length": 256000,
        "created": created,
        "expiration_date": expires,
        "supported_parameters": ["temperature"] + (["tools"] if tools else []),
    }


def _privacy_map(tiers):
    def lookup(base_slug):
        return Privacy(tier=tiers.get(base_slug, TIER_PRIVATE))

    return lookup


def _all_up(model_id):
    return Availability(ok=True, reason="uptime 99% (1d)", best_uptime_1d=99.0)


def _avail_map(down_ids=(), missing_ids=()):
    def lookup(model_id):
        if model_id in missing_ids:
            return Availability(
                ok=False,
                reason="no free endpoint currently offered",
                has_free_endpoint=False,
            )
        if model_id in down_ids:
            return Availability(
                ok=False, reason="free endpoint down (uptime 0% 1d)", best_uptime_1d=0.0
            )
        return Availability(ok=True, reason="uptime 99% (1d)", best_uptime_1d=99.0)

    return lookup


def _run(models, order, tiers=None, avail=None, **kw):
    return select_models(
        models, order, _privacy_map(tiers or {}), avail or _all_up, today=TODAY, **kw
    )


def test_catalog_orders_private_then_logs_then_trains_then_unknown():
    # Collection order is the exact reverse of tier order — tier must win.
    slugs = ["a/unknown", "b/trains", "c/logs", "d/private"]
    tiers = {
        "a/unknown": TIER_UNKNOWN,
        "b/trains": TIER_TRAINS,
        "c/logs": TIER_LOGS,
        "d/private": TIER_PRIVATE,
    }
    result = _run([_model(s) for s in slugs], slugs, tiers)
    assert result.catalog_ids == [
        "d/private:free",
        "c/logs:free",
        "b/trains:free",
        "a/unknown:free",
    ]


def test_catalog_subsorts_by_collection_rank_within_tier():
    slugs = ["a/one", "b/two", "c/three", "d/four"]
    result = _run([_model(s) for s in slugs], slugs)
    assert result.catalog_ids == [f"{s}:free" for s in slugs]


def test_catalog_excludes_non_tool_models():
    models = [_model("a/one", tools=False), _model("b/two")]
    result = _run(models, ["a/one", "b/two"])
    assert result.catalog_ids == ["b/two:free"]


def test_catalog_excludes_expiring_models():
    models = [_model("a/one", expires="2026-07-18"), _model("b/two")]
    result = _run(models, ["a/one", "b/two"])
    assert result.catalog_ids == ["b/two:free"]


def test_catalog_excludes_model_with_no_free_endpoint():
    models = [_model("a/one"), _model("b/two")]
    result = _run(models, ["a/one", "b/two"], avail=_avail_map(missing_ids={"a/one:free"}))
    assert result.catalog_ids == ["b/two:free"]


def test_down_model_is_last_in_catalog_but_present():
    # a/one is #1 and private but down; it must still be offered, just last —
    # below even an unknown-tier model, since a dead endpoint is worse.
    slugs = ["a/one", "b/two", "c/three"]
    result = _run(
        [_model(s) for s in slugs],
        slugs,
        {"c/three": TIER_UNKNOWN},
        avail=_avail_map(down_ids={"a/one:free"}),
    )
    assert result.catalog_ids == ["b/two:free", "c/three:free", "a/one:free"]


def test_down_model_still_skips_privacy_lookup():
    calls = []

    def privacy(slug):
        calls.append(slug)
        return Privacy(tier=TIER_PRIVATE)

    models = [_model("a/one"), _model("b/two")]
    result = select_models(
        models, ["a/one", "b/two"], privacy,
        _avail_map(down_ids={"a/one:free"}), today=TODAY,
    )
    assert calls == ["b/two"]
    down = next(c for c in result.catalog if c.base_slug == "a/one")
    assert down.tier is None and down.in_catalog


def test_budget_exhausted_models_appended_in_rank_order():
    slugs = [f"m/x{i}" for i in range(6)]
    result = _run([_model(s) for s in slugs], slugs, max_classifications=2)
    assert len(result.catalog) == 6
    assert result.budget_exhausted
    tail = result.catalog[2:]
    assert [c.id for c in tail] == [f"m/x{i}:free" for i in range(2, 6)]
    assert all(c.outcome == OUTCOME_UNCLASSIFIED for c in tail)


def test_catalog_ids_are_deterministic_across_runs():
    slugs = ["a/one", "b/two", "c/three", "d/four"]
    tiers = {"b/two": TIER_LOGS, "d/four": TIER_TRAINS}
    models = [_model(s) for s in slugs]
    first = _run(models, slugs, tiers).catalog_ids
    second = _run([_model(s) for s in slugs], slugs, tiers).catalog_ids
    assert first == second


def test_selected_unchanged_when_full_classification_runs():
    # Same fixture as test_logs_fill_only_when_not_enough_private: classifying
    # everything must not perturb which three models get written to config.
    slugs = ["a/one", "b/two", "c/three", "d/four"]
    tiers = {"a/one": TIER_LOGS, "c/three": TIER_TRAINS}
    result = _run([_model(s) for s in slugs], slugs, tiers)
    assert [c.id for c in result.selected] == ["b/two:free", "d/four:free", "a/one:free"]
    # ...while the trains model, previously never classified, is now listed.
    assert "c/three:free" in result.catalog_ids


def test_availability_failure_open_keeps_candidate():
    def unreachable(model_id):
        return Availability(
            ok=True, reason="availability unknown (endpoints API unreachable)", fetched=False
        )

    result = _run([_model("a/one")], ["a/one"], avail=unreachable)
    assert result.catalog_ids == ["a/one:free"]
    assert [c.id for c in result.selected] == ["a/one:free"]
