#!/usr/bin/env bash
# Remove hermes-openrouter-free-rotator.
#
#   ./uninstall.sh           remove the plugin (symlink or copied dir)
#   ./uninstall.sh --purge   also remove ~/.hermes/freemodels state dir and
#                            the crontab entry
set -euo pipefail

PLUGIN_NAME="hermes-openrouter-free-rotator"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
TARGET="$HERMES_HOME/plugins/$PLUGIN_NAME"
STATE_DIR="$HERMES_HOME/freemodels"

PURGE=0
[ "${1:-}" = "--purge" ] && PURGE=1

# Always clean the /model picker list — it's a display list maintained by a
# plugin that's going away, so leaving stale free models in the picker is a
# bug, not a preserved preference. Must run BEFORE --purge deletes the state
# dir, which holds the ledger of which entries are ours.
if command -v hermes >/dev/null 2>&1; then
  hermes freemodels remove-catalog 2>/dev/null || \
    echo "warning: could not clean providers.openrouter.models — remove it by hand" >&2
fi

if [ -L "$TARGET" ]; then
  rm -f "$TARGET"
  echo "removed symlink $TARGET"
elif [ -d "$TARGET" ]; then
  rm -rf "$TARGET"
  echo "removed $TARGET"
else
  echo "not installed ($TARGET missing)"
fi

if [ "$PURGE" -eq 1 ]; then
  rm -rf "$STATE_DIR"
  echo "removed $STATE_DIR"
  if crontab -l 2>/dev/null | grep -q "freemodels sync"; then
    crontab -l 2>/dev/null | grep -v "freemodels sync" | crontab -
    echo "removed crontab entry"
  fi
else
  echo "state kept at $STATE_DIR (use --purge to remove it and the cron entry)"
fi

echo "note: your model.default and fallback_providers are untouched; only the"
echo "      plugin's free-model list under providers.openrouter.models was removed."
