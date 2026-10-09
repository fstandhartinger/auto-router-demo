#!/usr/bin/env bash
# Put the pinned router package next to the demo app for local development.
# The Docker image does the same thing; auto_router/ is deliberately gitignored.
set -euo pipefail
REF="${ROUTER_REF:-ba9e61a76bdb9a96b4fd92b44a1dd4f28e6fe820}"
REPO="${ROUTER_REPO:-https://github.com/fstandhartinger/auto-model-router.git}"
root="$(cd "$(dirname "$0")/.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
git clone --filter=blob:none --no-checkout "$REPO" "$tmp/router" >/dev/null 2>&1
git -C "$tmp/router" checkout --quiet "$REF"
rm -rf "$root/auto_router"
cp -r "$tmp/router/auto_router" "$root/auto_router"
echo "$REF" > "$root/ROUTER_REF"
echo "vendored auto_router at $REF"
