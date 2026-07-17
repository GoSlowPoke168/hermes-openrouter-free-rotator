# hermes-openrouter-free-rotator — post-install steps

Finish setup by running the install script from the plugin directory:

```bash
bash ~/.hermes/plugins/hermes-openrouter-free-rotator/install.sh
```

(It detects that the plugin is already in place and only creates the state
directory.) Then:

```bash
hermes freemodels list              # inspect ranked free models + privacy tiers
hermes freemodels sync --dry-run    # preview what would change
hermes freemodels sync              # switch default/fallbacks to the best free models
hermes freemodels install-cron --apply   # keep it fresh with a daily cron job
```
