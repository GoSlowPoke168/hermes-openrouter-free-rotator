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

It also puts **every** free model into Hermes's `/model` picker so you can
switch to one by hand — see [Browsing free models](#browsing-free-models).

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

## Browsing free models

Hermes's `/model` picker only offers OpenRouter models from a curated catalog,
so free ones outside it are normally unreachable by hand. Each `sync` writes
its own manifest (`~/.hermes/freemodels/model-catalog.json` — free models
first, then Hermes's curated ones) and points
`model_catalog.providers.openrouter.url` at it:

```
/model  →  OpenRouter (45 models)  →  13 free first, then Hermes's own 32
```

> [!IMPORTANT]
> **Restart Hermes before picking a free model — `/model` won't show them
> otherwise.** Hermes caches the catalog for the life of the process, so any
> session or gateway already running when `sync` wrote the list keeps showing
> the old one.
>
> ```
> /restart                # inside a session (CLI, Discord, Telegram)
> hermes gateway restart  # from a shell, for the gateway
> ```
>
> Auto Model Rotation is unaffected since `model.default` is read from config.yaml per
> request, so a rotated default applies with no restart. `/freemodels` also
> needs one restart to appear, since slash commands register at plugin load.

For the labelled view use: `/freemodels` in a session, or `hermes freemodels list`:

```
Rank  Model                                     Privacy  Served by         Uptime  Status
-----------------------------------------------------------------------------------------
  #8  inclusionai/ling-3.0-tiny:free            Private  NovitaAI             99%  ← Default
  #4  cohere/north-mini-code:free               Logs     Cohere               97%  ← Fallback #1
 #10  google/gemma-4-26b-a4b-it:free            Logs     Darkbloom            97%  ← Fallback #2
 #11  openai/gpt-oss-20b:free                   Logs     Darkbloom            97%  Retains prompts
 #13  google/gemma-4-31b-it:free                Logs     Google AI Studio     99%  Retains prompts
  #1  nvidia/nemotron-3-ultra-550b-a55b:free    Trains   NVIDIA              100%  Trains on prompts
 ...
```
- **Note:** At the time of running this command, there is only 1 fully private model, which is why the fallback models are logging models. Also, only **tool-capable** models are listed.
- Models are ordered from best→worst: `private` → `logs` → `trains` → `unknown` → currently down, with popularity breaking ties inside each tier. 

Opt out per run with `sync --no-catalog`, or drop the list entirely with `hermes freemodels remove-catalog` (deletes the manifest and config pointer, restoring the picker exactly).

## Install

> [!IMPORTANT]
> Make sure you have the Openrouter provider configured in Hermes with an Openrouter API Key added.

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
Hermes session (or gateway) to pick up `freemodels` and `/freemodels`.

## Usage

```bash
hermes freemodels list                  # ranked table: tier, endpoint, uptime, status
hermes freemodels sync                  # apply the best selection (idempotent)
hermes freemodels sync --dry-run        # preview the change without writing
hermes freemodels sync --no-catalog     # rotate only; leave the /model list alone
hermes freemodels status                # current selection, last sync, last error
hermes freemodels remove-catalog        # drop the free models from /model
hermes freemodels install-cron --apply  # run daily (default 00:01 UTC; --time HH:MM)
```

Inside a Hermes session, `/freemodels` prints the same table (`/freemodels full`
includes skipped models, `/freemodels json` the raw snapshot). It reads the
last sync's cached results — never the network mid-conversation — and says when
they've gone stale.

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

## State & files

Everything lives under `~/.hermes/freemodels/`:

| File | Purpose |
|------|---------|
| `state.json` | Ownership ledgers (rotation + `/model` catalog), privacy cache, last table snapshot |
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

## TODO
- [ ] Add a feature where you can manually choose a model you want to override the free model it chose and the fallback sequence. This should be set until the user either unsets it or the model expires.
- [x] Add the feature where it automatically loads all models into Hermes' `/model` list so you can choose which one you want. — see [Browsing free models](#browsing-free-models)
- [ ] Add a feature where it overrides the privacy-first models to allow the user to choose an option that makes the freemodel rotator select models that allow logging, or allow training as well. *(Note: this is about **auto-selection** — the `/model` picker already lists every tier.)*

## License

[![GitHub License](https://img.shields.io/github/license/goslowpoke168/hermes-openrouter-free-rotator?style=for-the-badge&logo=data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIyNCIgaGVpZ2h0PSIyNCIgdmlld0JveD0iMCAwIDI0IDI0IiBmaWxsPSJub25lIiBzdHJva2U9IiNmZmZmZmYiIHN0cm9rZS13aWR0aD0iMiIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5kIiBjbGFzcz0ibHVjaWRlIGx1Y2lkZS1zY2FsZSI+PHBhdGggZD0ibTE2IDE2IDMtOCAzIDhjLS44Ny42NS0xLjkyIDEtMyAxcy0yLjEzLS4zNS0zLTFaIi8+PHBhdGggZD0ibTIgMTYgMy04IDMgOGMtLjg3LjY1LTEuOTIgMS0zIDFzLTIuMTMtLjM1LTMtMVoiLz48cGF0aCBkPSJNNyAyMWgxMCIvPjxwYXRoIGQ9Ik0xMiAzdjE4Ii8+PHBhdGggZD0iTTMgN2gyYzIgMCA1LTEgNy0yIDIgMSA1IDIgNyAyaDIiLz48L3N2Zz4=)](https://github.com/GoSlowPoke168/hermes-openrouter-free-rotator/blob/main/LICENSE)
