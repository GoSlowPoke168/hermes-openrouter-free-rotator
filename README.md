# hermes-openrouter-free-rotator

A [Hermes Agent](https://hermes-agent.nousresearch.com) plugin that keeps your
default and fallback models pointed at the best **free**, **privacy-respecting**
OpenRouter models — automatically.

Free (`:free`) OpenRouter models expire and disappear without notice. Once a day
this plugin ranks the [free-models collection](https://openrouter.ai/collections/free-models),
drops anything unsuitable, and rewrites your Hermes config:

- best pick → `model.default`
- next two → the leading `fallback_providers` (up to three when another model is pinned)
- your other fallbacks are preserved after them — the plugin only ever
  touches managed `:free` entries (the first sync adopts existing OpenRouter free fallbacks)

Config writes happen only when the selection or picker catalog changes (unless forced), so a model expiring
tomorrow is replaced *before* it goes dark. You can also
[pick a model yourself](#choosing-a-model-yourself) or
[change which privacy tiers qualify](#selection-modes).

## Platform support

| Platform | Notes |
|----------|-------|
| ![Linux](https://img.shields.io/badge/Linux-supported-brightgreen?logo=linux&logoColor=white) | Supported and tested. |
| ![Windows](https://img.shields.io/badge/Windows-experimental-yellow) | Experimental; [native Windows smoke test](https://github.com/GoSlowPoke168/hermes-openrouter-free-rotator/actions/runs/38112733783) passed. Windows 10 remains untested. |

If you encounter any issues, please
[open a GitHub issue](https://github.com/GoSlowPoke168/hermes-openrouter-free-rotator/issues).

## Install

> [!IMPORTANT]
> Set up the OpenRouter provider in Hermes first, with an API key added, so that
> `model.provider` is `openrouter`. If it's anything else `sync` refuses rotation, but
> can still update the picker catalog — rotation only manages a setup you've already pointed
> at OpenRouter, it won't switch providers for you. `fallback_providers` doesn't
> need to exist beforehand; it's created if missing.

### Installing with Hermes Plugin Manager
```bash
hermes plugins install GoSlowPoke168/hermes-openrouter-free-rotator
hermes plugins enable hermes-openrouter-free-rotator
```

### Installing Manually
```bash
# Bash setup — clone anywhere, then install into ~/.hermes/plugins/
git clone https://github.com/GoSlowPoke168/hermes-openrouter-free-rotator
cd hermes-openrouter-free-rotator && ./install.sh

# Dev mode — symlink your checkout instead of copying
./install.sh --symlink
```

Requires **Hermes >=0.21.0**. The Bash installer enables the plugin; the plugin-manager
route also works in PowerShell without Bash. State is created when the plugin is used.

**Restart your
Hermes session (or gateway)** to pick up the `freemodels` and `/freemodels`
commands — they register at plugin load. Then:

```bash
hermes freemodels sync                   # apply the best selection now
hermes freemodels install-cron --apply   # daily at 00:01 UTC through Hermes
hermes cron status                       # check gateway/ticker health
```

Daily sync uses Hermes cron without an agent or LLM. **Keep the gateway running**
with `hermes gateway install` for auto-start or `hermes gateway run` in the foreground.
It catches up after downtime and retries failures; `--time HH:MM` changes the UTC target.
Requires script-only cron support. Installing the job migrates this home's old crontab entry.

On Linux, `install-cron --system --apply` opts into system cron. Remove the Hermes
job first (`hermes cron remove freemodels-sync`); rerun system setup after DST changes.

## Commands

```bash
hermes freemodels list                  # ranked table: tier, endpoint, uptime, status
hermes freemodels sync                  # apply the best selection (idempotent)
hermes freemodels sync --dry-run        # preview the change without writing
hermes freemodels sync --no-catalog     # rotate only; leave the /model list alone
hermes freemodels status                # current selection, mode, pin, last sync, last error
hermes freemodels pin <model-id>        # switch to a model and stop rotating it
hermes freemodels pin                   # pin the current config.yaml default
hermes freemodels unpin                 # resume automatic rotation
hermes freemodels mode [name]           # show/set which privacy tiers may be picked
hermes freemodels remove-catalog        # drop the free models from /model
hermes freemodels install-cron --apply   # Hermes scheduling (00:01 UTC; --time HH:MM)
hermes freemodels install-cron --system --apply  # opt into system crontab
```

Inside a Hermes session, `/freemodels` prints the same table (`/freemodels full`
includes skipped models, `/freemodels json` the raw snapshot). It reads the last
`sync` or `list` results — never the network mid-conversation — and says when
they've gone stale. `/freemodels pin <model-id>`, `/freemodels unpin` and
`/freemodels mode <name>` work from chat too, so you don't need a shell.

On Discord, use `/freemodels`'s **args** field for `full`, `pin <model-id>`,
`unpin`, or `mode <name>`. After a plugin install/update, restart the gateway and
allow a few minutes for Discord's command menu to synchronize.

## Choosing a model yourself

### Browsing free models

Hermes's OpenRouter picker normally uses a curated catalog, so free models may
be missing from the menu (explicit IDs remain usable). By default, `sync` maintains its own
manifest (`~/.hermes/freemodels/model-catalog.json` — free models first, then
Hermes's curated ones) and points `model_catalog.providers.openrouter.url` at it:

```
/model  →  OpenRouter (45 models)  →  13 free first, then Hermes's own 32
```

> [!IMPORTANT]
> **Automatic rotation needs no gateway restart or picker refresh.** With daily
> scheduling enabled and the gateway running, chats using the global default
> follow the updated config. Explicit chat model selections and existing CLI
> sessions may keep their current model.
>
> - **Browsing stale picker results?** Let the gateway refresh its catalog, then
>   use `/model --refresh` (Discord: `/model` → **name** = `--refresh`). It opens
>   the picker; restarting alone may still use old cache.
> - **Installed or updated the plugin?** Restart your session/gateway to load it.
>
> `/freemodels`, `list`, and `status` warn about managed models expiring within
> **48 hours**, using saved expiry dates.

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
- Opt out per run with `sync --no-catalog`, or drop the list entirely with `hermes freemodels remove-catalog` (deletes the manifest and config pointer; Hermes caches may take time to refresh).

### Pinning a model

Changing the configured default by hand isn't enough on its own — the next sync rotates
`model.default` straight back. Pin it to stop that:

```bash
hermes freemodels pin cohere/north-mini-code:free   # switch to it and hold it
hermes freemodels pin                               # hold the configured default
hermes freemodels unpin                             # back to automatic
```

- Pinning **updates the configured default as well as freezing it** — one step, not a setting that waits for the next sync.
- Only `model.default` is frozen. The fallback chain keeps rotating, when qualifying models are available, and the pinned model is dropped from its own fallbacks.
- If a pinned `:free` model expires or stops being offered, a successful sync with qualifying replacements drops the pin, resumes rotation, and records why — shown in `status` and `/freemodels`. Non-free pins are allowed and never auto-clear; their availability is not checked.
- IDs are checked against the generated `/model` catalog when available (`--force` overrides); otherwise pinning proceeds with a verification warning.

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
- **`strict` can leave you with fewer fallbacks** — if only one private model exists you get a default and no free fallbacks, and if none exists sync refuses rotation (the picker catalog can still update). That's deliberate.
- `unknown` is never selectable in any mode: the privacy policy could not be established. Those models stay pickable by hand in `/model`.
- A mode takes effect at the next `sync`, not immediately. A [pin](#pinning-a-model) overrides the mode for `model.default`; the fallback chain still follows it.

## What it changes in your config

Rotation changes `model.default` and managed OpenRouter `:free` fallbacks.
Catalog management also updates `model_catalog.providers.openrouter.url`
and removes old plugin-owned picker entries. Other fallbacks are preserved:

```yaml
fallback_providers:
  - provider: openrouter          # managed — rewritten every sync
    model: cohere/north-mini-code:free
  - provider: openrouter          # managed — rewritten every sync
    model: openai/gpt-oss-20b:free
  - provider: google              # yours — never touched, always kept after ours
    model: gemini-3.1-flash-lite
```

Ownership is tracked in the plugin's state file. The first sync adopts existing
OpenRouter `:free` fallbacks; later runs match owned entries by provider and model ID.

## How it works

A free model is eligible only if it is:

| Criterion | Rule | Source |
|-----------|------|--------|
| **Right privacy tier** | Which tiers qualify is set by the [selection mode](#selection-modes). By default: private first, falling back to **Logs** (retains but doesn't train) only if fewer than 3 private models exist, and never **Trains**. | Not in the public API — the plugin reads each candidate's model page for its free endpoint's data policy. If several providers serve a model free, the least private one decides its tier. Cached 24h. |
| **Tool-capable** | The `:free` model reports support for `tools` — Hermes is an agent, so a model that can't call tools can't be the default. | Models API (`/api/v1/models`) |
| **Not expiring** | Expiry dates on or before tomorrow UTC are skipped (a calendar-day buffer). | Models API |
| **Available** | At least one free endpoint has day-long uptime of 20% or higher (or healthy status if uptime is unavailable). Lenient by design (a brief outage won't drop a model) and re-checked daily, so a recovered model returns automatically. | Endpoints API (`/api/v1/models/<id>/endpoints`, `uptime_last_1d`), checked fresh every run and evaluated *before* the privacy scrape so down models are dropped cheaply. |

Eligible models are ranked by the collection page's real-usage order. Whether
privacy tier outranks that order is what the [selection mode](#selection-modes)
decides. If the collection page can't be read, ranking falls back to newest-first.

## Files

State and logs live under `~/.hermes/freemodels/` (or your profile's Hermes home):

| File | Purpose |
|------|---------|
| `state.json` | Ownership ledgers (rotation + `/model` catalog), pin, mode, privacy cache, last table snapshot |
| `model-catalog.json` | Generated catalog manifest feeding Hermes's `/model` picker |
| `freemodels.log` | Rotating activity log |
| `cron.log` | Output of scheduled runs |
| `config.yaml.pre-sync.bak` | Backup taken before every config write |

The scheduler script lives at `$HERMES_HOME/scripts/freemodels_sync.py`;
Hermes owns its job database and execution history under `$HERMES_HOME/cron/`.

## Uninstall

```bash
./uninstall.sh --standard # remove plugin; keep state and schedules
./uninstall.sh --purge    # also remove state, schedules, and generated script
```

Both attempt to remove the managed picker catalog first (subject to Hermes caches). Your `model.default` and
fallback chain are left as-is.

These scripts require Bash; purge also needs Python 3 and Hermes for an installed
job. Standard uninstall keeps schedules, which cannot sync until reinstallation.

## Permissions

**This plugin requests no privileged capabilities.** It registers a CLI command
and a slash command through public APIs, fetches public OpenRouter data over HTTPS,
and writes the profile's `config.yaml`, picker catalog, state, logs, and backups.
Scheduling writes the script, reads the job database, and invokes Hermes cron;
migration and `--system` also invoke `crontab`. Bash setup changes plugin files
and enablement. No prompts are sent; it reads no credential files, stores no
keys, and has no telemetry. Host-reported authentication is advisory only.

The plugin does not update itself. Hermes-managed Git installs can use
`hermes plugins update hermes-openrouter-free-rotator`.

```bash
hermes plugins capabilities hermes-openrouter-free-rotator
```

## Support the Project
If you find this plugin useful, please leave a star on GitHub or consider supporting its development!

[![ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/T5T725W4FX)

## License

[![GitHub License](https://img.shields.io/github/license/goslowpoke168/hermes-openrouter-free-rotator?style=for-the-badge&logo=data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIyNCIgaGVpZ2h0PSIyNCIgdmlld0JveD0iMCAwIDI0IDI0IiBmaWxsPSJub25lIiBzdHJva2U9IiNmZmZmZmYiIHN0cm9rZS13aWR0aD0iMiIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5kIiBjbGFzcz0ibHVjaWRlIGx1Y2lkZS1zY2FsZSI+PHBhdGggZD0ibTE2IDE2IDMtOCAzIDhjLS44Ny42NS0xLjkyIDEtMyAxcy0yLjEzLS4zNS0zLTFaIi8+PHBhdGggZD0ibTIgMTYgMy04IDMgOGMtLjg3LjY1LTEuOTIgMS0zIDFzLTIuMTMtLjM1LTMtMVoiLz48cGF0aCBkPSJNNyAyMWgxMCIvPjxwYXRoIGQ9Ik0xMiAzdjE4Ii8+PHBhdGggZD0iTTMgN2gyYzIgMCA1LTEgNy0yIDIgMSA1IDIgNyAyaDIiLz48L3N2Zz4=)](https://github.com/GoSlowPoke168/hermes-openrouter-free-rotator/blob/main/LICENSE)
