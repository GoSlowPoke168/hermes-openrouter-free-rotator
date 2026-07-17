"""Pure selection logic — no I/O, unit-testable.

Pipeline: hard filters (tool support, expiry buffer) → rank (collection
order, falling back to API metadata) → privacy classification in rank order
(via an injected lookup, so callers control caching/rate limits) → choose
all "private" models first, topping up from "logs" only when fewer than
*want* private ones exist. "trains" and "unknown" are never selectable.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Any, Callable

from .openrouter import TIER_LOGS, TIER_PRIVATE, Availability, Privacy

EXPIRY_BUFFER_DAYS = 2
DEFAULT_WANT = 3
MAX_PRIVACY_LOOKUPS = 12


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
    used_fallback_ranking: bool = False

    @property
    def ok(self) -> bool:
        return bool(self.selected)


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


def select_models(
    api_models: list[dict[str, Any]],
    collection_order: list[str] | None,
    privacy_of: Callable[[str], Privacy],
    availability_of: Callable[[str], Availability],
    *,
    today: _dt.date,
    want: int = DEFAULT_WANT,
    expiry_buffer_days: int = EXPIRY_BUFFER_DAYS,
    max_privacy_lookups: int = MAX_PRIVACY_LOOKUPS,
) -> SelectionResult:
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

    private_pool: list[Candidate] = []
    logs_pool: list[Candidate] = []
    lookups = 0
    for c in ranked:
        if len(private_pool) >= want:
            c.reason = c.reason or "not needed: enough higher-ranked private models"
            continue
        if lookups >= max_privacy_lookups:
            c.reason = "skipped: lookup budget exhausted"
            continue
        lookups += 1

        # Availability first — a cheap JSON call that skips down endpoints
        # before we spend a page scrape on privacy.
        availability = availability_of(c.id)
        c.uptime_1d = availability.best_uptime_1d
        if not availability.ok:
            c.tier = None
            c.endpoint_provider = availability.endpoint_provider
            c.reason = f"skipped: {availability.reason}"
            continue

        privacy = privacy_of(c.base_slug)
        c.tier = privacy.tier
        c.endpoint_provider = privacy.endpoint_provider or availability.endpoint_provider
        if privacy.tier == TIER_PRIVATE:
            private_pool.append(c)
        elif privacy.tier == TIER_LOGS:
            logs_pool.append(c)
            c.reason = "held: retains prompts (logs tier) — used only if <%d private" % want
        else:
            c.reason = f"skipped: privacy tier '{privacy.tier}'"

    # Tier dominates popularity: every private pick outranks any logs pick,
    # so a retains-prompts model can never become the default while a private
    # one exists. Within a tier, collection rank order is preserved.
    selected = private_pool[:want]
    for c in logs_pool:
        if len(selected) >= want:
            break
        selected.append(c)

    for i, c in enumerate(selected):
        role = "default" if i == 0 else f"fallback #{i}"
        c.reason = f"selected: {role} ({c.tier} tier)"

    return SelectionResult(
        selected=selected,
        candidates=candidates,
        used_fallback_ranking=used_fallback,
    )
