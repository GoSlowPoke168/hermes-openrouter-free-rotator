"""Command handlers for `hermes freemodels ...`."""

from __future__ import annotations

import datetime as _dt
import json
import os
import sys
from typing import Any

from . import configsync, croninstall, openrouter, render, selection, state as state_mod

# `/freemodels` reads a snapshot rather than the network, so it needs to say
# when that snapshot went stale. 26h = the 24h privacy TTL plus cron slack.
STALE_AFTER_HOURS = 26


def _today() -> _dt.date:
    # HERMES_FREEMODELS_TODAY lets tests simulate an expiry day without waiting.
    try:
        return _dt.date.fromisoformat(os.environ["HERMES_FREEMODELS_TODAY"])
    except (KeyError, ValueError):
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


def _max_classifications() -> int:
    try:
        return int(os.environ["HERMES_FREEMODELS_MAX_LOOKUPS"])
    except (KeyError, ValueError):
        return selection.MAX_CLASSIFICATIONS


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
        max_classifications=_max_classifications(),
        mode=selection.resolve_mode(state.get("selection_mode")),
    )
    state_mod.prune_privacy_cache(state, {c.base_slug for c in result.candidates})
    # .get, not [], to match Candidate.from_api: one malformed entry in the API
    # response must not take the whole sync down.
    return result, {str(m.get("id", "")): m for m in api_models}


def _snapshot_row(c: selection.Candidate) -> dict[str, Any]:
    """The one Candidate→dict converter.

    Feeds `--json`, the rendered table, and the state.json snapshot alike, so
    those three can never drift. The first eight keys are the original
    `list --json` contract and must not be renamed or dropped.
    """
    return {
        "rank": c.rank,
        "id": c.id,
        "tier": c.tier,
        "tools": c.supports_tools,
        "expires": c.expiration_date,
        "uptime_1d": c.uptime_1d,
        "endpoint_provider": c.endpoint_provider,
        "reason": c.reason,
        "outcome": c.outcome,
        "role_index": c.role_index,
        "in_catalog": c.in_catalog,
        "available": c.available,
    }


def _mark_pinned(rows: list[dict[str, Any]], pinned: str | None) -> list[dict[str, Any]]:
    """Stamp the pinned row so the renderer can label it.

    Kept out of `_snapshot_row` because a pin is a property of the user's
    config, not of the candidate — and out of `render`, which reads structured
    fields and never state.
    """
    if pinned:
        for row in rows:
            if row.get("id") == pinned:
                row["pinned"] = True
    return rows


def _ordered_snapshots(
    result: selection.SelectionResult, pinned: str | None = None
) -> list[dict[str, Any]]:
    """Catalog order (best→worst) first, then everything excluded from it."""
    rows = [_snapshot_row(c) for c in result.catalog]
    rest = [c for c in result.candidates if not c.in_catalog]
    rest.sort(key=lambda c: (c.rank is None, c.rank or 0))
    rows.extend(_snapshot_row(c) for c in rest)
    return _mark_pinned(rows, pinned)


def _catalog_will_be_honored() -> tuple[bool, str]:
    """Use host-reported authentication as an advisory picker check."""
    try:
        from agent.secret_scope import get_secret
        from hermes_cli.auth import get_auth_status

        if get_secret("OPENROUTER_API_KEY") or get_auth_status("openrouter").get("logged_in"):
            return True, ""
    except Exception:
        # Unknown host status must not gate catalog writes or produce misleading warnings.
        return True, ""
    return False, (
        "Hermes reports no OpenRouter credentials — /model may show its built-in "
        "list instead of the free models. Run `hermes auth add openrouter`."
    )


def _store_catalog_snapshot(
    state: dict[str, Any], result: selection.SelectionResult, warning: str
) -> None:
    state["catalog"] = {
        "generated": state_mod.now_iso(),
        "ranking": "created" if result.used_fallback_ranking else "collection",
        "warning": warning or None,
        "rows": _ordered_snapshots(result),
    }


