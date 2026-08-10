"""Pure selection logic — no I/O, unit-testable.

Pipeline: hard filters (tool support, expiry buffer) → rank (collection
order, falling back to API metadata) → privacy classification in rank order
(via an injected lookup, so callers control caching/rate limits) → pick
*want* models according to the active `Mode`.

A mode carries two independent rules — which tiers may be selected, and
whether tier order outranks popularity — so the four are a table, not a
scale. "unknown" is selectable in none of them: it means the privacy scrape
failed, not that a model is a particular kind. See `MODES`.

Two products come out of one pass:

- ``selected`` — the 3 models the plugin writes to model.default and the
  leading fallback_providers. Privacy-gated; unchanged by the catalog work.
- ``catalog`` — *every* listable free model, ordered best→worst, which the
  caller writes to providers.openrouter.models so Hermes's /model picker can
  offer them. Includes tiers the active mode won't auto-select (they sort
  last), because the user picking one by hand is an informed choice — the
  catalog is deliberately identical under every mode.

Classification therefore runs over every ranked candidate rather than
stopping once enough private models are found — the catalog needs a tier for
each one to order and label it.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Any, Callable

from .openrouter import (
    TIER_LOGS,
    TIER_PRIVATE,
    TIER_TRAINS,
    TIER_UNKNOWN,
    Availability,
    Privacy,
)

EXPIRY_BUFFER_DAYS = 1  # skip a model once it's within 1 day of expiring
DEFAULT_WANT = 3
# Safety valve, not a tuning knob: bounds one sync's work if OpenRouter ever
# ships hundreds of free models. Overflow is catalogued-but-unclassified
# rather than dropped, so the picker list stays complete either way.
MAX_CLASSIFICATIONS = 40

# Machine-readable disposition. The renderer switches on this so no caller
# ever has to string-match the human-readable `reason`.
OUTCOME_SELECTED = "selected"          # written to config as default/fallback
OUTCOME_CATALOG = "catalog"            # classified and listable, not chosen
OUTCOME_DEGRADED = "degraded"          # endpoint exists but is down today
OUTCOME_UNCLASSIFIED = "unclassified"  # budget ran out before we got to it
OUTCOME_SKIPPED = "skipped"            # never eligible, never listed

# Catalog ordering, best first. Degraded and unclassified models sort after
# every classified one — they're listable, just not recommendable.
CATALOG_TIER_ORDER = (TIER_PRIVATE, TIER_LOGS, TIER_TRAINS, TIER_UNKNOWN)
_CATALOG_BUCKET = {tier: i for i, tier in enumerate(CATALOG_TIER_ORDER)}
BUCKET_DEGRADED = len(CATALOG_TIER_ORDER)
BUCKET_UNCLASSIFIED = BUCKET_DEGRADED + 1


@dataclass(frozen=True)
class Mode:
    """How much privacy to trade for a better-ranked model.

    Two rules vary independently, which is why these are a table rather than
    a scale: `balanced` and `relaxed` allow the same tiers and differ only in
    whether tier order beats popularity.
    """

    name: str
    tiers: tuple[str, ...]  # selectable, best-privacy first
    tier_dominant: bool     # True: every tier beats the next. False: rank decides.
    summary: str            # one line, reused by the CLI, status and README


MODES = {
    m.name: m
    for m in (
        Mode("strict", (TIER_PRIVATE,), True, "private only"),
        Mode("balanced", (TIER_PRIVATE, TIER_LOGS), True,
             "private first, logs as fallback"),
        Mode("relaxed", (TIER_PRIVATE, TIER_LOGS), False,
             "private + logs, best rank wins"),
        Mode("loose", (TIER_PRIVATE, TIER_LOGS, TIER_TRAINS), False,
             "adds models that train on prompts, best rank wins"),
    )
}
DEFAULT_MODE = "balanced"
# TIER_UNKNOWN is in no mode on purpose: it marks a failed privacy scrape, so
# selecting one would mean "auto-pick a model whose policy we couldn't read".


def resolve_mode(name: str | None) -> Mode:
    """Look up a mode by name, falling back to the default.

    Never raises — the name comes from state.json, and a typo there must not
    take rotation down.
    """
    return MODES.get(name or "", MODES[DEFAULT_MODE])


@dataclass
class Candidate:
    id: str                      # "tencent/hy3:free"
    base_slug: str               # "tencent/hy3"
    name: str = ""
    context_length: int = 0
    created: int = 0
    expiration_date: str | None = None
    supports_tools: bool = False
    rank: int | None = None      # 1-based, after ranking
    tier: str | None = None      # privacy tier once classified
    endpoint_provider: str | None = None
    uptime_1d: float | None = None   # best free-endpoint uptime %, once checked
    reason: str = ""             # human-readable selection/skip reason
    outcome: str = OUTCOME_SKIPPED   # machine-readable disposition
    available: bool | None = None    # availability verdict; None = not checked
    has_free_endpoint: bool = True   # False → OpenRouter offers no free route
    classified: bool = False         # a privacy lookup actually ran
    role_index: int | None = None    # 0 = default, 1 = fallback #1, ...
    in_catalog: bool = False         # listed in the /model picker

    @classmethod
    def from_api(cls, m: dict[str, Any]) -> "Candidate":
        model_id = str(m.get("id", ""))
        return cls(
            id=model_id,
            base_slug=model_id.removesuffix(":free"),
            name=str(m.get("name", model_id)),
            context_length=int(m.get("context_length") or 0),
            created=int(m.get("created") or 0),
            expiration_date=m.get("expiration_date") or None,
            supports_tools="tools" in (m.get("supported_parameters") or []),
        )


@dataclass
class SelectionResult:
    selected: list[Candidate] = field(default_factory=list)
    candidates: list[Candidate] = field(default_factory=list)  # all, with reasons
    catalog: list[Candidate] = field(default_factory=list)     # picker list, best first
    used_fallback_ranking: bool = False
    budget_exhausted: bool = False

    @property
    def ok(self) -> bool:
        return bool(self.selected)

    @property
    def catalog_ids(self) -> list[str]:
        return [c.id for c in self.catalog]


def _parse_date(value: str) -> _dt.date | None:
    try:
        return _dt.date.fromisoformat(value[:10])
    except (ValueError, TypeError):
        return None


def is_expired(
    expiration_date: str | None, today: _dt.date, buffer_days: int = 0
) -> bool:
    """True when the model expires on/before today + buffer_days.

    An unparseable date is treated as expiring (a model whose lifecycle we
    can't reason about shouldn't become the default).
    """
    if not expiration_date:
        return False
    expiry = _parse_date(expiration_date)
    if expiry is None:
        return True
    return expiry <= today + _dt.timedelta(days=buffer_days)


def rank_candidates(
    candidates: list[Candidate], collection_order: list[str] | None
) -> tuple[list[Candidate], bool]:
    """Order candidates best-first; returns (ranked, used_fallback_ranking)."""
    order_index = {slug: i for i, slug in enumerate(collection_order or [])}
    # Models missing from the collection page sort after listed ones, newest
    # first, largest context as the tiebreak.
    ranked = sorted(
        candidates,
        key=lambda c: (
            order_index.get(c.base_slug, len(order_index)),
            -c.created,
            -c.context_length,
        ),
    )
    for i, c in enumerate(ranked):
        c.rank = i + 1
    return ranked, not order_index


def catalog_bucket(c: Candidate) -> int:
    """Sort bucket for the picker list — lower is better."""
    if c.outcome == OUTCOME_UNCLASSIFIED:
        return BUCKET_UNCLASSIFIED
    if c.available is False:
        return BUCKET_DEGRADED
    return _CATALOG_BUCKET.get(c.tier or TIER_UNKNOWN, _CATALOG_BUCKET[TIER_UNKNOWN])


def build_catalog(ranked: list[Candidate]) -> list[Candidate]:
    """Order every listable model best→worst for the /model picker.

    Listable means eligible (ranked) *and* actually offered free by someone.
    A down endpoint stays in — availability is volatile, the catalog is
    written once a day, and a 0%-today model has usually recovered by the
    time the user opens the picker — but it sorts below every healthy one.
    Ranks are unique, so (bucket, rank) is a total order and two runs over
    the same input always produce the same list.
    """
    listable = [c for c in ranked if c.rank is not None and c.has_free_endpoint]
    listable.sort(key=lambda c: (catalog_bucket(c), c.rank or 0))
    for c in listable:
        c.in_catalog = True
    return listable


def _catalog_reason(tier: str | None, want: int, mode: Mode) -> str:
    """Why a classified model was or wasn't a candidate, under this mode."""
    if tier == TIER_UNKNOWN or tier is None:
        return "listed: privacy tier could not be determined — never auto-selected"
    if tier not in mode.tiers:
        return f"listed: {tier} tier — not selectable in {mode.name} mode"
    if mode.tier_dominant and tier != mode.tiers[0]:
        better = " or ".join(mode.tiers[: mode.tiers.index(tier)])
        return f"held: {tier} tier — used only if <{want} {better}"
    return f"listed: {tier} tier"


