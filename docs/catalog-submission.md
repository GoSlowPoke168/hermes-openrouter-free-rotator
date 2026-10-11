# Catalog submission preparation

Follow the [Hermes admission rules](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins/catalog-submission).
This checkout is prepared for review; a listing still needs a published commit and maintainer approval.

## Entry draft

After publishing the reviewed changes, add `plugin-catalog/hermes-openrouter-free-rotator.yaml`
to `NousResearch/hermes-agent`. Replace the placeholder with that commit's full 40-character SHA;
do not pin the current base commit, which does not contain these changes.

```yaml
name: hermes-openrouter-free-rotator
repo: https://github.com/GoSlowPoke168/hermes-openrouter-free-rotator
sha: "<full 40-character reviewed commit SHA>"
description: Rotate OpenRouter free models by privacy and availability, with daily UTC scheduling.
maintainer: GoSlowPoke168
tier: community
category: models
requires_hermes: ">=0.21.0"
version: "1.1.0"
readme: true
platforms: [linux]
known_issues:
  - "Windows support is experimental: native runner smoke tests pass, but Windows 10 and manual setup remain untested."
  - "Automatic sync requires a running Hermes gateway."
capabilities:
  provides_tools: []
  provides_hooks: []
  provides_middleware: []
  requires_env: []
```

The draft advertises Linux only while Windows support remains experimental. The plugin fetches public data without
credentials; an OpenRouter account is needed for Hermes to use those models, not for data retrieval.
The optional key check goes through Hermes's profile-scoped secret API and auth-status API.

## PR disclosure draft

This standalone plugin registers `freemodels` CLI and `/freemodels` session commands using public
Hermes APIs. It makes unauthenticated requests to OpenRouter's public models/endpoints APIs and
collection/model pages; no prompts, telemetry or credential files are read or sent. It reads and
updates the current profile's model routing and picker configuration, maintains an ownership ledger,
logs and backups, and clears the host's provider-model cache. Authentication status is queried through
Hermes only, for an advisory picker warning; it stores no credentials and refreshes no tokens.

Opt-in scheduling installs a generated Python script and a local, no-agent Hermes cron job; it reads
the profile's cron metadata and invokes the Hermes CLI. The user runs the existing gateway; the
plugin starts no service. System-crontab installation and migration invoke `crontab`, affecting only
this home's sync entries. Bash install/uninstall scripts modify plugin files and enablement; uninstall
removes owned picker entries and purge additionally removes owned state and schedules. There is no
self-updater, runtime core override, or privileged capability request.

## Required final checks

From a checkout of the exact published commit, on a Hermes version with catalog validation:

```bash
hermes plugins validate /path/to/hermes-openrouter-free-rotator --install-deps
```

Review all warnings and run the catalog entry schema check. Admission CI must pass against the
pinned tree, and a Hermes maintainer must review it before listing. Local tests are ignored by this
repository's existing policy, so they will not be present in the published checkout.

## Windows verification

The [native Windows smoke run](https://github.com/GoSlowPoke168/hermes-openrouter-free-rotator/actions/runs/38111758689)
passed on Python 3.14.7 x64 with Hermes v0.21.6. It checked real cross-process locking,
GitHub plugin installation, live OpenRouter list/sync, status, pin/unpin, idempotent schedule
installation, a due sync launched by the scheduled script, a natural gateway execution,
a quiet UTC check, and CLI uninstall.
The probe lives in `.github/scripts/windows_smoke.py`, outside the ignored local test tree.
Windows 10, Bash install/uninstall scripts, inference, and gateway auto-start were not checked.