def _persist_snapshot(state: dict[str, Any], log) -> None:
    """Save only the two keys `list` owns, without clobbering a running sync.

    `list` loads state, then spends minutes on the network before saving. A
    sync finishing inside that window would have its ownership ledgers
    overwritten by this stale copy — losing track of the entries it wrote and
    orphaning them in config.yaml. So re-read under the lock and transplant
    just the cache and the snapshot onto whatever is now on disk.
    """
    with state_mod.sync_lock() as acquired:
        if not acquired:
            # A sync holds the lock; it will write a fresher snapshot itself.
            log.info("list: sync in progress — not persisting snapshot")
            return
        current = state_mod.load_state()
        current["privacy_cache"] = state.get("privacy_cache", {})
        current["catalog"] = state.get("catalog")
        state_mod.save_state(current)


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


def _catalog_str(plan: "configsync.CatalogPlan | None") -> str:
    if plan is None:
        return "not managed"
    if not plan.managed_ids:
        return "cleared"
    parts = [f"{len(plan.managed_ids)} models in /model"]
    if plan.removed_ids:
        parts.append(f"{len(plan.removed_ids)} no longer offered, dropped")
    if plan.clear_legacy:
        parts.append("migrated off providers.openrouter.models")
    return ", ".join(parts)


def _catalog_summary(
    plan: "configsync.CatalogPlan | None", warning: str
) -> dict[str, Any]:
    if plan is None:
        return {"managed": False, "warning": warning or None}
    return {
        "managed": True,
        "changed": plan.changed,
        "models": plan.managed_ids,
        "manifest_models": plan.new_ids,
        "removed": plan.removed_ids,
        "preserved": plan.user_ids,
        "migrated_legacy_key": plan.clear_legacy,
        "warning": warning or None,
    }


def _print_catalog_warning(honored: bool, warning: str, args) -> None:
    if honored or not warning or getattr(args, "no_catalog", False):
        return
    print(f"warning: {warning}", file=sys.stderr)


def _view_warnings(state: dict[str, Any]) -> list[str]:
    notes = []
    if state.get("managed_catalog"):
        notes.extend([
            "Automatic updates: With daily scheduling enabled, sync fetches free models and "
            "updates the config and picker catalog. Keep the gateway running for scheduled "
            "syncs and periodic catalog refreshes (when enabled).",
            "The /model picker may show cached results. If models are missing, wait for the "
            "gateway's catalog refresh, then run `/model --refresh`; this opens the picker. "
            "On Discord, use `/model` with `name: --refresh`. Restarting alone may still use the disk cache.",
            "Restart Hermes after installing or updating this plugin to load its commands. "
            "An open CLI session may keep its old model: select the new model or restart that "
            "session. Gateway chats using the global default follow config changes automatically; "
            "session/channel overrides take precedence.",
        ])
    selected = state.get("selected") or []
    pinned = state.get("pinned")
    watched = {row.get("model") for row in state.get("managed_fallbacks") or []}
    watched.add(pinned or state.get("managed_default"))
    rows = {row.get("id"): row for row in selected}
    rows.update({row.get("id"): row for row in (state.get("catalog") or {}).get("rows") or []})
    now = _dt.datetime.now(_dt.timezone.utc)
    for model_id, row in rows.items():
        if not model_id or model_id not in watched:
            continue
        try:
            expiry = _dt.datetime.fromisoformat(row.get("expires"))
        except (ValueError, TypeError):
            continue
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=_dt.timezone.utc)
        if expiry > now + _dt.timedelta(hours=48):
            continue
        timing = "expired" if expiry <= now else "expires"
        soon = "" if expiry <= now else " (within 48 hours)"
        action = (
            "Choose another model or run `hermes freemodels unpin`."
            if model_id == pinned else "Run `hermes freemodels sync`."
        )
        notes.append(f"{model_id} {timing} on {row['expires']}{soon}. {action}")
    return notes


def _record_catalog(state: dict[str, Any], plan: "configsync.CatalogPlan") -> None:
    state["managed_catalog"] = list(plan.managed_ids)
    state["managed_catalog_written"] = state_mod.now_iso()


