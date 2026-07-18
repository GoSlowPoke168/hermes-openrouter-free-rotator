"""OpenRouter data sources.

Three fetchers, all unauthenticated:

- fetch_free_models(): public /api/v1/models, filtered to ":free" variants.
  Includes supported_parameters and (crucially) expiration_date.
- fetch_collection_order(): scrapes https://openrouter.ai/collections/free-models,
  whose embedded RSC payload lists model slugs in weekly-token-usage order —
  the "best first" ranking the website shows. Returns None on any failure so
  callers can fall back to an API-derived ordering.
- fetch_privacy(): scrapes a model's page for the free endpoint's data_policy
  (training / retainsPrompts) — this is NOT exposed by the public API.

The scrapers parse Next.js RSC payloads: script chunks of the form
self.__next_f.push([1,"<js-string>"]) whose concatenated payload contains
plain JSON fragments once the string escapes are decoded.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

MODELS_API_URL = "https://openrouter.ai/api/v1/models"
COLLECTION_URL = "https://openrouter.ai/collections/free-models"
MODEL_PAGE_URL = "https://openrouter.ai/{slug}"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0"

TIER_PRIVATE = "private"  # no training, no prompt retention
TIER_LOGS = "logs"        # no training, retains prompts
TIER_TRAINS = "trains"    # may train on prompts — never selectable
TIER_UNKNOWN = "unknown"  # could not determine — never selectable


@dataclass
class Privacy:
    tier: str
    training: bool | None = None
    retains_prompts: bool | None = None
    can_publish: bool | None = None
    endpoint_provider: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "training": self.training,
            "retains_prompts": self.retains_prompts,
            "can_publish": self.can_publish,
            "endpoint_provider": self.endpoint_provider,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Privacy":
        return cls(
            tier=d.get("tier", TIER_UNKNOWN),
            training=d.get("training"),
            retains_prompts=d.get("retains_prompts"),
            can_publish=d.get("can_publish"),
            endpoint_provider=d.get("endpoint_provider"),
        )


MIN_UPTIME_1D = 20.0  # percent; below this a free endpoint is considered down

ENDPOINTS_API_URL = "https://openrouter.ai/api/v1/models/{model_id}/endpoints"


@dataclass
class Availability:
    ok: bool
    reason: str
    best_uptime_1d: float | None = None
    endpoint_provider: str | None = None
    fetched: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "reason": self.reason,
            "best_uptime_1d": self.best_uptime_1d,
            "endpoint_provider": self.endpoint_provider,
            "fetched": self.fetched,
        }


def _http_get(url: str, timeout: int = 20) -> str:
    import requests

    resp = requests.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    return resp.text


def fetch_free_models(timeout: int = 20) -> list[dict[str, Any]]:
    """Return raw model dicts for every ':free' variant on OpenRouter."""
    data = json.loads(_http_get(MODELS_API_URL, timeout=timeout))
    return [m for m in data.get("data", []) if str(m.get("id", "")).endswith(":free")]


def fetch_availability(model_id: str, timeout: int = 20, min_uptime: float = MIN_UPTIME_1D) -> Availability:
    """Assess whether a ':free' model has at least one healthy free endpoint.

    Uses the public per-model endpoints API for the ':free' slug, which
    returns exactly the free endpoint(s) with clean uptime/status. A network
    failure yields ``fetched=False`` and is treated as *available* — a
    transient API hiccup must never wipe the selection.
    """
    try:
        raw = json.loads(_http_get(ENDPOINTS_API_URL.format(model_id=model_id), timeout=timeout))
    except Exception:
        return Availability(ok=True, reason="availability unknown (endpoints API unreachable)", fetched=False)
    return assess_availability(raw.get("data") or {}, min_uptime=min_uptime)


def assess_availability(data: dict[str, Any], min_uptime: float = MIN_UPTIME_1D) -> Availability:
    """Judge a free model available if its best free endpoint's day-long uptime
    clears *min_uptime*.

    Uptime over the last day is the primary signal — it tolerates a brief blip
    ("down for the day") while excluding endpoints that are essentially offline
    (e.g. 0%). OpenRouter's ``status`` field is only consulted when no uptime
    numbers are reported at all (a brand-new endpoint).
    """
    endpoints = data.get("endpoints") if isinstance(data.get("endpoints"), list) else []
    if not endpoints:
        return Availability(ok=False, reason="no free endpoint currently offered")

    def uptime(ep: dict) -> float | None:
        v = ep.get("uptime_last_1d")
        return float(v) if isinstance(v, (int, float)) else None

    best_ep = max(endpoints, key=lambda ep: (uptime(ep) if uptime(ep) is not None else -1.0))
    provider = best_ep.get("provider_name") or best_ep.get("name")
    known = [u for ep in endpoints if (u := uptime(ep)) is not None]

    if known:
        best = max(known)
        if best >= min_uptime:
            return Availability(True, f"uptime {best:.0f}% (1d)", best, provider)
        return Availability(False, f"free endpoint down (uptime {best:.0f}% 1d)", best, provider)

    # No uptime reported anywhere — fall back to the status flag.
    if any((ep.get("status") is None or ep.get("status") >= 0) for ep in endpoints):
        return Availability(True, "status ok, uptime n/a", None, provider)
    return Availability(False, "free endpoint unavailable (status)", None, provider)


# ---------------------------------------------------------------------------
# RSC payload decoding
# ---------------------------------------------------------------------------

_RSC_PUSH_RE = re.compile(r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)')


def decode_rsc_payload(html: str) -> str:
    """Concatenate and unescape all Next.js flight-data chunks in *html*."""
    parts: list[str] = []
    for m in _RSC_PUSH_RE.finditer(html):
        raw = m.group(1)
        try:
            parts.append(json.loads(f'"{raw}"'))
        except ValueError:
            parts.append(raw)
    return "".join(parts)


def fetch_collection_order(timeout: int = 20) -> list[str] | None:
    """Ordered base slugs (e.g. 'tencent/hy3') from the free-models collection.

    The page lists models by weekly token usage, best first. Returns None if
    the fetch or parse fails so callers can use a fallback ranking.
    """
    try:
        html = _http_get(COLLECTION_URL, timeout=timeout)
        return parse_collection_order(decode_rsc_payload(html))
    except Exception:
        return None


def parse_collection_order(payload: str) -> list[str] | None:
    order: list[str] = []
    seen: set[str] = set()
    for slug in re.findall(r'"slug":"([^"]+)"', payload):
        # Provider slugs ("novita") and other noise lack the author/model shape.
        if "/" not in slug or slug in seen:
            continue
        seen.add(slug)
        order.append(slug)
    return order or None


# ---------------------------------------------------------------------------
# Per-model privacy (data policy of the free endpoint)
# ---------------------------------------------------------------------------

def fetch_privacy(base_slug: str, timeout: int = 20) -> Privacy:
    """Classify the free endpoint(s) of *base_slug* by provider data policy."""
    try:
        html = _http_get(MODEL_PAGE_URL.format(slug=base_slug), timeout=timeout)
        return parse_privacy(decode_rsc_payload(html))
    except Exception:
        return Privacy(tier=TIER_UNKNOWN)


def _enclosing_json_object(payload: str, index: int) -> dict[str, Any] | None:
    """Parse the smallest JSON object in *payload* that encloses *index*.

    Walks '{' positions backward from *index* (closest first); the first one
    that raw_decodes to a dict spanning past *index* is the immediate
    enclosing object. Robust to the two RSC page shapes OpenRouter uses
    (endpoints nested under a variant group vs. flat endpoint objects).
    """
    decoder = json.JSONDecoder()
    b = index
    while b >= 0:
        b = payload.rfind("{", 0, b)
        if b < 0:
            return None
        try:
            obj, end = decoder.raw_decode(payload, b)
        except ValueError:
            continue
        if isinstance(obj, dict) and end > index:
            return obj
    return None


def _policy_from_endpoint(ep: dict[str, Any]) -> Privacy | None:
    dp = ep.get("data_policy") or ep.get("dataPolicy")
    if not isinstance(dp, dict):
        return None
    training = bool(dp.get("training")) or bool(
        dp.get("trainingOpenRouter") or dp.get("training_open_router")
    )
    retains = dp.get("retainsPrompts", dp.get("retains_prompts"))
    provider = (
        ep.get("provider_display_name")
        or ep.get("providerDisplayName")
        or ep.get("provider_name")
        or ep.get("providerName")
    )
    if training:
        tier = TIER_TRAINS
    elif retains is None:
        tier = TIER_UNKNOWN
    elif retains:
        tier = TIER_LOGS
    else:
        tier = TIER_PRIVATE
    return Privacy(
        tier=tier,
        training=training,
        retains_prompts=None if retains is None else bool(retains),
        can_publish=bool(dp.get("canPublish", dp.get("can_publish", False))),
        endpoint_provider=str(provider) if provider else None,
    )


_TIER_BADNESS = {TIER_PRIVATE: 0, TIER_LOGS: 1, TIER_UNKNOWN: 2, TIER_TRAINS: 3}


def parse_privacy(payload: str) -> Privacy:
    """Worst-case data policy across every free endpoint in the payload.

    Iterates each ``"data_policy":`` occurrence, resolves its enclosing
    endpoint object, and keeps only free endpoints (``is_free`` or
    ``variant == "free"``). A model can be served free by several providers
    and routing may hit any of them, so the least private endpoint
    determines the model's tier.
    """
    policies: dict[str, Privacy] = {}
    for m in re.finditer(r'"data_?[Pp]olicy"\s*:', payload):
        ep = _enclosing_json_object(payload, m.start())
        if not isinstance(ep, dict):
            continue
        if not (ep.get("is_free") or ep.get("variant") == "free"):
            continue
        policy = _policy_from_endpoint(ep)
        if policy is not None:
            key = str(ep.get("id") or ep.get("provider_slug") or ep.get("provider_name") or id(ep))
            policies[key] = policy
    if not policies:
        return Privacy(tier=TIER_UNKNOWN)
    return max(policies.values(), key=lambda p: _TIER_BADNESS.get(p.tier, 2))
