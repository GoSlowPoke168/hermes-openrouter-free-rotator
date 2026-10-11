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
hermes freemodels install-cron --apply   # daily at 00:01 UTC through Hermes
hermes cron status                     # check gateway/ticker health
```

The gateway must stay running for scheduled jobs to fire. Use `hermes gateway
install` for auto-start, or `hermes gateway run` in the foreground. Current Hermes
supports Windows gateway auto-start through Task Scheduler or a Startup-folder
fallback. Use `install-cron --system --apply` to opt into system crontab on Linux.
