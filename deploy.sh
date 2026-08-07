#!/usr/bin/env bash
#
# Deploy / update ContextPress on the server.
#
# Pulls the latest commit, regenerates the static site into ./dist using the
# builder container (no Python needed on the host), and makes sure nginx is up.
# The site is served from the bind-mounted ./dist, so content updates are picked
# up live — nginx is never restarted for a content change.
#
# Usage (on the VPS, from the repo root):
#     ./deploy.sh
#
set -euo pipefail
cd "$(dirname "$0")"

echo "==> Pulling latest from git"
git pull --ff-only

echo "==> Building the builder image (cached unless requirements.txt changed)"
docker compose build build

echo "==> Rendering the site into ./dist"
docker compose run --rm build

echo "==> Ensuring nginx is running"
docker compose up -d web

echo "==> Done. nginx is serving ./dist behind Nginx Proxy Manager."
