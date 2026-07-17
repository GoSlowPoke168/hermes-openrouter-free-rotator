"""State dir (~/.hermes/freemodels/): state.json, logging, privacy cache.

state.json is the plugin's ownership ledger — `managed_default` and
`managed_fallbacks` record exactly which config entries the plugin wrote,
so sync never touches entries the user added by hand. It also caches
privacy lookups (model-page scrapes) for 24h so the daily cron run is
usually a single API call.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import logging.handlers
import os
from pathlib import Path
from typing import Any

from .openrouter import Privacy

STATE_VERSION = 1
PRIVACY_TTL_HOURS = 24


def hermes_home() -> Path:
    try:
        from hermes_constants import get_hermes_home

        return Path(get_hermes_home())
    except Exception:
        return Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser()


def state_dir() -> Path:
    d = hermes_home() / "freemodels"
    d.mkdir(parents=True, exist_ok=True)
    return d


def state_path() -> Path:
    return state_dir() / "state.json"


def default_state() -> dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "last_sync": None,
        "last_change": None,
        "last_error": None,
        "selected": [],
        "managed_default": None,
        "managed_fallbacks": [],
        "previous_default": None,
        "privacy_cache": {},
    }


def load_state() -> dict[str, Any]:
    try:
        data = json.loads(state_path().read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("state.json is not an object")
    except (OSError, ValueError):
        return default_state()
    merged = default_state()
    merged.update(data)
    return merged


def save_state(state: dict[str, Any]) -> None:
    target = state_path()
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    try:
        from utils import atomic_replace  # hermes helper: symlink-safe atomic move

        atomic_replace(tmp, target)
    except ImportError:
        os.replace(tmp, target)


def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Privacy cache
# ---------------------------------------------------------------------------

def cached_privacy(state: dict[str, Any], base_slug: str) -> Privacy | None:
    entry = (state.get("privacy_cache") or {}).get(base_slug)
    if not isinstance(entry, dict):
        return None
    try:
        checked = _dt.datetime.fromisoformat(entry["checked"])
    except (KeyError, ValueError):
        return None
    age = _dt.datetime.now(_dt.timezone.utc) - checked
    if age > _dt.timedelta(hours=PRIVACY_TTL_HOURS):
        return None
    return Privacy.from_dict(entry)


def cache_privacy(state: dict[str, Any], base_slug: str, privacy: Privacy) -> None:
    state.setdefault("privacy_cache", {})[base_slug] = {
        **privacy.to_dict(),
        "checked": now_iso(),
    }


def prune_privacy_cache(state: dict[str, Any], keep_slugs: set[str]) -> None:
    cache = state.get("privacy_cache") or {}
    state["privacy_cache"] = {k: v for k, v in cache.items() if k in keep_slugs}


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

_logger: logging.Logger | None = None


def get_logger() -> logging.Logger:
    global _logger
    if _logger is not None:
        return _logger
    logger = logging.getLogger("hermes_freemodels")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = logging.handlers.RotatingFileHandler(
            state_dir() / "freemodels.log", maxBytes=512_000, backupCount=1
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        )
        logger.addHandler(handler)
    logger.propagate = False
    _logger = logger
    return logger