def select_models(
    api_models: list[dict[str, Any]],
    collection_order: list[str] | None,
    privacy_of: Callable[[str], Privacy],
    availability_of: Callable[[str], Availability],
    *,
    today: _dt.date,
    want: int = DEFAULT_WANT,
    expiry_buffer_days: int = EXPIRY_BUFFER_DAYS,
    max_classifications: int = MAX_CLASSIFICATIONS,
    mode: Mode | None = None,
) -> SelectionResult:
    mode = mode or MODES[DEFAULT_MODE]
    candidates = [Candidate.from_api(m) for m in api_models]

    eligible: list[Candidate] = []
    for c in candidates:
        if not c.supports_tools:
            c.reason = "skipped: free endpoint does not support tool calling"
        elif is_expired(c.expiration_date, today, expiry_buffer_days):
            c.reason = f"skipped: expires {c.expiration_date} (within {expiry_buffer_days}d buffer)"
        else:
            eligible.append(c)

    ranked, used_fallback = rank_candidates(eligible, collection_order)
    # Unranked (filtered-out) candidates keep rank=None; rank the full list
    # for display purposes only after eligibility so ranks reflect choices.

    # One pool per selectable tier, filled in rank order.
    pools: dict[str, list[Candidate]] = {t: [] for t in mode.tiers}
    budget_exhausted = False
    lookups = 0
    for c in ranked:
        # Classify every ranked candidate, not just enough to fill `want`:
        # the catalog needs a tier for each model to order and label it.
        if lookups >= max_classifications:
            c.outcome = OUTCOME_UNCLASSIFIED
            c.reason = "not classified: lookup budget exhausted"
            budget_exhausted = True
            continue
        lookups += 1

        # Availability first — a cheap JSON call that skips down endpoints
        # before we spend a page scrape on privacy.
        availability = availability_of(c.id)
        c.uptime_1d = availability.best_uptime_1d
        c.available = availability.ok
        c.has_free_endpoint = availability.has_free_endpoint
        if not availability.ok:
            c.tier = None
            c.endpoint_provider = availability.endpoint_provider
            if availability.has_free_endpoint:
                c.outcome = OUTCOME_DEGRADED
                c.reason = f"listed last: {availability.reason}"
            else:
                c.outcome = OUTCOME_SKIPPED
                c.reason = f"skipped: {availability.reason}"
            continue

        privacy = privacy_of(c.base_slug)
        c.tier = privacy.tier
        c.classified = True
        c.outcome = OUTCOME_CATALOG
        c.endpoint_provider = privacy.endpoint_provider or availability.endpoint_provider
        c.reason = _catalog_reason(privacy.tier, want, mode)
        if privacy.tier in pools:
            pools[privacy.tier].append(c)

    if mode.tier_dominant:
        # Tier beats popularity: every private pick outranks any logs pick, so
        # a retains-prompts model can never become the default while a private
        # one exists. Rank order is preserved within each tier.
        ordered = [c for tier in mode.tiers for c in pools[tier]]
    else:
        # Popularity beats tier: any allowed model can win on rank alone.
        ordered = sorted(
            (c for pool in pools.values() for c in pool), key=lambda c: c.rank or 0
        )
    selected = ordered[:want]

    for i, c in enumerate(selected):
        role = "default" if i == 0 else f"fallback #{i}"
        c.outcome = OUTCOME_SELECTED
        c.role_index = i
        c.reason = f"selected: {role} ({c.tier} tier)"

    return SelectionResult(
        selected=selected,
        candidates=candidates,
        catalog=build_catalog(ranked),
        used_fallback_ranking=used_fallback,
        budget_exhausted=budget_exhausted,
    )
