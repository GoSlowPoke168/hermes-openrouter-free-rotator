import datetime as dt

from hermes_openrouter_free_rotator.openrouter import (
    TIER_LOGS,
    TIER_PRIVATE,
    TIER_TRAINS,
    TIER_UNKNOWN,
    Availability,
    Privacy,
    assess_availability,
)
from hermes_openrouter_free_rotator.selection import is_expired, select_models

TODAY = dt.date(2026, 7, 17)


def _model(slug, tools=True, expires=None, created=100, context=256000):
    return {
        "id": f"{slug}:free",
        "name": slug,
        "context_length": context,
        "created": created,
        "expiration_date": expires,
        "supported_parameters": ["temperature"] + (["tools"] if tools else []),
    }


def _privacy_map(tiers: dict[str, str]):
    def lookup(base_slug: str) -> Privacy:
        return Privacy(tier=tiers.get(base_slug, TIER_PRIVATE))

    return lookup


def _all_up(model_id: str) -> Availability:
    return Availability(ok=True, reason="uptime 99% (1d)", best_uptime_1d=99.0)


def _avail_map(down_ids: set[str]):
    def lookup(model_id: str) -> Availability:
        if model_id in down_ids:
            return Availability(
                ok=False, reason="free endpoint down (best uptime 0% 1d)", best_uptime_1d=0.0
            )
        return Availability(ok=True, reason="uptime 99% (1d)", best_uptime_1d=99.0)

    return lookup


def test_is_expired_buffer():
    assert is_expired("2026-07-18", TODAY, buffer_days=2)      # within buffer
    assert is_expired("2026-07-17", TODAY)                     # today
    assert not is_expired("2026-07-25", TODAY, buffer_days=2)  # far enough out
    assert not is_expired(None, TODAY)                         # no expiry listed
    assert is_expired("not-a-date", TODAY)                     # unparseable → expiring


def test_happy_path_top3_private_in_collection_order():
    models = [_model("a/one"), _model("b/two"), _model("c/three"), _model("d/four")]
    result = select_models(
        models, ["c/three", "a/one", "b/two", "d/four"],
        _privacy_map({}), _all_up, today=TODAY,
    )
    assert [c.id for c in result.selected] == ["c/three:free", "a/one:free", "b/two:free"]


def test_tools_required():
    models = [_model("a/one", tools=False), _model("b/two")]
    result = select_models(models, ["a/one", "b/two"], _privacy_map({}), _all_up, today=TODAY)
    assert [c.id for c in result.selected] == ["b/two:free"]
    skipped = next(c for c in result.candidates if c.base_slug == "a/one")
    assert "tool calling" in skipped.reason


def test_expiring_model_skipped():
    models = [_model("a/one", expires="2026-07-18"), _model("b/two")]
    result = select_models(models, ["a/one", "b/two"], _privacy_map({}), _all_up, today=TODAY)
    assert [c.id for c in result.selected] == ["b/two:free"]


def test_down_model_skipped():
    models = [_model("a/one"), _model("b/two")]
    result = select_models(
        models, ["a/one", "b/two"], _privacy_map({}), _avail_map({"a/one:free"}), today=TODAY
    )
    assert [c.id for c in result.selected] == ["b/two:free"]
    skipped = next(c for c in result.candidates if c.base_slug == "a/one")
    assert "down" in skipped.reason
    assert skipped.uptime_1d == 0.0


def test_down_model_not_chosen_even_if_top_ranked_and_private():
    # a/one is #1 and private, but down → b/two (private) becomes default.
    models = [_model("a/one"), _model("b/two")]
    result = select_models(
        models, ["a/one", "b/two"], _privacy_map({}), _avail_map({"a/one:free"}), today=TODAY
    )
    assert result.selected[0].id == "b/two:free"


def test_availability_checked_before_privacy():
    # A down model must not trigger a privacy lookup (cheap check gates the scrape).
    privacy_calls = []

    def privacy(slug):
        privacy_calls.append(slug)
        return Privacy(tier=TIER_PRIVATE)

    models = [_model("a/one"), _model("b/two")]
    select_models(
        models, ["a/one", "b/two"], privacy, _avail_map({"a/one:free"}), today=TODAY
    )
    assert "a/one" not in privacy_calls
    assert "b/two" in privacy_calls


