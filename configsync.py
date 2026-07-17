"""Plan and apply the config.yaml mutation.

Ownership model: the plugin only ever replaces `model.default` and the
fallback entries recorded in state.json as `managed_fallbacks` (plus, on
first run, it adopts any existing openrouter `:free` entries as managed).
Everything else in `fallback_providers` — e.g. hand-added paid last-resort
models — is preserved, in order, at the end of the chain.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from typing import Any

from .selection import Candidate, is_expired
from .state import state_dir


@dataclass
class SyncPlan:
    abort_reason: str | None = None
    changed: bool = False
    reasons: list[str] = field(default_factory=list)
    current_default: str | None = None
    new_default: str | None = None
    current_chain: list[dict[str, Any]] = field(default_factory=list)
    new_chain: list[dict[str, Any]] = field(default_factory=list)
    managed_fallbacks: list[dict[str, str]] = field(default_factory=list)


def _entry_key(entry: dict[str, Any]) -> tuple[str, str]:
    return (str(entry.get("provider", "")), str(entry.get("model", "")))


def _is_managed_shape(entry: dict[str, Any]) -> bool:
    return (
        str(entry.get("provider", "")) == "openrouter"
        and str(entry.get("model", "")).endswith(":free")
    )


def build_plan(
    cfg: dict[str, Any],
    selected: list[Candidate],
    state: dict[str, Any],
    *,
    force: bool,
    today,
    free_models_by_id: dict[str, dict[str, Any]],
) -> SyncPlan:
    from hermes_cli.fallback_config import get_fallback_chain

    plan = SyncPlan()

    model_cfg = cfg.get("model") or {}
    current_default = model_cfg.get("default")
    plan.current_default = current_default
    if model_cfg.get("provider") != "openrouter":
        plan.abort_reason = (
            f"model.provider is '{model_cfg.get('provider')}', not 'openrouter' — "
            "refusing to manage a setup this plugin doesn't own. "
            "Switch back with `hermes model` or remove the plugin's cron entry."
        )
        return plan
    if not current_default:
        plan.abort_reason = "model.default is missing from config.yaml — schema drift, aborting."
        return plan
    if not selected:
        plan.abort_reason = "no qualifying free models found — leaving config untouched."
        return plan

    current_chain = get_fallback_chain(cfg)
    plan.current_chain = current_chain

    # Bootstrap ownership: adopt the current :free default and any existing
    # openrouter :free fallback entries as plugin-managed.
    managed_keys = {("openrouter", str(m.get("model", ""))) for m in state.get("managed_fallbacks") or []}
    if not state.get("managed_fallbacks") and not state.get("managed_default"):
        managed_keys |= {_entry_key(e) for e in current_chain if _is_managed_shape(e)}

    plan.new_default = selected[0].id
    fallback_ids = [c.id for c in selected[1:]]
    new_managed = [{"provider": "openrouter", "model": mid} for mid in fallback_ids]
    preserved = [e for e in current_chain if _entry_key(e) not in managed_keys]
    plan.new_chain = new_managed + preserved
    plan.managed_fallbacks = new_managed

    default_changed = plan.new_default != current_default
    chain_changed = [_entry_key(e) for e in plan.new_chain] != [
        _entry_key(e) for e in current_chain
    ]

    current_model = free_models_by_id.get(str(current_default))
    default_gone = current_model is None
    default_expired = current_model is not None and is_expired(
        current_model.get("expiration_date"), today
    )

    if default_changed:
        plan.reasons.append(f"default: {current_default} -> {plan.new_default}")
        if default_gone:
            plan.reasons.append(f"current default {current_default} is no longer offered")
        if default_expired:
            plan.reasons.append(f"current default {current_default} has expired")
    if chain_changed:
        plan.reasons.append("fallback chain updated")
    if force and not (default_changed or chain_changed):
        plan.reasons.append("--force: rewriting unchanged selection")

    plan.changed = default_changed or chain_changed or force
    return plan


def apply_plan(plan: SyncPlan, cfg: dict[str, Any]) -> None:
    """Backup config.yaml, mutate cfg, save via hermes's sanctioned writer."""
    from hermes_cli.config import get_config_path, save_config

    config_path = get_config_path()
    try:
        shutil.copy2(config_path, state_dir() / "config.yaml.pre-sync.bak")
    except OSError:
        pass  # backup is best-effort; save_config itself is atomic

    cfg.setdefault("model", {})["default"] = plan.new_default
    cfg["fallback_providers"] = plan.new_chain
    save_config(cfg)
