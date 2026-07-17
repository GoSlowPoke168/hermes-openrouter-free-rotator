"""Command handlers for `hermes freemodels ...`."""

from __future__ import annotations

import datetime as _dt
import json
import os
import sys
from typing import Any

from . import configsync, croninstall, openrouter, selection, state as state_mod


def _today() -> _dt.date:
    # HERMES_FREEMODELS_TODAY lets tests simulate an expiry day without waiting.
    override = os.environ.get("HERMES_FREEMODELS_TODAY")
    if override:
        return _dt.date.fromisoformat(override)
    return _dt.date.today()


def _privacy_lookup(state: dict[str, Any], log):
    """Privacy lookup with the 24h state.json cache in front of the scraper."""

    def lookup(base_slug: str) -> openrouter.Privacy:
        cached = state_mod.cached_privacy(state, base_slug)
        if cached is not None:
            return cached
        privacy = openrouter.fetch_privacy(base_slug)
        if privacy.tier == openrouter.TIER_UNKNOWN:
            log.warning("privacy: could not classify %s", base_slug)
        else:
            state_mod.cache_privacy(state, base_slug, privacy)
        return privacy

    return lookup


def _availability_lookup(log):
    """Fresh uptime check per model (never cached — availability is volatile)."""

    def lookup(model_id: str) -> openrouter.Availability:
        avail = openrouter.fetch_availability(model_id)
        if not avail.ok:
            log.info("availability: %s — %s", model_id, avail.reason)
        return avail

    return lookup


def _run_selection(state: dict[str, Any], log) -> tuple[selection.SelectionResult, dict[str, dict]]:
    api_models = openrouter.fetch_free_models()
    collection_order = openrouter.fetch_collection_order()
    if collection_order is None:
        log.warning("collection page scrape failed — ranking by model creation date")
    result = selection.select_models(
        api_models,
        collection_order,
        _privacy_lookup(state, log),
        _availability_lookup(log),
        today=_today(),
    )
    state_mod.prune_privacy_cache(state, {c.base_slug for c in result.candidates})
    return result, {str(m["id"]): m for m in api_models}


def _fail(state: dict[str, Any], log, message: str) -> None:
    log.error(message)
    state["last_error"] = f"{state_mod.now_iso()} {message}"
    state_mod.save_state(state)
    print(f"error: {message}", file=sys.stderr)
    sys.exit(1)


def _chain_str(chain: list[dict[str, Any]]) -> str:
    return (
        " -> ".join(f"{e.get('provider')}/{e.get('model')}" for e in chain)
        or "(empty)"
    )


def cmd_sync(args) -> None:
    log = state_mod.get_logger()
    state = state_mod.load_state()

    try:
        result, by_id = _run_selection(state, log)
    except Exception as exc:  # network/parse failure: never touch config
        _fail(state, log, f"sync aborted, could not fetch OpenRouter data: {exc}")
        return

    if not result.ok:
        _fail(state, log, "no qualifying free models (tools + not expiring + private/logs tier)")
        return

    from hermes_cli.config import load_config

    cfg = load_config()
    plan = configsync.build_plan(
        cfg, result.selected, state,
        force=args.force, today=_today(), free_models_by_id=by_id,
    )
    if plan.abort_reason:
        _fail(state, log, plan.abort_reason)
        return

    summary = {
        "changed": plan.changed,
        "dry_run": args.dry_run,
        "default": {"before": plan.current_default, "after": plan.new_default},
        "fallback_chain": {
            "before": [_e for _e in plan.current_chain],
            "after": [_e for _e in plan.new_chain],
        },
        "selected": [
            {
                "id": c.id, "rank": c.rank, "tier": c.tier,
                "endpoint_provider": c.endpoint_provider,
                "uptime_1d": c.uptime_1d,
                "expires": c.expiration_date,
            }
            for c in result.selected
        ],
        "reasons": plan.reasons,
    }

    if not plan.changed:
        state["last_sync"] = state_mod.now_iso()
        state["last_error"] = None
        state_mod.save_state(state)
        log.info("sync: no change (default=%s)", plan.current_default)
        if args.json:
            print(json.dumps(summary, indent=2))
        else:
            print(f"no change — default is already {plan.current_default}")
        return

    if args.dry_run:
        state_mod.save_state(state)  # keep privacy cache warm; no ownership change
        if args.json:
            print(json.dumps(summary, indent=2))
        else:
            print("dry run — would apply:")
            print(f"  default:  {plan.current_default} -> {plan.new_default}")
            print(f"  fallbacks: {_chain_str(plan.current_chain)}")
            print(f"         ->  {_chain_str(plan.new_chain)}")
            for r in plan.reasons:
                print(f"  reason: {r}")
        return

    configsync.apply_plan(plan, cfg)

    state.update(
        last_sync=state_mod.now_iso(),
        last_change=state_mod.now_iso(),
        last_error=None,
        previous_default=plan.current_default,
        managed_default=plan.new_default,
        managed_fallbacks=plan.managed_fallbacks,
        selected=summary["selected"],
    )
    state_mod.save_state(state)
    log.info(
        "sync: default %s -> %s; chain: %s",
        plan.current_default, plan.new_default, _chain_str(plan.new_chain),
    )
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(f"updated default: {plan.current_default} -> {plan.new_default}")
        print(f"fallback chain:  {_chain_str(plan.new_chain)}")
        for r in plan.reasons:
            print(f"  reason: {r}")


