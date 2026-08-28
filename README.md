# hermes-openrouter-free-rotator

A [Hermes Agent](https://hermes-agent.nousresearch.com) plugin that keeps your
default and fallback models pointed at the best **free**, **privacy-respecting**
OpenRouter models — automatically.

Free (`:free`) OpenRouter models expire and disappear without notice. Once a day
this plugin ranks the [free-models collection](https://openrouter.ai/collections/free-models),
drops anything unsuitable, and rewrites your Hermes config:

- best pick → `model.default`
- next two → the leading `fallback_providers`
- anything you added yourself is preserved after them — the plugin only ever
  touches the `:free` entries it owns

Writes happen only when the selection actually changes, so a model expiring
tomorrow is replaced *before* it goes dark. You can also
[pick a model yourself](#choosing-a-model-yourself) or
[change which privacy tiers qualify](#selection-modes).

## Install

> [!IMPORTANT]
> Set up the OpenRouter provider in Hermes first, with an API key added, so that
> `model.provider` is `openrouter`. If it's anything else `sync` aborts without
> touching your config — the plugin only manages a setup you've already pointed
> at OpenRouter, it won't switch providers for you. `fallback_providers` doesn't
> need to exist beforehand; it's created if missing.

```bash
# Portable — clone anywhere, then install into ~/.hermes/plugins/
git clone https://github.com/GoSlowPoke168/hermes-openrouter-free-rotator
cd hermes-openrouter-free-rotator && ./install.sh

# Dev mode — symlink your checkout instead of copying
./install.sh --symlink

# Via the Hermes plugin manager
hermes plugins install GoSlowPoke168/hermes-openrouter-free-rotator
bash ~/.hermes/plugins/hermes-openrouter-free-rotator/install.sh
```

The installer enables the plugin and creates its state directory. **Restart your
Hermes session (or gateway)** to pick up the `freemodels` and `/freemodels`
commands — they register at plugin load. Then:

```bash
hermes freemodels sync                  # apply the best selection now
hermes freemodels install-cron --apply  # and keep it fresh daily
```

## Commands

```bash
hermes freemodels list                  # ranked table: tier, endpoint, uptime, status
hermes freemodels sync                  # apply the best selection (idempotent)
hermes freemodels sync --dry-run        # preview the change without writing
hermes freemodels sync --no-catalog     # rotate only; leave the /model list alone
hermes freemodels status                # current selection, mode, pin, last sync, last error
hermes freemodels pin <model-id>        # switch to a model and stop rotating it
hermes freemodels pin                   # pin whatever you already picked in /model
hermes freemodels unpin                 # resume automatic rotation
hermes freemodels mode [name]           # show/set which privacy tiers may be picked
hermes freemodels remove-catalog        # drop the free models from /model
hermes freemodels install-cron --apply  # run daily (default 00:01 UTC; --time HH:MM)
```

Inside a Hermes session, `/freemodels` prints the same table (`/freemodels full`
includes skipped models, `/freemodels json` the raw snapshot). It reads the last
sync's cached results — never the network mid-conversation — and says when
they've gone stale. `/freemodels pin <model-id>`, `/freemodels unpin` and
`/freemodels mode <name>` work from chat too, so you don't need a shell.

## Choosing a model yourself

### Browsing free models

Hermes's `/model` picker only offers OpenRouter models from a curated catalog, so
free ones outside it are normally unreachable by hand. Each `sync` writes its own
manifest (`~/.hermes/freemodels/model-catalog.json` — free models first, then
Hermes's curated ones) and points `model_catalog.providers.openrouter.url` at it:

```
/model  →  OpenRouter (45 models)  →  13 free first, then Hermes's own 32
```

> [!IMPORTANT]
> **Restart Hermes before picking a free model — `/model` won't show them
> otherwise.** Hermes caches the catalog for the life of the process, so any
> session or gateway already running when `sync` wrote the list keeps showing the
> old one.
>
> ```
> /restart                # inside a session (CLI, Discord, Telegram)
> hermes gateway restart  # from a shell, for the gateway
> ```
>
> Auto model rotation is unaffected: `model.default` is read from config.yaml per
> request, so a rotated default applies with no restart.

For the labelled view use `/freemodels` in a session, or `hermes freemodels list`:

```
Rank  Model                                               Privacy  Served by          Uptime  Status
----------------------------------------------------------------------------------------------------
 #13  z-ai/glm-5.2:free                                   Private  Decart               100%  ← Default
 #15  inclusionai/ling-3.0-flash-fin:free                 Private  NovitaAI             100%  ← Fallback #1
  #4  minimax/minimax-m3:free                             Logs     GMICloud             100%  ← Fallback #2
  #6  cohere/north-mini-code:free                         Logs     Cohere                98%  Retains prompts
 ...
  #1  nvidia/nemotron-3-ultra-550b-a55b:free              Trains   NVIDIA                99%  Trains on prompts
  #2  poolside/laguna-s-2.1:free                          Trains   Poolside             100%  Trains on prompts
 ...
```

- Ordered best→worst: `private` → `logs` → `trains` → `unknown` → currently down, with popularity breaking ties inside each tier.
- Opt out per run with `sync --no-catalog`, or drop the list entirely with `hermes freemodels remove-catalog` (deletes the manifest and config pointer, restoring the picker exactly).

### Pinning a model

Picking a model in `/model` isn't enough on its own — the next sync rotates
`model.default` straight back. Pin it to stop that:

```bash
hermes freemodels pin cohere/north-mini-code:free   # switch to it and hold it
hermes freemodels pin                               # hold what you already picked
hermes freemodels unpin                             # back to automatic
```

- Pinning **switches as well as freezes** — one step, not a setting that waits for the next sync.
- Only `model.default` is frozen. The fallback chain keeps rotating, so a pin never costs you a live safety net, and the pinned model is dropped from its own fallbacks.
- **A pin can't strand you:** if a pinned `:free` model expires or stops being offered, the next sync drops the pin, resumes rotation, and records why — shown in `status` and `/freemodels`. Paid models are pinnable too and never auto-clear, since they don't expire.
- Typos are rejected — ids are checked against the `/model` catalog (`--force` overrides).

## Selection modes

How much privacy to trade for a better-ranked model. Auto-selection only —
`/model` always lists every tier regardless.

```bash
hermes freemodels mode           # show the current mode
hermes freemodels mode relaxed   # set one  (also /freemodels mode relaxed)
```

| Mode | Tiers it may pick | Tiebreak |
|------|-------------------|----------|
| `strict` | private | — |
| `balanced` *(default)* | private, logs | privacy — every private model beats every logs one |
| `relaxed` | private, logs | **rank** — a popular logs model can beat a private one |
| `loose` | private, logs, **trains** | **rank** |

- `balanced` and `relaxed` allow the same tiers and differ *only* in what breaks the tie.
- **`loose` can send your prompts to models that train on them.** It's shown in `status` and in the `/freemodels` header so it's never a silent state.
- **`strict` can leave you with fewer fallbacks** — if only one private model exists you get a default and no free fallbacks, and if none exists the sync leaves your config untouched. That's deliberate.
- `unknown` is never selectable in any mode: it means the privacy scrape failed, not that a model is a particular kind. Those models stay pickable by hand in `/model`.
- A mode takes effect at the next `sync`, not immediately. A [pin](#pinning-a-model) overrides the mode for `model.default`; the fallback chain still follows it.

## What it changes in your config

Only `model.default` and the `openrouter` `:free` entries the plugin wrote
itself. Everything else is left alone:

```yaml
fallback_providers:
  - provider: openrouter          # managed — rewritten every sync
    model: cohere/north-mini-code:free
  - provider: openrouter          # managed — rewritten every sync
    model: openai/gpt-oss-20b:free
  - provider: google              # yours — never touched, always kept after ours
    model: gemini-3.1-flash-lite
```

Ownership is tracked in the plugin's own state file, so an entry you add by hand
is never mistaken for one of its own.

## How it works

A free model is eligible only if it is:

| Criterion | Rule | Source |
|-----------|------|--------|
| **Right privacy tier** | Which tiers qualify is set by the [selection mode](#selection-modes). By default: private first, falling back to **Logs** (retains but doesn't train) only if fewer than 3 private models exist, and never **Trains**. | Not in the public API — the plugin reads each candidate's model page for its free endpoint's data policy. If several providers serve a model free, the least private one decides its tier. Cached 24h. |
| **Tool-capable** | The free endpoint supports `tools` — Hermes is an agent, so a model that can't call tools can't be the default. | Models API (`/api/v1/models`) |
| **Not expiring** | Models expiring within 1 day are skipped. | Models API |
| **Available** | The free endpoint's day-long uptime is above ~20%. Lenient by design (a brief outage won't drop a model) and re-checked daily, so a recovered model returns automatically. | Endpoints API (`/api/v1/models/<id>/endpoints`, `uptime_last_1d`), checked fresh every run and evaluated *before* the privacy scrape so down models are dropped cheaply. |

Eligible models are ranked by the collection page's real-usage order. Whether
privacy tier outranks that order is what the [selection mode](#selection-modes)
decides. If the collection page can't be read, ranking falls back to newest-first.

## Files

Everything lives under `~/.hermes/freemodels/`:

| File | Purpose |
|------|---------|
| `state.json` | Ownership ledgers (rotation + `/model` catalog), pin, mode, privacy cache, last table snapshot |
| `model-catalog.json` | Generated catalog manifest feeding Hermes's `/model` picker |
| `freemodels.log` | Rotating activity log |
| `cron.log` | Output of scheduled runs |
| `config.yaml.pre-sync.bak` | Backup taken before every config write |

## Uninstall

```bash
./uninstall.sh           # remove the plugin, keep state
./uninstall.sh --purge   # also remove the state dir and crontab entry
```

Both remove the free models from `/model` first. Your `model.default` and
fallback chain are left as-is.

## Permissions

**This plugin requests no privileged capabilities.** It registers a CLI command
and a slash command, reads OpenRouter's public API, and writes `config.yaml` and
its own state directory - all unprivileged.

```bash
hermes plugins capabilities hermes-openrouter-free-rotator
```

## Support the Project
If you find this plugin useful, please leave a star on GitHub or consider supporting its development!

[![Ko-fi](https://img.shields.io/badge/Ko--fi-F16061?logo=ko-fi&logoColor=white&style=for-the-badge)](https://ko-fi.com/jeremyhou)

## License

[![GitHub License](https://img.shields.io/github/license/goslowpoke168/hermes-openrouter-free-rotator?style=for-the-badge&logo=data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIyNCIgaGVpZ2h0PSIyNCIgdmlld0JveD0iMCAwIDI0IDI0IiBmaWxsPSJub25lIiBzdHJva2U9IiNmZmZmZmYiIHN0cm9rZS13aWR0aD0iMiIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5kIiBjbGFzcz0ibHVjaWRlIGx1Y2lkZS1zY2FsZSI+PHBhdGggZD0ibTE2IDE2IDMtOCAzIDhjLS44Ny42NS0xLjkyIDEtMyAxcy0yLjEzLS4zNS0zLTFaIi8+PHBhdGggZD0ibTIgMTYgMy04IDMgOGMtLjg3LjY1LTEuOTIgMS0zIDFzLTIuMTMtLjM1LTMtMVoiLz48cGF0aCBkPSJNNyAyMWgxMCIvPjxwYXRoIGQ9Ik0xMiAzdjE4Ii8+PHBhdGggZD0iTTMgN2gyYzIgMCA1LTEgNy0yIDIgMSA1IDIgNyAyaDIiLz48L3N2Zz4=)](https://github.com/GoSlowPoke168/hermes-openrouter-free-rotator/blob/main/LICENSE)