def _apply_catalog_only(
    args, state: dict[str, Any], log, cfg: dict[str, Any],
    catalog_plan: "configsync.CatalogPlan | None",
) -> None:
    """Land the catalog on a path where the rotation is about to fail.

    The picker list feeds no routing decision, so a rotation that can't run —
    wrong provider, nothing qualifying — is no reason to withhold it.
    """
    if catalog_plan is None or not catalog_plan.changed or args.dry_run:
        return
    try:
        configsync.apply_all(cfg, catalog=catalog_plan)
    except Exception as exc:
        log.warning("catalog write failed: %s", exc)
        return
    _record_catalog(state, catalog_plan)
    log.info("catalog: %s (rotation did not run)", _catalog_str(catalog_plan))
    if not args.json:
        print(f"/model catalog:  {_catalog_str(catalog_plan)}")


def _settle_pin(state: dict[str, Any], plan: configsync.SyncPlan, log) -> None:
    """Drop a pin the plan found unusable, leaving a note explaining why.

    The note survives in state.json until the next successful `pin`, so the
    user finds out why rotation resumed even if they weren't watching the run
    that dropped it.
    """
    if not plan.pin_cleared:
        return
    log.info("pin: cleared — %s", plan.pin_cleared)
    state.update(
        pinned=None,
        pinned_at=None,
        pin_cleared_note=f"{state_mod.now_iso()} {plan.pin_cleared} — rotation resumed",
    )


def _warn_stale_cron() -> None:
    """Surface a crontab still using the self-deadlocking flock wrapper.

    Written to stderr so it lands in cron.log (the crontab line redirects
    2>&1) — that is exactly where someone debugging a stale default looks.
    Warn only: rewriting a user's crontab behind their back is not ours to do.
    """
    try:
        stale = croninstall.find_stale_cron_line()
    except Exception:
        return                      # never let a health check break a sync
    if not stale:
        return
    print(
        "warning: your crontab wraps this command in flock(1), which makes "
        "every scheduled run exit without syncing:\n"
        f"  {stale}\n"
        "  fix: run git pull and then hermes freemodels install-cron --apply",
        file=sys.stderr,
    )


def _cron_missing() -> bool:
    try:
        return croninstall.schedule_missing()
    except Exception:
        return False                # a health check must never break a command


def cmd_sync(args) -> None:
    scheduled_time = getattr(args, "scheduled_time", None)
    if scheduled_time:
        try:
            due = croninstall.scheduled_sync_due(state_mod.load_state().get("last_sync"), scheduled_time)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            sys.exit(1)
        if not due:
            return
    _warn_stale_cron()
    log = state_mod.get_logger()
    with state_mod.sync_lock() as acquired:
        if not acquired:
            print("another sync is already running — skipping this run")
            return
        # Load inside the lock: `pin` writes state too, and reading first would
        # let this run's save_state clobber a pin set while we were fetching.
        current = state_mod.load_state()
        if scheduled_time and not croninstall.scheduled_sync_due(current.get("last_sync"), scheduled_time):
            return
        _sync(args, current, log)


