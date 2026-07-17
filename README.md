# hermes-openrouter-free-rotator

A [Hermes Agent](https://hermes-agent.nousresearch.com) plugin that keeps your
`model.default` and `fallback_providers` pointed at the **best free OpenRouter
models that respect your privacy**.

Free (`:free`) OpenRouter models expire or disappear without notice. This
plugin checks the [free models collection](https://openrouter.ai/collections/free-models)
(ranked by real weekly usage), filters out anything unsuitable, and rotates
your Hermes config automatically:

- **#1 pick** → `model.default`
- **#2 and #3** → first two `fallback_providers`
- Fallback entries you added yourself (e.g. paid last-resort models) are
  preserved at the end of the chain — the plugin only ever touches entries it
  added, tracked in its own state file.

## Selection rules

A free model qualifies only if:

1. **Privacy** — its free endpoint's provider policy is **Private** (does not
   train on prompts, does not retain prompt data). If fewer than 3 Private
   models exist, the remaining slots may be filled from the **Logs** tier
   (retains prompts but does not train). Models whose providers **train on
   prompts are never selected.**
2. **Tool calling** — the free endpoint must support `tools` (Hermes is an
   agent; a model that can't call tools is useless as a default).
3. **Not expiring** — models expiring within 2 days are skipped, so the
   switch happens *before* the old default dies.
4. **Actually up** — a model whose best free endpoint has less than ~20%
   uptime over the last day is skipped as effectively offline. Day-long
   uptime is deliberately lenient (a brief outage won't drop a model), and
   since the check re-runs daily, a model that recovers is picked back up.

Within a tier, models are ranked by the collection page's usage order
(best/most-used first). If the page can't be scraped, ranking falls back to
newest-first from the public API.

## Install

Portable (any machine):

```bash
git clone https://github.com/jeremyhou/hermes-openrouter-free-rotator
cd hermes-openrouter-free-rotator
./install.sh          # copies the plugin into ~/.hermes/plugins/
```

Dev mode (run from your checkout, wherever it lives):

```bash
./install.sh --symlink
```

Via Hermes plugin manager:

```bash
hermes plugins install <owner>/hermes-openrouter-free-rotator
bash ~/.hermes/plugins/hermes-openrouter-free-rotator/install.sh
```

## Usage

```bash
hermes freemodels list                 # ranked candidates, privacy tiers, skip reasons
hermes freemodels sync --dry-run       # preview the config change
hermes freemodels sync                 # apply (idempotent — only writes on change)
hermes freemodels status               # current selection, last sync, last error
hermes freemodels install-cron --apply # daily check at 06:17 (customize with --time HH:MM)
```

`sync` exits 0 on success/no-change and 1 on any failure (visible in
`~/.hermes/freemodels/cron.log`). On failure it never touches your config —
if every free model disappears overnight, Hermes's own runtime fallback to
your preserved chain entries is the safety net.

## How privacy is determined

OpenRouter's public API doesn't expose provider data policies, so the plugin
reads each candidate model's page and extracts the free endpoint's
`data_policy` (`training`, `retainsPrompts`). If a model is served free by
multiple providers, the *worst* policy wins (routing may hit any of them).
Privacy results are cached for 24h in `~/.hermes/freemodels/state.json`, so
the daily run is usually a single API call plus at most a few page fetches.

Uptime comes from the public per-model endpoints API for the `:free` slug
(`/api/v1/models/<id>/endpoints`), which reports each free endpoint's
`uptime_last_1d`. It's checked fresh every run (never cached — availability
is volatile) and gates the privacy scrape, so down models are dropped cheaply
before any page fetch.

## Files

- `~/.hermes/freemodels/state.json` — ownership ledger + privacy cache
- `~/.hermes/freemodels/freemodels.log` — rotating log
- `~/.hermes/freemodels/cron.log` — cron output
- `~/.hermes/freemodels/config.yaml.pre-sync.bak` — config backup taken before every write

## Testing

```bash
HERMES_FREEMODELS_TODAY=2099-01-01 hermes freemodels sync --dry-run  # simulate expiry day
python -m pytest tests/                                              # unit tests
```

## Uninstall

```bash
./uninstall.sh           # remove plugin, keep state
./uninstall.sh --purge   # also remove state dir + crontab entry
```

Your config.yaml is left as-is on uninstall.