def test_trains_never_selected_even_when_only_option():
    models = [_model("a/one")]
    result = select_models(
        models, ["a/one"], _privacy_map({"a/one": TIER_TRAINS}), _all_up, today=TODAY
    )
    assert not result.ok
    assert result.selected == []


def test_unknown_tier_skipped():
    models = [_model("a/one"), _model("b/two")]
    result = select_models(
        models, ["a/one", "b/two"], _privacy_map({"a/one": TIER_UNKNOWN}), _all_up, today=TODAY
    )
    assert [c.id for c in result.selected] == ["b/two:free"]


def test_logs_fill_only_when_not_enough_private():
    models = [_model("a/one"), _model("b/two"), _model("c/three"), _model("d/four")]
    tiers = {"a/one": TIER_LOGS, "c/three": TIER_TRAINS}
    result = select_models(
        models, ["a/one", "b/two", "c/three", "d/four"], _privacy_map(tiers), _all_up, today=TODAY
    )
    # private picks (b/two, d/four) outrank the higher-ranked logs model a/one
    assert [c.id for c in result.selected] == ["b/two:free", "d/four:free", "a/one:free"]
    assert [c.tier for c in result.selected] == [TIER_PRIVATE, TIER_PRIVATE, TIER_LOGS]


def test_logs_not_used_when_three_private_exist():
    models = [_model(f"m/p{i}") for i in range(4)]
    tiers = {"m/p0": TIER_LOGS}
    result = select_models(
        models, [f"m/p{i}" for i in range(4)], _privacy_map(tiers), _all_up, today=TODAY
    )
    assert [c.tier for c in result.selected] == [TIER_PRIVATE] * 3


def test_fallback_ranking_by_created_when_no_collection_order():
    models = [
        _model("a/old", created=100),
        _model("b/new", created=300),
        _model("c/mid", created=200),
    ]
    result = select_models(models, None, _privacy_map({}), _all_up, today=TODAY)
    assert result.used_fallback_ranking
    assert [c.id for c in result.selected] == ["b/new:free", "c/mid:free", "a/old:free"]


def test_privacy_lookup_budget():
    models = [_model(f"m/x{i}") for i in range(6)]
    calls = []

    def lookup(slug):
        calls.append(slug)
        return Privacy(tier=TIER_TRAINS)  # nothing qualifies → exhaust budget

    result = select_models(
        models, [f"m/x{i}" for i in range(6)], lookup, _all_up,
        today=TODAY, max_privacy_lookups=4,
    )
    assert len(calls) == 4
    assert not result.ok


# ---- assess_availability (pure) ------------------------------------------

def test_assess_availability_healthy():
    data = {"endpoints": [{"provider_name": "Novita", "status": 0, "uptime_last_1d": 99.9}]}
    a = assess_availability(data)
    assert a.ok and a.best_uptime_1d == 99.9 and a.endpoint_provider == "Novita"


def test_assess_availability_down():
    data = {"endpoints": [{"provider_name": "Venice", "status": -5, "uptime_last_1d": 0}]}
    a = assess_availability(data)
    assert not a.ok and "down" in a.reason


def test_assess_availability_no_endpoints():
    a = assess_availability({"endpoints": []})
    assert not a.ok and "no free endpoint" in a.reason


def test_assess_availability_best_of_several():
    data = {"endpoints": [
        {"provider_name": "Down", "status": -5, "uptime_last_1d": 0},
        {"provider_name": "Up", "status": 0, "uptime_last_1d": 98.0},
    ]}
    a = assess_availability(data)
    assert a.ok and a.best_uptime_1d == 98.0


def test_assess_availability_unknown_uptime_but_status_ok():
    data = {"endpoints": [{"provider_name": "New", "status": 0, "uptime_last_1d": None}]}
    a = assess_availability(data)
    assert a.ok  # brand-new endpoint, status ok → not blocked