def _sync(args, state: dict[str, Any], log) -> None:
    try:
        result, by_id = _run_selection(state, log)
    except Exception as exc:  # network/parse failure: never touch config
        _fail(state, log, f"sync aborted, could not fetch OpenRouter data: {exc}")
        return

    honored, catalog_warning = _catalog_will_be_honored()
    if not args.no_catalog and not honored:
        log.warning("catalog: %s", catalog_warning)
    _store_catalog_snapshot(state, result, "" if honored else catalog_warning)

    from hermes_cli.config import load_config

    cfg = load_config()
    catalog_plan = (
        None if args.no_catalog else configsync.build_catalog_plan(cfg, result.catalog, state)
    )
    if catalog_plan is not None and catalog_plan.skipped_reason:
        log.warning("catalog: %s", catalog_plan.skipped_reason)
        catalog_plan = None

    if not result.ok:
        # No rotation possible, but the catalog is independent of the privacy
        # gate — write it anyway so /model still gains the free models.
        _apply_catalog_only(args, state, log, cfg, catalog_plan)
        mode = selection.resolve_mode(state.get("selection_mode"))
        widen = "" if mode.name == "loose" else " — try a wider `hermes freemodels mode`"
        _fail(
            state, log,
            f"no qualifying free models in {mode.name} mode "
            f"({'/'.join(mode.tiers)} tier, tool-capable, not expiring){widen}",
        )
        return

    plan = configsync.build_plan(
        cfg, result.selected, state,
        force=args.force, today=_today(), free_models_by_id=by_id,
        pinned=state.get("pinned"),
    )
    if plan.abort_reason:
        # Same reasoning: the catalog changes no routing, so a user who moved
        # off OpenRouter still gets the picker list they asked for.
        _apply_catalog_only(args, state, log, cfg, catalog_plan)
        _fail(state, log, plan.abort_reason)
        return

    catalog_changed = catalog_plan is not None and catalog_plan.changed
    summary = {
        "changed": plan.changed or catalog_changed,
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
        "catalog": _catalog_summary(catalog_plan, catalog_warning if not honored else ""),
        "pinned": plan.pinned,
        "pin_cleared": plan.pin_cleared,
        "reasons": plan.reasons,
    }

    if not (plan.changed or catalog_changed):
        state["last_sync"] = state_mod.now_iso()
        state["last_error"] = None
        _settle_pin(state, plan, log)
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
            if plan.changed:
                print(f"  default:  {plan.current_default} -> {plan.new_default}")
                print(f"  fallbacks: {_chain_str(plan.current_chain)}")
                print(f"         ->  {_chain_str(plan.new_chain)}")
            if catalog_changed:
                print(f"  /model catalog: {_catalog_str(catalog_plan)}")
            for r in plan.reasons:
                print(f"  reason: {r}")
            _print_catalog_warning(honored, catalog_warning, args)
        return

    configsync.apply_all(
        cfg,
        rotation=plan if plan.changed else None,
        catalog=catalog_plan if catalog_changed else None,
    )

    if plan.changed:
        state.update(
            last_change=state_mod.now_iso(),
            previous_default=plan.current_default,
            managed_default=plan.new_default,
            managed_fallbacks=plan.managed_fallbacks,
            selected=summary["selected"],
        )
    if catalog_changed:
        _record_catalog(state, catalog_plan)
    state.update(last_sync=state_mod.now_iso(), last_error=None)
    _settle_pin(state, plan, log)
    state_mod.save_state(state)

    log.info(
        "sync: default %s -> %s; chain: %s; catalog: %s",
        plan.current_default, plan.new_default, _chain_str(plan.new_chain),
        _catalog_str(catalog_plan) if catalog_changed else "unchanged",
    )
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        if plan.changed:
            print(f"updated default: {plan.current_default} -> {plan.new_default}")
            print(f"fallback chain:  {_chain_str(plan.new_chain)}")
        if catalog_changed:
            print(f"/model catalog:  {_catalog_str(catalog_plan)}")
        for r in plan.reasons:
            print(f"  reason: {r}")
        _print_catalog_warning(honored, catalog_warning, args)


