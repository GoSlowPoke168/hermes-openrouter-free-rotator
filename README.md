# hermes-openrouter-free-rotator

A [Hermes Agent](https://hermes-agent.nousresearch.com) plugin that finds the best
free OpenRouter models and keeps your default, fallbacks, and model picker updated.

## Capabilities

- **Automatic rotation:** ranks free models by OpenRouter collection popularity
  and your chosen privacy mode, then updates the default and leading fallbacks.
- **Model filtering:** checks tool support, endpoint availability, privacy policy,
  and expiry. Models with expiry dates on or before tomorrow UTC are skipped.
- **Free-model picker:** adds eligible free models to Hermes's `/model` catalog.
- **Manual pinning:** holds your chosen default while fallbacks keep rotating.
  A successful sync clears an expired or withdrawn free pin when replacements exist.
- **Daily scheduling:** syncs around **00:01 UTC** through Hermes cron, without
  an agent or LLM. Catches up after downtime and retries failed syncs.
- **CLI and chat controls:** inspect rankings, change privacy modes, pin models,
  and see warnings for stale data or managed models expiring within **48 hours**.

Other fallbacks are preserved after the managed ones. The first sync adopts
existing OpenRouter `:free` fallbacks; subsequent runs track ownership by provider
and model ID. Config changes are backed up and written only when needed, unless forced.

## Platform support

| Platform | Notes |
|----------|-------|
| ![Linux](https://img.shields.io/badge/Linux-supported-brightgreen?logo=linux&logoColor=white) | Supported and tested. |
| ![Windows](https://img.shields.io/badge/Windows-experimental-yellow) | Experimental; [native Windows smoke test](https://github.com/GoSlowPoke168/hermes-openrouter-free-rotator/actions/runs/38112733783) passed. Windows 10 remains untested. |

Problems? [Open a GitHub issue](https://github.com/GoSlowPoke168/hermes-openrouter-free-rotator/issues).

## Quick start

Requires **Hermes >=0.21.0**. Configure Hermes with the OpenRouter provider and
an API key first. The plugin never switches providers; rotation requires
`model.provider: openrouter` (catalog updates can still run with another provider).

```bash
hermes plugins install GoSlowPoke168/hermes-openrouter-free-rotator
hermes plugins enable hermes-openrouter-free-rotator
```

**Restart your existing Hermes session or gateway** to load the plugin. Then:

```bash
hermes freemodels list                  # inspect candidates
hermes freemodels sync --dry-run        # preview changes
hermes freemodels sync                  # apply selection and picker catalog
hermes freemodels install-cron --apply  # enable daily sync
hermes cron status                     # check scheduler health
```

**Keep the gateway running for daily updates:** use `hermes gateway install`
for auto-start or `hermes gateway run` in the foreground. Scheduling requires
Hermes's script-only cron support. Use `install-cron --time HH:MM --apply` to
change the UTC time, or omit `--apply` to preview.

For a local checkout, use `./install.sh` (copy) or `./install.sh --symlink` (development).
These scripts require Bash; the plugin-manager route also works in Windows PowerShell.

## Commands

Prefix these with `hermes freemodels`:

| Command | Purpose |
|---------|---------|
| `list` | Fetch fresh data and show ranked candidates. |
| `sync` | Update the configured default, fallbacks, and picker catalog. |
| `sync --dry-run` | Preview changes. |
| `sync --no-catalog` | Rotate models while keeping the existing picker catalog. |
| `status` | Show mode, pin, selection, schedule, and last sync/error. |
| `pin [model-id]` | Set and freeze the default; omit the ID to pin the configured default. |
| `unpin` | Resume automatic default rotation on the next sync. |
| `mode [name]` | Show or change the selection mode for the next sync. |
| `remove-catalog` | Remove the managed picker catalog; Hermes caches may take time to refresh. |
| `install-cron --apply` | Create or update the daily Hermes schedule. |

`list`, `status`, and `sync` support `--json`. Pins are checked against the generated
catalog when available; `pin --force <model-id>` bypasses that check. Non-free pins
are allowed, but their availability is not checked and they never auto-clear.

In chat, use `/freemodels`, `/freemodels full`, or `/freemodels json` to view
saved results without fetching from OpenRouter. `/freemodels pin <model-id>`,
`/freemodels unpin`, and `/freemodels mode <name>` also work. On Discord,
enter these arguments in `/freemodels`'s **args** field.

Linux system cron remains available with `install-cron --system --apply`.
Installing the Hermes job migrates this profile's legacy crontab entry. To switch
back, remove the Hermes job first (`hermes cron remove freemodels-sync`) and
reinstall the system entry after daylight-saving changes.

## Selection modes

| Mode | Allowed privacy tiers | Ranking priority |
|------|-----------------------|------------------|
| `strict` | Private | Popularity within private models |
| `balanced` (default) | Private, logs | Privacy first, then popularity |
| `relaxed` | Private, logs | Popularity first |
| `loose` | Private, logs, trains | Popularity first |

**Logs** means prompts may be retained; **trains** means they may be used for
training. Unknown policies are never auto-selected. Privacy is assessed from the
model page using the least private free provider's policy and cached for 24 hours.
If popularity data is unavailable, ranking falls back to newest-first. Fewer
qualifying models means fewer managed fallbacks; none means rotation is refused.
A pin overrides the mode for the default, while fallbacks still follow it.

## When to refresh or restart

- **Daily updates are automatic** once scheduling is enabled and the gateway is
  running. To update now, run `hermes freemodels sync`.
- **Models missing from `/model`?** Let the gateway refresh its catalog cache
  (normally every 20 minutes when enabled), then run `/model --refresh`.
  It opens the picker: select OpenRouter and choose a model. On Discord, enter
  `--refresh` in `/model`'s **name** field. Restarting alone may still use old cache.
- **Installed or updated the plugin?** Restart the Hermes session/gateway to
  load its commands. Give Discord a few minutes to update its menu.

Gateway chats using the global default follow model rotation automatically.
An existing CLI session may need a new model selection or restart; session/channel
overrides still take precedence. Expiry warnings use saved dates.

To update the plugin itself, use
`hermes plugins update hermes-openrouter-free-rotator` for a Hermes-managed Git install.

## Permissions and storage

**No privileged Hermes capabilities are requested.** The plugin registers CLI
and slash commands, fetches public OpenRouter data over HTTPS, and uses
host-reported authentication status for a picker warning. It sends no prompts,
reads no credential files, stores no API keys, and has no telemetry.

It updates the current profile's `config.yaml` and picker URL, writes state,
logs, the generated catalog, and config backups under `$HERMES_HOME/freemodels/`
(default `~/.hermes/freemodels/`), and clears the provider-model cache. Scheduling
writes `$HERMES_HOME/scripts/freemodels_sync.py`, reads Hermes's job database,
and invokes the cron CLI; migration/system scheduling also invokes `crontab`.
Bash setup scripts change plugin files and enablement.

## Uninstall

From the checkout or installed plugin directory (requires Bash):

```bash
./uninstall.sh --standard  # remove plugin; keep state and schedules
./uninstall.sh --purge     # also remove state, schedules, and generated script
```

Both attempt to remove the managed picker catalog; default and fallback settings
remain. Purge needs Python 3 and Hermes for an installed job. Standard uninstall
keeps schedules, which cannot sync until the plugin is reinstalled.

## Support the Project
If you find this plugin useful, please leave a star on GitHub or consider supporting its development!

[![ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/T5T725W4FX)

## License

[![GitHub License](https://img.shields.io/github/license/goslowpoke168/hermes-openrouter-free-rotator?style=for-the-badge&logo=data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIyNCIgaGVpZ2h0PSIyNCIgdmlld0JveD0iMCAwIDI0IDI0IiBmaWxsPSJub25lIiBzdHJva2U9IiNmZmZmZmYiIHN0cm9rZS13aWR0aD0iMiIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5kIiBjbGFzcz0ibHVjaWRlIGx1Y2lkZS1zY2FsZSI+PHBhdGggZD0ibTE2IDE2IDMtOCAzIDhjLS44Ny42NS0xLjkyIDEtMyAxcy0yLjEzLS4zNS0zLTFaIi8+PHBhdGggZD0ibTIgMTYgMy04IDMgOGMtLjg3LjY1LTEuOTIgMS0zIDFzLTIuMTMtLjM1LTMtMVoiLz48cGF0aCBkPSJNNyAyMWgxMCIvPjxwYXRoIGQ9Ik0xMiAzdjE4Ii8+PHBhdGggZD0iTTMgN2gyYzIgMCA1LTEgNy0yIDIgMSA1IDIgNyAyaDIiLz48L3N2Zz4=)](https://github.com/GoSlowPoke168/hermes-openrouter-free-rotator/blob/main/LICENSE)
