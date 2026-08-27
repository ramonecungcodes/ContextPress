#!/usr/bin/env bash
#
# Deploy / update ContextPress on the server.
#
# Pulls the latest commit, renders the static site into ./dist with the builder
# container (no Python on the host), then promotes it to the live webroot
# ./dist-webroot, keeping a dated, rotated backup of the version it replaces.
# nginx serves ./dist-webroot as a bind mount, so the swap is picked up live
# with no restart. A failed build never reaches the webroot: the last good
# ./dist-webroot keeps serving.
#
#   ./dist/                    the fresh build output (overwritten every run)
#   ./dist-webroot/            the live site nginx serves
#   ./dist-webroot-YYYY-MM-DD/ dated backups of prior live versions (rollback)
#
# Usage (on the server, from the repo root):
#     ./deploy.sh
#
set -euo pipefail
cd "$(dirname "$0")"

KEEP="${KEEP:-7}"   # number of dated backups to retain

# Serialize: never let two deploys (e.g. two quick merges) overlap.
exec 9>/tmp/contextpress-deploy.lock
flock -n 9 || { echo "==> another deploy is already running; exiting"; exit 0; }

echo "==> Pulling latest from git"
git pull --ff-only

echo "==> Building the builder image (cached unless requirements.txt changed)"
docker compose build build

echo "==> Rendering the site into ./dist"
docker compose run --rm build          # writes ./dist; on failure set -e stops here,
                                        # leaving the live ./dist-webroot untouched

# Promote ./dist -> ./dist-webroot, archiving the outgoing version first.
if [ -d dist-webroot ]; then
  backup="dist-webroot-$(date +%F)"
  [ -e "$backup" ] && backup="dist-webroot-$(date +%F-%H%M%S)"   # same-day redeploy
  echo "==> Archiving current webroot -> $backup"
  cp -al dist-webroot "$backup" 2>/dev/null || cp -a dist-webroot "$backup"
else
  mkdir -p dist-webroot                 # first deploy: nothing to archive yet
fi

echo "==> Promoting ./dist -> ./dist-webroot (in place; served live, no restart)"
if command -v rsync >/dev/null 2>&1; then
  rsync -a --delete dist/ dist-webroot/
else
  find dist-webroot -mindepth 1 -delete
  cp -a dist/. dist-webroot/
fi

echo "==> Pruning old backups (keeping newest $KEEP)"
# `|| true`: with pipefail on, an unmatched glob (no backups yet) must not abort.
ls -1dt dist-webroot-*/ 2>/dev/null | tail -n +"$((KEEP + 1))" | xargs -r rm -rf || true

echo "==> Ensuring nginx is running"
docker compose up -d web

echo "==> Done. nginx is serving ./dist-webroot behind Nginx Proxy Manager."