def cmd_status(args) -> None:
    _warn_stale_cron()
    state = state_mod.load_state()
    try:
        from hermes_cli.config import load_config_readonly

        cfg = load_config_readonly()
        current_default = (cfg.get("model") or {}).get("default")
    except Exception:
        current_default = None

    managed_catalog = state.get("managed_catalog") or []
    info = {
        "current_default": current_default,
        "managed_default": state.get("managed_default"),
        "previous_default": state.get("previous_default"),
        "managed_fallbacks": state.get("managed_fallbacks"),
        "selected": state.get("selected"),
        "managed_catalog": managed_catalog,
        "managed_catalog_written": state.get("managed_catalog_written"),
        "pinned": state.get("pinned"),
        "pinned_at": state.get("pinned_at"),
        "selection_mode": selection.resolve_mode(state.get("selection_mode")).name,
        "last_sync": state.get("last_sync"),
        "last_change": state.get("last_change"),
        "last_error": state.get("last_error"),
        "cron_missing": _cron_missing(),
    }
    if args.json:
        print(json.dumps(info, indent=2))
        return
    mode = selection.resolve_mode(state.get("selection_mode"))
    print(f"current default:  {current_default}")
    print(f"mode:             {mode.name} — {mode.summary}")
    if state.get("pinned"):
        since = state.get("pinned_at") or "unknown"
        print(f"pinned:           {state['pinned']} since {since} — rotation paused")
    else:
        print("pinned:           none — rotating automatically")
    if state.get("pin_cleared_note"):
        print(f"pin cleared:      {state['pin_cleared_note']}")
    print(f"last sync:        {state.get('last_sync') or 'never'}")
    print(f"last change:      {state.get('last_change') or 'never'}")
    if info["cron_missing"]:
        print(
            "daily schedule:   NOT INSTALLED — nothing keeps your models fresh;"
            " run `hermes freemodels install-cron --apply`"
        )
    else:
        try:
            if not croninstall.hermes_job_missing():
                print("daily schedule:   Hermes job installed; check `hermes cron status` for gateway/ticker health")
        except (OSError, ValueError):
            print("daily schedule:   could not inspect Hermes jobs; check `hermes cron status`")
    if managed_catalog:
        written = state.get("managed_catalog_written") or "unknown"
        stale = " (stale)" if _is_stale(state.get("last_sync")) else ""
        print(f"/model catalog:   {len(managed_catalog)} models, written {written}{stale}")
    else:
        print("/model catalog:   none written yet")
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
    for note in _view_warnings(state):
        print(f"warning: {note}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Pin: a manual override of the rotated default
# ---------------------------------------------------------------------------

def _pin_tier(state: dict[str, Any], model_id: str) -> str | None:
    """Privacy tier for `model_id` per the last catalog snapshot, if known."""
    for row in (state.get("catalog") or {}).get("rows") or []:
        if row.get("id") == model_id:
            return row.get("tier")
    return None


def _do_pin(model_id: str | None, force: bool) -> tuple[bool, str]:
    """Pin a model and switch to it. Returns (ok, message). No network.

    Shared by the CLI command and the slash command so the two can't drift.
    Pinning switches as well as freezing: a pin that only took effect at the
    next sync would be a setting, not an action.
    """
    log = state_mod.get_logger()
    with state_mod.sync_lock() as acquired:
        if not acquired:
            return False, "a sync is running right now — try again in a moment."
        state = state_mod.load_state()
        try:
            from hermes_cli.config import load_config

            cfg = load_config()
        except Exception as exc:
            return False, f"could not read config.yaml: {exc}"

        model_cfg = cfg.get("model") or {}
        if model_cfg.get("provider") != "openrouter":
            return False, (
                f"model.provider is '{model_cfg.get('provider')}', not 'openrouter' — "
                "refusing to manage a setup this plugin doesn't own."
            )

        target = model_id or model_cfg.get("default")
        if not target:
            return False, "no model given and model.default is empty — pass a model id."

        notes: list[str] = []
        known = configsync.read_manifest_ids()
        if not known:
            notes.append("Couldn't verify the id — no /model catalog has been written yet.")
        elif target not in known and not force:
            return False, (
                f"{target} isn't in the /model catalog. Run `/freemodels` to see the "
                "ids you can pin, or `hermes freemodels pin --force <id>` to override."
            )

        tier = _pin_tier(state, target)
        if tier == "trains":
            notes.append("Warning: this model trains on your prompts.")
        elif tier == "logs":
            notes.append("Note: this model retains prompts.")

        try:
            configsync.apply_pin(cfg, target)
        except Exception as exc:
            return False, f"could not write config.yaml: {exc}"

        state.update(pinned=target, pinned_at=state_mod.now_iso(), pin_cleared_note=None)
        state_mod.save_state(state)
        log.info("pin: %s — rotation of model.default paused", target)

    lines = [f"Pinned {target} — sync will no longer change model.default."]
    lines.extend(notes)
    lines.append("Fallbacks keep rotating. Run `unpin` to resume automatic selection.")
    return True, "\n".join(lines)


def _do_unpin() -> tuple[bool, str]:
    """Clear the pin. Returns (ok, message). No network, no config write."""
    log = state_mod.get_logger()
    with state_mod.sync_lock() as acquired:
        if not acquired:
            return False, "a sync is running right now — try again in a moment."
        state = state_mod.load_state()
        previous = state.get("pinned")
        if not previous:
            return True, "No model is pinned — sync is already rotating automatically."
        state.update(pinned=None, pinned_at=None, pin_cleared_note=None)
        state_mod.save_state(state)
        log.info("unpin: %s — rotation resumed", previous)
    return True, (
        f"Unpinned {previous}. model.default stays as it is for now; the next sync "
        "will start rotating it again."
    )


def _mode_listing(active: str) -> str:
    """The four modes, one per line, marking the active one."""
    return "\n".join(
        f"  {'*' if name == active else ' '} {name:<9} {m.summary}"
        for name, m in selection.MODES.items()
    )


def _do_mode(name: str | None) -> tuple[bool, str]:
    """Read or set the selection mode. Returns (ok, message).

    No network and no config write — a mode only takes effect the next time
    selection runs, which the message has to say so nobody waits for a change
    that hasn't happened yet.
    """
    log = state_mod.get_logger()
    if name is None:
        # Reporting takes no lock: a sync can hold it for minutes, and there's
        # no reason "which mode am I on?" should fail while one runs.
        current = selection.resolve_mode(state_mod.load_state().get("selection_mode"))
        return True, (
            f"**Selection mode:** {current.name} — {current.summary}\n"
            f"{_mode_listing(current.name)}\n"
            "Set one with `mode <name>`."
        )
    if name.lower() not in selection.MODES:
        return False, f"unknown mode '{name}'. Valid modes:\n{_mode_listing('')}"

    with state_mod.sync_lock() as acquired:
        if not acquired:
            return False, "a sync is running right now — try again in a moment."
        state = state_mod.load_state()
        current = selection.resolve_mode(state.get("selection_mode"))
        chosen = selection.MODES[name.lower()]
        state["selection_mode"] = chosen.name
        state_mod.save_state(state)
        log.info("mode: %s -> %s", current.name, chosen.name)

    note = ""
    if chosen.name == "loose":
        note = "\n⚠️ This allows models that train on your prompts."
    elif chosen.name == "strict":
        note = (
            "\nOnly private models qualify — if there are fewer than 3, you get "
            "fewer fallbacks, and if there are none the sync leaves your config alone."
        )
    return True, (
        f"Selection mode: {chosen.name} — {chosen.summary}.{note}\n"
        "Run `hermes freemodels sync` to apply it now; otherwise it takes effect "
        "at the next daily run."
    )


def _emit(ok: bool, message: str) -> None:
    """Print a (ok, message) pair the way every other command reports."""
    if ok:
        print(message)
        return
    print(f"error: {message}", file=sys.stderr)
    sys.exit(1)


def cmd_pin(args) -> None:
    _emit(*_do_pin(args.model_id, args.force))


def cmd_unpin(args) -> None:
    _emit(*_do_unpin())


def cmd_mode(args) -> None:
    _emit(*_do_mode(args.mode))


def cmd_list(args) -> None:
    log = state_mod.get_logger()
    state = state_mod.load_state()
    try:
        result, _ = _run_selection(state, log)
    except Exception as exc:
        print(f"error: could not fetch OpenRouter data: {exc}", file=sys.stderr)
        sys.exit(1)

    honored, warning = _catalog_will_be_honored()
    _store_catalog_snapshot(state, result, "" if honored else warning)
    _persist_snapshot(state, log)

    if args.json:
        # Unchanged contract: same envelope, same keys, same ordering. New
        # per-row fields and the top-level catalog block are additive only.
        print(json.dumps({
            "used_fallback_ranking": result.used_fallback_ranking,
            "models": [
                _snapshot_row(c)
                for c in sorted(result.candidates, key=lambda c: (c.rank is None, c.rank or 0))
            ],
            "catalog": result.catalog_ids,
            "ranking": "created" if result.used_fallback_ranking else "collection",
        }, indent=2))
        return

    if result.used_fallback_ranking:
        print("note: collection page unavailable — ranked by creation date\n")
    print(render.render_snapshots(_ordered_snapshots(result, state.get("pinned"))))
    for note in _view_warnings(state):
        print(f"warning: {note}", file=sys.stderr)
    if not honored:
        print(f"\nwarning: {warning}", file=sys.stderr)


def cmd_remove_catalog(args) -> None:
    """Drop the plugin's ids from providers.openrouter.models. No network."""
    log = state_mod.get_logger()
    state = state_mod.load_state()
    try:
        from hermes_cli.config import load_config

        cfg = load_config()
    except Exception as exc:
        print(f"error: could not read config.yaml: {exc}", file=sys.stderr)
        sys.exit(1)

    plan = configsync.build_catalog_plan(cfg, [], state, remove=True)
    if plan.skipped_reason:
        print(f"nothing to do — {plan.skipped_reason}")
        return
    if not plan.changed:
        print("nothing to do — this plugin isn't adding any models to /model")
        return

    configsync.apply_all(cfg, catalog=plan)
    state["managed_catalog"] = []
    state["managed_catalog_written"] = None
    state_mod.save_state(state)
    log.info("catalog: removed %d managed models", len(plan.removed_ids))
    print(f"removed {len(plan.removed_ids)} plugin-managed models from /model")
    if plan.user_ids:
        print(f"kept {len(plan.user_ids)} model(s) you added to providers.openrouter.models")


def _is_stale(last_sync: Any) -> bool:
    if not isinstance(last_sync, str) or not last_sync:
        return True
    try:
        when = _dt.datetime.fromisoformat(last_sync)
    except ValueError:
        return True
    age = _dt.datetime.now(_dt.timezone.utc) - when
    return age > _dt.timedelta(hours=STALE_AFTER_HOURS)


def _age_str(last_sync: Any) -> str:
    if not isinstance(last_sync, str) or not last_sync:
        return "never"
    try:
        when = _dt.datetime.fromisoformat(last_sync)
    except ValueError:
        return "unknown"
    hours = (_dt.datetime.now(_dt.timezone.utc) - when).total_seconds() / 3600
    if hours < 1:
        return "just now"
    if hours < 48:
        return f"{hours:.0f}h ago"
    return f"{hours / 24:.0f}d ago"


def _in_chat_client() -> bool:
    """True when output goes to a chat client rather than a terminal.

    Chat clients render in a proportional font, which collapses an aligned
    table into gibberish — it needs a code fence. Terminals are already
    monospaced and would just show the literal backticks.

    The platform itself is unavailable here: plugin slash commands are
    dispatched before the gateway binds its session vars, and one gateway
    process serves several platforms, so env vars can't be trusted either.
    ``_cli_ref`` is the reliable signal — the interactive CLI sets it on the
    plugin manager, and it stays None everywhere else.
    """
    try:
        from hermes_cli.plugins import get_plugin_manager

        return get_plugin_manager()._cli_ref is None
    except Exception:
        return False  # unknown host: assume a terminal, don't add noise


def _fence(body: str) -> str:
    return f"```\n{body}\n```" if _in_chat_client() else body


# Discord caps a message at 2000 characters and silently truncates past it —
# which would cut the closing fence and spill raw table text into the channel.
CHAT_CHAR_BUDGET = 1900
MAX_ROWS = 40


def _fit_rows(rows: list[dict[str, Any]], header: str) -> tuple[list[dict[str, Any]], int]:
    """Trim rows until the rendered message fits the destination's limits."""
    shown = rows[:MAX_ROWS]
    if _in_chat_client():
        budget = CHAT_CHAR_BUDGET - len(header)
        # Re-render as we drop rows: the table's column widths shrink with the
        # content, so a character estimate per row would be wrong.
        while len(shown) > 1 and len(render.render_snapshots(shown)) + 8 > budget:
            shown = shown[:-1]
    return shown, max(0, len(rows) - len(shown))


def slash_freemodels(raw_args: str) -> str:
    """`/freemodels` — the labelled table `/model` structurally can't show.

    Never hits the network — a round-trip would stall the interactive turn, so
    the view is served from the state.json snapshot and the staleness banner
    names the command to refresh it. `pin`/`unpin`/`mode` do write, but only
    when typed explicitly; viewing is still free of side effects.
    """
    parts = (raw_args or "").strip().split()
    # Tokenised rather than compared whole, because a model id must keep its
    # case while the verb is matched case-insensitively.
    verb = parts[0].lower() if parts else ""
    target = parts[1] if len(parts) > 1 else None

    if verb == "mode":
        return _do_mode(target)[1]
    if verb == "pin":
        return _do_pin(target, force=False)[1]
    if verb == "unpin":
        return _do_unpin()[1]

    state = state_mod.load_state()
    snapshot = state.get("catalog") or {}
    rows = snapshot.get("rows") or []

    if verb == "json":
        return _fence(json.dumps(snapshot, indent=2))

    if not rows:
        return "\n".join([
            "**Free OpenRouter Models**\nNo data yet — run `hermes freemodels sync`.",
            *(f"⚠️ {note}" for note in _view_warnings(state)),
        ])

    if verb != "full":
        rows = [r for r in rows if r.get("in_catalog")]

    pinned = state.get("pinned")
    rows = _mark_pinned([dict(r) for r in rows], pinned)

    last_sync = state.get("last_sync")
    scope = "All candidates" if verb == "full" else "Available in /model"
    mode = selection.resolve_mode(state.get("selection_mode"))
    lines = [
        "**Free OpenRouter Models**",
        f"{scope}: {len(rows)} · Mode: {mode.name} · Last synced {_age_str(last_sync)}",
    ]

    notes: list[str] = []
    notes.extend(f"⚠️ {note}" for note in _view_warnings(state))
    if mode.name == "loose":
        notes.append("⚠️ Mode `loose` — models that train on your prompts can be selected.")
    if pinned:
        notes.append(
            f"📌 Pinned to `{pinned}` — rotation paused. `/freemodels unpin` to resume."
        )
    if state.get("pin_cleared_note"):
        notes.append(f"ℹ️ {state['pin_cleared_note']}")
    if _is_stale(last_sync):
        notes.append("⚠️ Data is stale — run `hermes freemodels sync` to refresh.")
        if _cron_missing():
            notes.append(
                "⚠️ The daily sync isn't scheduled — run "
                "`hermes freemodels install-cron --apply`."
            )
    if state.get("last_error"):
        notes.append(f"⚠️ Last sync failed: {state['last_error']}")
    if snapshot.get("ranking") == "created":
        notes.append(
            "ℹ️ Ranked by release date — OpenRouter's usage ranking was unavailable."
        )
    if snapshot.get("warning"):
        notes.append(f"⚠️ {snapshot['warning']}")
    if notes:
        lines.append("")
        lines.extend(notes)

    hint = (
        ""
        if verb == "full"
        else "Pin one with `/freemodels pin <model-id>` so sync stops changing it, or "
        "widen auto-selection with `/freemodels mode`. "
        "`/freemodels full` also lists models that were skipped."
    )
    # Budget the footer too, not just the header — it is part of the message
    # Discord counts. The overflow line's presence depends on the trim we are
    # about to compute, so reserve room for its longest form.
    reserved = lines + [
        "",
        f"{len(rows)} more — run `hermes freemodels list` to see them all. {hint}",
    ]
    shown, extra = _fit_rows(rows, "\n".join(reserved))

    lines.append("")
    lines.append(_fence(render.render_snapshots(shown)))

    footer = []
    if extra:
        footer.append(f"{extra} more — run `hermes freemodels list` to see them all.")
    if hint:
        footer.append(hint)
    if footer:
        lines.append("")
        lines.append(" ".join(footer))
    return "\n".join(lines)


def cmd_install_cron(args) -> None:
    try:
        if getattr(args, "system", False):
            line = croninstall.build_cron_line(args.time)
            if not args.apply:
                print("add this line to your crontab (crontab -e), or rerun with --system --apply:")
                print(f"  {line}")
                return
            print(croninstall.install_cron_line(line))
            return
        croninstall.parse_utc_time(args.time)
        if not args.apply:
            script = state_mod.hermes_home() / "scripts" / croninstall.SCRIPT_NAME
            print(f"Would write {script} and create/update Hermes job {croninstall.JOB_NAME!r}.")
            print(f"Daily sync target: {args.time} UTC, year-round; lightweight checks every minute, no agent.")
            print("Would remove this home's legacy crontab entry after successful installation.")
            print("Requires a running gateway; rerun with --apply to install, or --system for crontab.")
            return
        with state_mod.sync_lock() as acquired:
            if not acquired:
                print("another freemodels command is running — retry installation later", file=sys.stderr)
                sys.exit(1)
            print(croninstall.install_hermes_job(args.time))
    except (OSError, ValueError, RuntimeError, croninstall.subprocess.SubprocessError) as exc:
        detail = getattr(exc, "stderr", None) or str(exc)
        if isinstance(exc, FileNotFoundError) and getattr(args, "system", False):
            detail += "; crontab is required for --system; use the default Hermes scheduler instead"
        print(f"error: {detail}", file=sys.stderr)
        sys.exit(1)
