# hermes-openrouter-free-rotator

A [Hermes Agent](https://hermes-agent.nousresearch.com) plugin that keeps your
default and fallback models pointed at the best **free**, **privacy-respecting**
OpenRouter models — automatically.

Free (`:free`) OpenRouter models expire and disappear without notice. Once a
day this plugin ranks the [free-models collection](https://openrouter.ai/collections/free-models),
drops anything unsuitable, and rewrites your Hermes config:

- best pick → `model.default`
- next two → the leading `fallback_providers`
- any fallback entries you added yourself are preserved after them — the plugin
  only ever touches the `:free` entries it owns (tracked in its own state file).

Writes happen only when the selection actually changes, so a model expiring
tomorrow is replaced *before* it goes dark.

**Prerequisite:** `model.provider` in `config.yaml` must already be
`openrouter`. If it's anything else, `sync` aborts without touching your
config — the plugin only ever manages a setup you've already pointed at
OpenRouter, it won't switch providers for you. `fallback_providers` doesn't
need to exist beforehand; if it's missing, the plugin creates it.

### Example

Before (freshly on OpenRouter, `fallback_providers` not yet set, one
hand-added non-OpenRouter fallback already present):

```yaml
model:
  provider: openrouter
  default: some-model-that-just-expired:free
  base_url: https://openrouter.ai/api/v1
  api_mode: chat_completions
fallback_providers:
  - provider: google
    model: gemini-3.1-flash-lite
```

After `hermes freemodels sync`:

```yaml
model:
  provider: openrouter
  default: tencent/hy3:free
  base_url: https://openrouter.ai/api/v1
  api_mode: chat_completions
fallback_providers:
  - provider: openrouter
    model: cohere/north-mini-code:free
  - provider: openrouter
    model: openai/gpt-oss-20b:free
  - provider: google
    model: gemini-3.1-flash-lite
```

`model.default` and the leading `openrouter` entries in `fallback_providers`
are plugin-managed and get rewritten on every sync; the `google` entry was
added by hand, isn't tracked as managed, and is preserved in place after them.

## Selection criteria

A free model is eligible only if it is:

| Criterion | Rule |
|-----------|------|
| **Private** | Its free endpoint's provider does not train on or retain prompts. Falls back to the **Logs** tier (retains but doesn't train) only if fewer than 3 private models exist. Providers that **train on prompts are never selected.** |
| **Tool-capable** | The free endpoint supports `tools` — Hermes is an agent, so a model that can't call tools can't be the default. |
| **Not expiring** | Models expiring within 1 day are skipped. |
| **Available** | The free endpoint's day-long uptime is above ~20%. Lenient by design (a brief outage won't drop a model) and re-checked daily, so a recovered model returns automatically. |

Eligible models are ranked by the collection's real-usage order, with privacy
tier taking priority (every private pick outranks any logs pick). If the
collection page can't be read, ranking falls back to newest-first.

## Install

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

The installer enables the plugin and creates its state directory. Restart your
Hermes session (or gateway) to pick up the new `freemodels` command.

## Usage

```bash
hermes freemodels list                  # ranked candidates: tier, uptime, expiry, skip reasons
hermes freemodels sync                  # apply the best selection (idempotent)
hermes freemodels sync --dry-run        # preview the change without writing
hermes freemodels status                # current selection, last sync, last error
hermes freemodels install-cron --apply  # run daily (default 00:01 UTC; --time HH:MM)
```

`sync` exits `0` on success or no-change and `1` on failure (logged to
`~/.hermes/freemodels/cron.log`). On any failure it leaves your config
untouched; Hermes's own runtime fallback chain is the safety net.

**No systemd service here** — this plugin runs as a daily cron job (see
`install-cron` above), not a background service. There's nothing to
`systemctl restart`; to re-run manually or pick up code changes just run
`hermes freemodels sync`. Check `crontab -l` to see the installed line and
`~/.hermes/freemodels/cron.log` for its output.

## How it works

- **Ranking & expiry** come from the public models API (`/api/v1/models`) and
  the collection page's usage order.
- **Privacy** isn't in the public API, so the plugin reads each candidate's
  model page and extracts its free endpoint's data policy. If several providers
  serve a model free, the least private one decides its tier. Cached 24h.
- **Uptime** comes from the `:free` endpoints API
  (`/api/v1/models/<id>/endpoints`, field `uptime_last_1d`), checked fresh
  every run and evaluated *before* the privacy scrape so down models are
  dropped cheaply.

A typical daily run is one API call plus a handful of page fetches.

## State & files

Everything lives under `~/.hermes/freemodels/`:

| File | Purpose |
|------|---------|
| `state.json` | Ownership ledger (which entries the plugin manages) + privacy cache |
| `freemodels.log` | Rotating activity log |
| `cron.log` | Output of scheduled runs |
| `config.yaml.pre-sync.bak` | Backup taken before every config write |

Config is written through Hermes's own atomic writer, preserving file mode.

## Testing

```bash
python -m pytest tests/                                              # unit tests
HERMES_FREEMODELS_TODAY=2099-01-01 hermes freemodels sync --dry-run  # simulate an expiry day
```

## Uninstall

```bash
./uninstall.sh           # remove the plugin, keep state
./uninstall.sh --purge   # also remove the state dir and crontab entry
```

Your `config.yaml` is left as-is on uninstall.

## License

[![GitHub License](https://img.shields.io/github/license/goslowpoke168/hermes-openrouter-free-rotator?style=for-the-badge&logo=data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIyNCIgaGVpZ2h0PSIyNCIgdmlld0JveD0iMCAwIDI0IDI0IiBmaWxsPSJub25lIiBzdHJva2U9IiNmZmZmZmYiIHN0cm9rZS13aWR0aD0iMiIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5kIiBjbGFzcz0ibHVjaWRlIGx1Y2lkZS1zY2FsZSI+PHBhdGggZD0ibTE2IDE2IDMtOCAzIDhjLS44Ny42NS0xLjkyIDEtMyAxcy0yLjEzLS4zNS0zLTFaIi8+PHBhdGggZD0ibTIgMTYgMy04IDMgOGMtLjg3LjY1LTEuOTIgMS0zIDFzLTIuMTMtLjM1LTMtMVoiLz48cGF0aCBkPSJNNyAyMWgxMCIvPjxwYXRoIGQ9Ik0xMiAzdjE4Ii8+PHBhdGggZD0iTTMgN2gyYzIgMCA1LTEgNy0yIDIgMSA1IDIgNyAyaDIiLz48L3N2Zz4=)](https://github.com/GoSlowPoke168/hermes-openrouter-free-rotator/blob/main/LICENSE)
