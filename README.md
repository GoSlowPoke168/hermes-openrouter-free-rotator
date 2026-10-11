# hermes-openrouter-free-rotator

A [Hermes Agent](https://hermes-agent.nousresearch.com) plugin that keeps your
default and fallback models pointed at the best **free**, **privacy-respecting**
OpenRouter models — automatically.

Free (`:free`) OpenRouter models expire and disappear without notice. Once a day
this plugin ranks the [free-models collection](https://openrouter.ai/collections/free-models),
drops anything unsuitable, and rewrites your Hermes config:

- best pick → `model.default`
- next two → the leading `fallback_providers` (up to three while another model is pinned)
- your other fallbacks are preserved after them. On the first sync, existing
  OpenRouter `:free` fallbacks are adopted; later syncs use the ownership ledger

Config writes happen only when the selection or picker catalog changes (unless
forced). Daily sync skips models with expiry dates on or before tomorrow UTC,
so scheduled runs can replace them before expiry. You can also
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
> `model.provider` is `openrouter`. Otherwise `sync` refuses model rotation, but
> can still update the picker catalog unless `--no-catalog` is used. The plugin
> won't switch providers for you. `fallback_providers` doesn't
> need to exist beforehand; it's created if missing.

```bash
# Bash setup — clone anywhere, then install into ~/.hermes/plugins/
git clone https://github.com/GoSlowPoke168/hermes-openrouter-free-rotator
cd hermes-openrouter-free-rotator && ./install.sh

# Dev mode — symlink your checkout instead of copying
./install.sh --symlink

# Via the Hermes plugin manager (also works in Windows PowerShell)
hermes plugins install GoSlowPoke168/hermes-openrouter-free-rotator
hermes plugins enable hermes-openrouter-free-rotator
```

Bash is required only for the shell setup/uninstall scripts. The plugin-manager
route needs no Bash setup step; the plugin creates its state directory when used.
Requires **Hermes >=0.21.0** with script-only cron support for Hermes scheduling.
Paths below assume the default Hermes home; profiles use their own home.

The Bash installer enables the plugin and creates its state directory. **Restart your
Hermes session (or gateway)** to pick up the `freemodels` and `/freemodels`
commands — they register at plugin load. Then:

```bash
hermes freemodels sync                  # apply the best selection now
hermes freemodels install-cron --apply  # and keep it fresh daily through Hermes
hermes cron status                    # check that the gateway/ticker is running
```

## Daily scheduling

Enable daily sync at **00:01 UTC** (including across daylight-saving changes):

```bash
hermes freemodels install-cron --apply
hermes cron status
```

**Keep the gateway running** with `hermes gateway install` for auto-start or
`hermes gateway run` in the foreground. Sync runs without an agent or LLM,
catches up after downtime, and retries failures. Timing follows the gateway tick.

Use `--time HH:MM` to change the UTC target; omit `--apply` to preview.
Requires Hermes's script-only cron support. Installing the job migrates this
home's legacy crontab entry, warning if cleanup fails.

On Linux, `--system --apply` opts into system cron. Remove the Hermes job first
with `hermes cron remove freemodels-sync`, and reinstall the system entry after
DST changes.

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
hermes freemodels install-cron --apply  # Hermes scheduling (00:01 UTC; --time HH:MM)
hermes freemodels install-cron --system --apply  # opt into system crontab
```

Inside a Hermes session, `/freemodels` prints the same table (`/freemodels full`
includes skipped models, `/freemodels json` the raw snapshot). It reads the last
`sync` or `list` command's cached results — never the network mid-conversation — and says when
they've gone stale. `/freemodels pin <model-id>`, `/freemodels unpin` and
`/freemodels mode <name>` work from chat too, so you don't need a shell.

On Discord, `/freemodels` appears in the native slash-command menu. Use its
`args` field for `full`, `pin <model-id>`, `unpin`, or `mode <name>`. If it is
missing after installation or an update, restart the gateway to load the plugin
and synchronize Discord commands. Synchronization runs in the background and
can take several minutes; leave the gateway running until its logs report that
commands were reconciled. Check the logs for a plugin version rejection or a
Discord sync failure if it still does not appear.

## Choosing a model yourself

### Browsing free models

Hermes's OpenRouter picker normally uses a curated catalog, so free models may
be missing from the menu (explicit model IDs remain usable). By default, `sync` maintains its own
manifest (`~/.hermes/freemodels/model-catalog.json` — free models first, then
Hermes's curated ones) and points `model_catalog.providers.openrouter.url` at it:

```
/model  →  OpenRouter (45 models)  →  13 free first, then Hermes's own 32
```

> [!IMPORTANT]
> **Restarting Hermes and refreshing models serve different purposes.**
> `/freemodels`, `list`, and `status` show reminders when a managed catalog exists.
>
> These views also warn when a selected default, fallback, or pinned model expires
> within 48 hours, or has already expired. Warnings use the saved expiry dates;
> `/freemodels` and `status` do not fetch fresh data. Dates without a time are
> treated as midnight UTC. Run `hermes freemodels sync` to refresh the selection,
> or choose another pinned model/unpin to resume rotation.

### Automatic updates, refresh, and restart

- **Automatic:** scheduled sync fetches free models and updates your configured
  default, fallbacks, and picker catalog. Run `hermes freemodels sync` to update now.
- **Refresh the picker:** if models are missing, let the gateway refresh its catalog
  (normally every 20 minutes when enabled), then run `/model --refresh`.
  This opens the picker; choose OpenRouter, then your model. On Discord,
  enter `--refresh` in `/model`'s **name** field. Restarting alone may still use old cache.
- **Restart Hermes:** after installing/updating the plugin, restart the CLI session
  or gateway to load its code and commands. Discord's menu may take a few minutes to sync.

Gateway chats using the global default follow model rotation automatically.
An existing CLI session may need a new model selection or restart; session/channel
model overrides take precedence. A picker refresh does not run sync or update the plugin.

For the labelled view use `/freemodels` in a session, or `hermes freemodels list`.
Example output (models and counts change):

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
- Opt out per run with `sync --no-catalog` (keeps an existing managed catalog), or remove it with `hermes freemodels remove-catalog` (deletes the manifest and config pointer, returning to Hermes's upstream catalog after its caches refresh).

### Pinning a model

Changing the configured default by hand isn't enough to freeze it — the next
sync can replace `model.default`. Pin it to stop that:

```bash
hermes freemodels pin cohere/north-mini-code:free   # switch to it and hold it
hermes freemodels pin                               # hold the configured default
hermes freemodels unpin                             # back to automatic
```

- Pinning **updates the configured default as well as freezing it** — it does not wait for the next sync. An active session's model override may still take precedence.
- Only `model.default` is frozen. The fallback chain keeps rotating when qualifying models are available, and the pinned model is dropped from its own fallbacks.
- If a pinned `:free` model expires or stops being offered, a successful sync with qualifying replacements drops the pin, resumes rotation, and records why — shown in `status` and `/freemodels`. Non-free pins do not auto-clear; their availability is not checked by this plugin.
- IDs are checked against the generated `/model` catalog when it exists (`--force` overrides). Without a catalog, pinning proceeds with a verification warning.

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
- **`strict` can leave you with fewer fallbacks** — if only one private model exists you get a default and no managed free fallbacks (unless another model is pinned). If none exists, sync refuses rotation; the picker catalog can still update.
- `unknown` is never selectable in any mode: the privacy policy could not be established. Listable unknown models stay pickable by hand in `/model`.
- A mode takes effect at the next `sync`, not immediately. A [pin](#pinning-a-model) overrides the mode for `model.default`; the fallback chain still follows it.

## What it changes in your config

Rotation updates `model.default` and the managed OpenRouter `:free` fallbacks.
Pinning can also set `model.default` to a non-free model. Catalog management
updates `model_catalog.providers.openrouter.url` and removes old plugin-owned
picker entries. Other fallbacks are preserved:

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
OpenRouter `:free` fallbacks; afterward, entries are matched by provider and model
ID, so an identical copy of an owned entry is also treated as managed.

## How it works

A free model is eligible only if it is:

| Criterion | Rule | Source |
|-----------|------|--------|
| **Right privacy tier** | Which tiers qualify is set by the [selection mode](#selection-modes). By default: private first, falling back to **Logs** (retains but doesn't train) only if fewer than 3 private models exist, and never **Trains**. | Not in the public API — the plugin reads each candidate's model page for its free endpoint's data policy. If several providers serve a model free, the least private one decides its tier. Cached 24h. |
| **Tool-capable** | The `:free` model reports `tools` in its supported parameters — Hermes needs tool calling. | Models API (`/api/v1/models`) |
| **Not expiring** | Expiry dates on or before tomorrow UTC are skipped; this is a calendar-day buffer, not an exact 24-hour window. Missing expiry dates are allowed; malformed dates are skipped. | Models API |
| **Available** | At least one free endpoint has day-long uptime of 20% or higher. If no uptime is reported, endpoint status is used instead. Re-checked each selection run, so a recovered model can return. | Endpoints API (`/api/v1/models/<id>/endpoints`, `uptime_last_1d`), checked before the privacy scrape. |

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

The generated scheduler script lives at `$HERMES_HOME/scripts/freemodels_sync.py`.
Hermes owns its job database and execution history under `$HERMES_HOME/cron/`.

## Uninstall

```bash
./uninstall.sh --standard # remove the plugin, keep state and schedules
./uninstall.sh --purge   # also remove state, owned schedules and generated script
```

Without a flag, the script prompts in a terminal and defaults to standard in
noninteractive use. Standard uninstall keeps schedules, which cannot sync until
the plugin is reinstalled. The scripts require Bash, including on Windows.

Both attempt to remove the managed catalog first; cleanup can fail if Hermes
is unavailable or the plugin is disabled, in which case manual cleanup may be
needed. Picker changes remain subject to Hermes's caches. Your `model.default`
and fallback chain are left as-is.

Purge requires Python 3 on PATH and Hermes when removing an installed job. It
also works from this checkout after a previous standard uninstall or plugin disable.

## Permissions

**This plugin requests no privileged capabilities.** It registers a CLI command
and a slash command through Hermes's public registration APIs.

It makes unauthenticated HTTPS requests to `openrouter.ai` for models, endpoint
availability, collection rankings, and privacy policies. No prompts or conversation
data are sent. Hermes reports authentication status for a picker warning; the
plugin does not read credential files, store keys, or refresh tokens.

It reads and updates the current profile's `config.yaml`, keeps its ownership
ledger and logs under `freemodels/`, and invalidates Hermes's provider-model cache.
Scheduling writes `scripts/freemodels_sync.py`, reads `cron/jobs.json`, and invokes
the Hermes cron CLI. The existing gateway runs the job; the plugin does not start
a background service. Migration and `--system` invoke `crontab`, changing only
this home's sync entries. Install/uninstall scripts use Bash and change plugin
files and enablement; standard uninstall removes the managed picker entries.

There is no telemetry or self-updater. Git installs managed by Hermes can use
`hermes plugins update hermes-openrouter-free-rotator`; manually copied installs
need to be refreshed through the setup script. Catalog installs use a reviewed
pinned revision and may follow different update rules.

```bash
hermes plugins capabilities hermes-openrouter-free-rotator
```

## Support the Project
If you find this plugin useful, please leave a star on GitHub or consider supporting its development!

[![ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/T5T725W4FX)

## License

[![GitHub License](https://img.shields.io/github/license/goslowpoke168/hermes-openrouter-free-rotator?style=for-the-badge&logo=data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIyNCIgaGVpZ2h0PSIyNCIgdmlld0JveD0iMCAwIDI0IDI0IiBmaWxsPSJub25lIiBzdHJva2U9IiNmZmZmZmYiIHN0cm9rZS13aWR0aD0iMiIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5kIiBjbGFzcz0ibHVjaWRlIGx1Y2lkZS1zY2FsZSI+PHBhdGggZD0ibTE2IDE2IDMtOCAzIDhjLS44Ny42NS0xLjkyIDEtMyAxcy0yLjEzLS4zNS0zLTFaIi8+PHBhdGggZD0ibTIgMTYgMy04IDMgOGMtLjg3LjY1LTEuOTIgMS0zIDFzLTIuMTMtLjM1LTMtMVoiLz48cGF0aCBkPSJNNyAyMWgxMCIvPjxwYXRoIGQ9Ik0xMiAzdjE4Ii8+PHBhdGggZD0iTTMgN2gyYzIgMCA1LTEgNy0yIDIgMSA1IDIgNyAyaDIiLz48L3N2Zz4=)](https://github.com/GoSlowPoke168/hermes-openrouter-free-rotator/blob/main/LICENSE)
