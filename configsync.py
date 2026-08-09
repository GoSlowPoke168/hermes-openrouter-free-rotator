"""Plan and apply the config.yaml mutations.

Two independent writes, each with its own ownership ledger in state.json:

- **Rotation** replaces `model.default` and the fallback entries recorded as
  `managed_fallbacks` (plus, on first run, it adopts any existing openrouter
  `:free` entries as managed). Everything else in `fallback_providers` — e.g.
  hand-added paid last-resort models — is preserved, in order, at the end.
- **Catalog** writes a model-catalog manifest listing the free models ahead of
  Hermes's own curated OpenRouter models, and points
  `model_catalog.providers.openrouter.url` at it with a `file://` URL.

Both land through a single `apply_all()` so the two can never half-apply.

Why a manifest and not `providers.openrouter.models`
----------------------------------------------------
That config key works, but only for the CLI picker. The gateway (Discord,
Telegram) goes through `list_picker_providers()`, which *replaces* the
OpenRouter row's models with `fetch_openrouter_models()` output and so
discards anything merged from config. The manifest feeds
`fetch_openrouter_models()` itself, which both pickers ultimately call — one
mechanism, both surfaces.

The manifest must therefore carry Hermes's curated models too, since it
becomes their source: we re-merge them from the upstream catalog on every
sync. If our file is missing or malformed, Hermes falls back to the upstream
manifest on its own, so the failure mode is "no free models in the picker",
never a broken picker.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from typing import Any

from .selection import Candidate, is_expired
from .state import state_dir

CATALOG_PROVIDER = "openrouter"
# Bounds our contribution. Gateway and desktop callers may pass max_models,
# and our ids lead the list — unbounded, they could evict Hermes's own
# curated models entirely.
CATALOG_MAX_IDS = 60
CATALOG_MANIFEST_NAME = "model-catalog.json"
# Legacy: the key earlier versions wrote. Still read so an upgrade can clean
# it up rather than leaving a stale second copy in the picker.
LEGACY_PROVIDER_KEY = "providers"


def manifest_path():
    return state_dir() / CATALOG_MANIFEST_NAME


def manifest_url() -> str:
    return manifest_path().resolve().as_uri()


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


@dataclass
class CatalogPlan:
    changed: bool = False
    skipped_reason: str | None = None  # non-fatal: rotation still proceeds
    current_ids: list[str] = field(default_factory=list)
    new_ids: list[str] = field(default_factory=list)      # full list to write
    managed_ids: list[str] = field(default_factory=list)  # the subset we own
    user_ids: list[str] = field(default_factory=list)     # hand-added, preserved
    removed_ids: list[str] = field(default_factory=list)  # ours, now gone
    manifest: dict[str, Any] | None = None   # written next to config.yaml's write
    set_url: bool = False                    # config needs the file:// override
    clear_legacy: bool = False               # an old providers.* write to undo


def read_declared_ids(value: Any) -> list[str]:
    """Model ids from any shape Hermes accepts under `providers.<slug>.models`.

    Deliberately mirrors `hermes_cli.model_switch._declared_model_ids` rather
    than importing it: that's a private host symbol, and a version skew there
    would silently corrupt our merge (ids we think we own becoming invisible,
    then getting duplicated). Shapes: bare string, list of strings, list of
    dicts with `id`/`name`, or a mapping keyed by id.
    """
    ids: list[str] = []
    seen: set[str] = set()

    def add(candidate: Any) -> None:
        if not isinstance(candidate, str):
            return
        model_id = candidate.strip()
        if not model_id or model_id.lower() in seen:
            return
        seen.add(model_id.lower())
        ids.append(model_id)

    if isinstance(value, str):
        add(value)
    elif isinstance(value, dict):
        for key in value:
            add(key)
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                add(item.get("id") or item.get("name"))
            else:
                add(item)
    return ids


def upstream_curated() -> list[dict[str, str]]:
    """Hermes's own curated OpenRouter models, from *upstream* only.

    Deliberately calls `get_catalog()` rather than
    `get_curated_openrouter_models()`: the latter honours the very
    per-provider override we install, so it would read our own file back and
    the curated block would compound with each sync.
    """
    try:
        from hermes_cli.model_catalog import get_catalog

        block = (get_catalog() or {}).get("providers", {}).get(CATALOG_PROVIDER)
        models = (block or {}).get("models")
        if isinstance(models, list) and models:
            return [
                {"id": str(m["id"]), "description": str(m.get("description") or "")}
                for m in models
                if isinstance(m, dict) and m.get("id")
            ]
    except Exception:
        pass
    try:
        from hermes_cli.models import OPENROUTER_MODELS

        return [{"id": mid, "description": desc} for mid, desc in OPENROUTER_MODELS]
    except Exception:
        return []


def build_manifest(free_ids: list[str], curated: list[dict[str, str]]) -> dict[str, Any]:
    """Free models first, then whatever upstream still curates.

    Rebuilt from scratch every sync — from the live free list and a fresh
    upstream read, never from our own previous manifest. That's what makes a
    model disappearing actually disappear: nothing carries over. Hermes then
    intersects this against the live OpenRouter catalog anyway, so even a
    stale file cannot surface a model that no longer exists.
    """
    from .state import now_iso

    free_lower = {i.lower() for i in free_ids}
    models = [{"id": i, "description": "free"} for i in free_ids]
    models += [m for m in curated if m["id"].lower() not in free_lower]
    return {
        "version": 1,
        "updated_at": now_iso(),
        "metadata": {
            "generated_by": "hermes-openrouter-free-rotator",
            "free_models": len(free_ids),
        },
        "providers": {CATALOG_PROVIDER: {"models": models}},
    }


def _manifest_ids(manifest: dict[str, Any] | None) -> list[str]:
    block = ((manifest or {}).get("providers") or {}).get(CATALOG_PROVIDER) or {}
    return [
        str(m.get("id"))
        for m in (block.get("models") or [])
        if isinstance(m, dict) and m.get("id")
    ]


def read_manifest() -> dict[str, Any] | None:
    try:
        return json.loads(manifest_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _catalog_url(cfg: dict[str, Any]) -> Any:
    mc = cfg.get("model_catalog")
    if not isinstance(mc, dict):
        return None
    providers = mc.get("providers")
    if not isinstance(providers, dict):
        return None
    entry = providers.get(CATALOG_PROVIDER)
    return entry.get("url") if isinstance(entry, dict) else None


def build_catalog_plan(
    cfg: dict[str, Any],
    catalog: list[Candidate],
    state: dict[str, Any],
    *,
    remove: bool = False,
) -> CatalogPlan:
    """Plan the manifest write plus its config pointer.

    Intentionally independent of the rotation guard: this list feeds exactly
    one consumer — a picker row — and participates in no routing decision, so
    it stays useful (and safe) even for a user whose model.provider has moved
    off OpenRouter.
    """
    plan = CatalogPlan()
    previously_managed = [
        i for i in (state.get("managed_catalog") or []) if isinstance(i, str)
    ]
    plan.managed_ids = [] if remove else [c.id for c in catalog][:CATALOG_MAX_IDS]
    managed_lower = {i.lower() for i in plan.managed_ids}
    # A model we listed last time that is no longer in the live free list is
    # simply absent from the freshly built manifest — nothing to delete.
    plan.removed_ids = [i for i in previously_managed if i.lower() not in managed_lower]

    existing = read_manifest()
    plan.current_ids = _manifest_ids(existing)

    if plan.managed_ids:
        plan.manifest = build_manifest(plan.managed_ids, upstream_curated())
        plan.new_ids = _manifest_ids(plan.manifest)
        plan.set_url = _catalog_url(cfg) != manifest_url()
    else:
        # Nothing to offer: drop the pointer so Hermes goes back to upstream.
        plan.manifest = None
        plan.new_ids = []
        plan.set_url = False

    legacy_ids = _legacy_ids(cfg)
    plan.clear_legacy = bool(legacy_ids) and any(
        i.lower() in {j.lower() for j in previously_managed} for i in legacy_ids
    )
    plan.user_ids = [
        i for i in legacy_ids
        if i.lower() not in {j.lower() for j in previously_managed}
    ]

    manifest_changed = plan.new_ids != plan.current_ids
    url_changed = plan.set_url or (not plan.managed_ids and _catalog_url(cfg) is not None)
    plan.changed = manifest_changed or url_changed or plan.clear_legacy
    return plan


def _legacy_ids(cfg: dict[str, Any]) -> list[str]:
    """Ids an earlier version wrote to providers.openrouter.models."""
    providers = cfg.get(LEGACY_PROVIDER_KEY)
    if not isinstance(providers, dict):
        return []
    entry = providers.get(CATALOG_PROVIDER)
    if not isinstance(entry, dict):
        return []
    return read_declared_ids(entry.get("models"))


def _prune_empty(container: dict[str, Any], path: list[str]) -> None:
    """Delete `path`, then any ancestors it leaves empty.

    Stops the moment a level still holds something of the user's — so a
    sibling `model_catalog.enabled` or a hand-added provider entry keeps its
    parent alive.
    """
    parents = [container]
    for key in path[:-1]:
        nxt = parents[-1].get(key)
        if not isinstance(nxt, dict):
            return
        parents.append(nxt)
    parents[-1].pop(path[-1], None)
    for parent, key in zip(reversed(parents[:-1]), reversed(path[:-1])):
        if parent.get(key) == {}:
            parent.pop(key, None)
        else:
            break


def _mutate_catalog(plan: CatalogPlan, cfg: dict[str, Any]) -> None:
    """Point model_catalog at our manifest, and undo any legacy write.

    Only the one `url` key is touched — `model_catalog.enabled`, `ttl_hours`
    and any other provider override ride through untouched.
    """
    if plan.set_url:
        mc = cfg.setdefault("model_catalog", {})
        if not isinstance(mc, dict):
            plan.skipped_reason = "config `model_catalog:` is not a mapping — leaving it alone"
            return
        providers = mc.setdefault("providers", {})
        if not isinstance(providers, dict):
            plan.skipped_reason = (
                "config `model_catalog.providers:` is not a mapping — leaving it alone"
            )
            return
        entry = providers.setdefault(CATALOG_PROVIDER, {})
        if not isinstance(entry, dict):
            plan.skipped_reason = (
                f"config `model_catalog.providers.{CATALOG_PROVIDER}:` is not a mapping"
            )
            return
        entry["url"] = manifest_url()
    elif not plan.managed_ids:
        # Removing: take the pointer out so Hermes falls back to upstream.
        _prune_empty(cfg, ["model_catalog", "providers", CATALOG_PROVIDER, "url"])

    if plan.clear_legacy:
        _mutate_legacy(plan, cfg)


def _mutate_legacy(plan: CatalogPlan, cfg: dict[str, Any]) -> None:
    """Remove ids an earlier version wrote to providers.openrouter.models.

    Anything the user put there themselves stays, exactly as before — only
    entries recorded in our ledger are dropped.
    """
    providers = cfg.get(LEGACY_PROVIDER_KEY)
    if not isinstance(providers, dict):
        return
    entry = providers.get(CATALOG_PROVIDER)
    if not isinstance(entry, dict):
        return
    if plan.user_ids:
        entry["models"] = list(plan.user_ids)
        return
    entry.pop("models", None)
    if not entry:
        providers.pop(CATALOG_PROVIDER, None)
    if not providers:
        cfg.pop(LEGACY_PROVIDER_KEY, None)


def write_manifest(plan: CatalogPlan) -> None:
    """Persist (or delete) the manifest file itself."""
    path = manifest_path()
    if plan.manifest is None:
        path.unlink(missing_ok=True)
        return
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(plan.manifest, indent=2), encoding="utf-8")
    tmp.replace(path)


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


def _mutate_rotation(plan: SyncPlan, cfg: dict[str, Any]) -> None:
    cfg.setdefault("model", {})["default"] = plan.new_default
    cfg["fallback_providers"] = plan.new_chain


def apply_all(
    cfg: dict[str, Any],
    rotation: SyncPlan | None = None,
    catalog: CatalogPlan | None = None,
) -> None:
    """Backup config.yaml, apply whichever plans are present, save once.

    A single backup and a single atomic save for both plans — two writes
    would risk a torn state where the rotation landed but the catalog didn't
    (or vice versa), leaving state.json's two ledgers disagreeing with disk.
    """
    from hermes_cli.config import get_config_path, save_config

    if rotation is None and catalog is None:
        return

    try:
        shutil.copy2(get_config_path(), state_dir() / "config.yaml.pre-sync.bak")
    except OSError:
        pass  # backup is best-effort; save_config itself is atomic

    if rotation is not None:
        _mutate_rotation(rotation, cfg)
    if catalog is not None:
        # Manifest first: if the config write then fails, Hermes reads a file
        # that exists. The reverse order would point config at a missing file.
        write_manifest(catalog)
        _mutate_catalog(catalog, cfg)
    save_config(cfg)

    if catalog is not None and catalog.managed_ids:
        # The picker caches provider model lists for an hour; without this the
        # new list wouldn't appear until that expired.
        try:
            from hermes_cli.models import clear_provider_models_cache

            clear_provider_models_cache(CATALOG_PROVIDER)
        except Exception:
            pass
