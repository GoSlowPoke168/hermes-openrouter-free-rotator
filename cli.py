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
    )
    state_mod.prune_privacy_cache(state, {c.base_slug for c in result.candidates})
    return result, {str(m["id"]): m for m in api_models}


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


def _ordered_snapshots(result: selection.SelectionResult) -> list[dict[str, Any]]:
    """Catalog order (best→worst) first, then everything excluded from it."""
    rows = [_snapshot_row(c) for c in result.catalog]
    rest = [c for c in result.candidates if not c.in_catalog]
    rest.sort(key=lambda c: (c.rank is None, c.rank or 0))
    rows.extend(_snapshot_row(c) for c in rest)
    return rows


def _catalog_will_be_honored() -> tuple[bool, str]:
    """Mirror Hermes's credential gate for the picker row we extend.

    `providers.<slug>.models` is merged in exactly one place — section 1 of
    ``list_authenticated_providers`` — and only when that provider looks
    authenticated: an API-key env var, or a credential pool with at least one
    key not exhausted. If neither holds, a *different* code path emits the
    OpenRouter row and our list is dropped with no error anywhere.

    This is a heuristic mirror of host-private logic, so it is phrased as
    "may" and must never gate the write: wrongly withholding the catalog
    would be worse than the silent-ignore it warns about.
    """
    try:
        if os.environ.get("OPENROUTER_API_KEY"):
            return True, ""
    except Exception:
        pass
    try:
        from agent.credential_pool import load_pool

        pool = load_pool(configsync.CATALOG_PROVIDER)
        if pool.has_credentials():
            if pool.has_available():
                return True, ""
            return False, (
                "every pooled OpenRouter key is currently exhausted or in cooldown, "
                "so Hermes may fall back to its built-in model list and ignore the "
                "free models until one recovers."
            )
    except Exception:
        # Can't tell (older host, import failure) — assume fine rather than
        # nag on every run.
        return True, ""
    return False, (
        "Hermes only merges this list into /model when it sees an OPENROUTER_API_KEY "
        "env var or a usable credential-pool entry, and neither was found — /model may "
        "show the built-in OpenRouter list instead. Fix: export OPENROUTER_API_KEY, or "
        "run `hermes auth add openrouter`."
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


def cmd_sync(args) -> None:
    log = state_mod.get_logger()
    state = state_mod.load_state()
    with state_mod.sync_lock() as acquired:
        if not acquired:
            print("another sync is already running — skipping this run")
            return
        _sync(args, state, log)


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
        _fail(state, log, "no qualifying free models (tools + not expiring + private/logs tier)")
        return

    plan = configsync.build_plan(
        cfg, result.selected, state,
        force=args.force, today=_today(), free_models_by_id=by_id,
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
        "reasons": plan.reasons,
    }

    if not (plan.changed or catalog_changed):
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
    state_mod.save_state(state)  # persist warmed privacy cache + snapshot

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
    print(render.render_snapshots(_ordered_snapshots(result)))
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

    Reads the state.json snapshot only: this runs inside an interactive turn,
    where a network round-trip would stall the session and a config write
    would be an invisible side effect of sending a chat message. The
    staleness banner names the command to run instead.
    """
    arg = (raw_args or "").strip().lower()
    state = state_mod.load_state()
    snapshot = state.get("catalog") or {}
    rows = snapshot.get("rows") or []

    if arg == "json":
        return _fence(json.dumps(snapshot, indent=2))

    if not rows:
        return "**Free OpenRouter Models**\nNo data yet — run `hermes freemodels sync`."

    if arg != "full":
        rows = [r for r in rows if r.get("in_catalog")]

    last_sync = state.get("last_sync")
    scope = "All candidates" if arg == "full" else "Available in /model"
    lines = [
        "**Free OpenRouter Models**",
        f"{scope}: {len(rows)} · Last synced {_age_str(last_sync)}",
    ]

    notes: list[str] = []
    if _is_stale(last_sync):
        notes.append("⚠️ Data is stale — run `hermes freemodels sync` to refresh.")
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

    shown, extra = _fit_rows(rows, "\n".join(lines))
    lines.append("")
    lines.append(_fence(render.render_snapshots(shown)))

    footer = []
    if extra:
        footer.append(f"{extra} more — run `hermes freemodels list` to see them all.")
    if arg != "full":
        footer.append(
            "Select one with `/model` → OpenRouter. "
            "`/freemodels full` also lists models that were skipped."
        )
    if footer:
        lines.append("")
        lines.append(" ".join(footer))
    return "\n".join(lines)


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
