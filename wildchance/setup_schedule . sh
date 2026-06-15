#!/usr/bin/env bash
# ============================================================================
# WILDCHANCE ENGINE — scheduler setup (Linux / macOS)
# ============================================================================
# Installs three cron jobs matching the engine's data cadence:
#   Tier 1  every 6h   -> live prices + recompute signals
#   Tier 2  daily 06:30 -> myfxbook retail + faireconomy calendar
#   Tier 3  Fri 21:00   -> CFTC COT (released Fri ~15:30 ET) + gold COMEX COT
#
# Usage:
#   1) edit the CONFIG block below (paths + credentials)
#   2) chmod +x setup_schedule.sh
#   3) ./setup_schedule.sh install      # add the cron jobs
#      ./setup_schedule.sh remove       # remove them
#      ./setup_schedule.sh test         # run all three tiers once, now
#      ./setup_schedule.sh show         # print current crontab
# ============================================================================

set -euo pipefail

# ---------------- CONFIG (edit these) ----------------
ENGINE_DIR="$HOME/wildchance"                     # folder holding wildchance_scraper.py + feed.json + engine.html
PYTHON_BIN="$(command -v python3 || echo python3)" # python interpreter
# Credentials — these are written into the cron environment, NOT into any source file.
TWELVEDATA_KEY="${TWELVEDATA_KEY:-}"              # export before running, or set here
MYFXBOOK_EMAIL="${MYFXBOOK_EMAIL:-}"
MYFXBOOK_PASSWORD="${MYFXBOOK_PASSWORD:-}"
# -----------------------------------------------------

SCRIPT="$ENGINE_DIR/wildchance_scraper.py"
LOG="$ENGINE_DIR/engine.log"
TAG="# wildchance-engine"   # marker so we can find/remove our lines

env_prefix() {
  # build the inline env for each cron line (only include vars that are set)
  local p=""
  [ -n "$TWELVEDATA_KEY" ]    && p+="TWELVEDATA_KEY=$TWELVEDATA_KEY "
  [ -n "$MYFXBOOK_EMAIL" ]    && p+="MYFXBOOK_EMAIL=$MYFXBOOK_EMAIL "
  [ -n "$MYFXBOOK_PASSWORD" ] && p+="MYFXBOOK_PASSWORD='$MYFXBOOK_PASSWORD' "
  echo "$p"
}

cron_lines() {
  local E; E="$(env_prefix)"
  cat <<EOF
0 */6 * * * cd $ENGINE_DIR && ${E}$PYTHON_BIN $SCRIPT --tier 6h     >> $LOG 2>&1 $TAG
30 6 * * *  cd $ENGINE_DIR && ${E}$PYTHON_BIN $SCRIPT --tier daily  >> $LOG 2>&1 $TAG
0 21 * * 5  cd $ENGINE_DIR && ${E}$PYTHON_BIN $SCRIPT --tier weekly >> $LOG 2>&1 $TAG
EOF
}

case "${1:-}" in
  install)
    [ -f "$SCRIPT" ] || { echo "ERROR: $SCRIPT not found. Fix ENGINE_DIR."; exit 1; }
    # strip any prior wildchance lines, then append fresh ones
    ( crontab -l 2>/dev/null | grep -v "$TAG" || true; cron_lines ) | crontab -
    echo "Installed. Current wildchance schedule:"
    crontab -l | grep "$TAG"
    echo "Logs -> $LOG"
    ;;
  remove)
    ( crontab -l 2>/dev/null | grep -v "$TAG" || true ) | crontab -
    echo "Removed all wildchance cron jobs."
    ;;
  test)
    [ -f "$SCRIPT" ] || { echo "ERROR: $SCRIPT not found. Fix ENGINE_DIR."; exit 1; }
    cd "$ENGINE_DIR"
    echo "== weekly =="; TWELVEDATA_KEY="$TWELVEDATA_KEY" MYFXBOOK_EMAIL="$MYFXBOOK_EMAIL" MYFXBOOK_PASSWORD="$MYFXBOOK_PASSWORD" "$PYTHON_BIN" "$SCRIPT" --tier weekly
    echo "== daily  =="; TWELVEDATA_KEY="$TWELVEDATA_KEY" MYFXBOOK_EMAIL="$MYFXBOOK_EMAIL" MYFXBOOK_PASSWORD="$MYFXBOOK_PASSWORD" "$PYTHON_BIN" "$SCRIPT" --tier daily
    echo "== 6h     =="; TWELVEDATA_KEY="$TWELVEDATA_KEY" "$PYTHON_BIN" "$SCRIPT" --tier 6h
    echo "feed.json written to $ENGINE_DIR/feed.json"
    ;;
  show)
    crontab -l 2>/dev/null | grep "$TAG" || echo "(no wildchance cron jobs installed)"
    ;;
  *)
    echo "usage: $0 {install|remove|test|show}"
    exit 1
    ;;
esac