def cmd_status(args) -> None:
    state = state_mod.load_state()
    try:
        from hermes_cli.config import load_config_readonly

        cfg = load_config_readonly()
        current_default = (cfg.get("model") or {}).get("default")
    except Exception:
        current_default = None

    info = {
        "current_default": current_default,
        "managed_default": state.get("managed_default"),
        "previous_default": state.get("previous_default"),
        "managed_fallbacks": state.get("managed_fallbacks"),
        "selected": state.get("selected"),
        "last_sync": state.get("last_sync"),
        "last_change": state.get("last_change"),
        "last_error": state.get("last_error"),
    }
    if args.json:
        print(json.dumps(info, indent=2))
        return
    print(f"current default:  {current_default}")
    print(f"last sync:        {state.get('last_sync') or 'never'}")
    print(f"last change:      {state.get('last_change') or 'never'}")
    if state.get("previous_default"):
        print(f"previous default: {state.get('previous_default')}")
    if state.get("last_error"):
        print(f"last error:       {state.get('last_error')}")
    selected = state.get("selected") or []
    if selected:
        print("managed selection:")
        for i, s in enumerate(selected):
            role = "default " if i == 0 else "fallback"
            up = s.get("uptime_1d")
            up_str = f"{up:.0f}%" if isinstance(up, (int, float)) else "n/a"
            print(
                f"  {role} {s.get('id')}  tier={s.get('tier')}"
                f"  via={s.get('endpoint_provider') or '?'}"
                f"  uptime={up_str}"
                f"  expires={s.get('expires') or 'none listed'}"
            )
    else:
        print("managed selection: none yet — run `hermes freemodels sync`")


def cmd_list(args) -> None:
    log = state_mod.get_logger()
    state = state_mod.load_state()
    try:
        result, _ = _run_selection(state, log)
    except Exception as exc:
        print(f"error: could not fetch OpenRouter data: {exc}", file=sys.stderr)
        sys.exit(1)
    state_mod.save_state(state)  # persist warmed privacy cache

    rows = [
        {
            "rank": c.rank, "id": c.id, "tier": c.tier,
            "tools": c.supports_tools, "expires": c.expiration_date,
            "uptime_1d": c.uptime_1d,
            "endpoint_provider": c.endpoint_provider, "reason": c.reason,
        }
        for c in sorted(result.candidates, key=lambda c: (c.rank is None, c.rank or 0))
    ]
    if args.json:
        print(json.dumps({"used_fallback_ranking": result.used_fallback_ranking, "models": rows}, indent=2))
        return
    if result.used_fallback_ranking:
        print("note: collection page unavailable — ranked by creation date\n")
    for r in rows:
        rank = f"#{r['rank']}" if r["rank"] else "--"
        tier = r["tier"] or "?"
        expires = r["expires"] or "-"
        up = f"{r['uptime_1d']:.0f}%" if r["uptime_1d"] is not None else "-"
        print(f"{rank:>4}  {r['id']:<55} tier={tier:<8} uptime={up:<5} expires={expires:<12} {r['reason']}")


def cmd_install_cron(args) -> None:
    try:
        line = croninstall.build_cron_line(args.time)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
    if not args.apply:
        print("add this line to your crontab (crontab -e), or rerun with --apply:")
        print(f"  {line}")
        return
    print(croninstall.install_cron_line(line))
