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

echo "note: config.yaml was not modified — your current model.default stays as-is."
