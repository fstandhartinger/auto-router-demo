#!/bin/sh
# Install the auto model router locally.
#
#   curl -fsSL https://whichmodel.app.mintapis.com/install.sh | sh
#
# What it does, and nothing else:
#   * clones https://github.com/fstandhartinger/auto-model-router (MIT) into
#     ~/.auto-router/src
#   * makes a virtualenv there and installs the router's dependencies into it
#   * writes a starter config, ~/.auto-router/config.yaml, if you have none
#   * writes one launcher, ~/.local/bin/auto-router
#
# It installs nothing system-wide, asks for no privileges, and touches no file
# outside ~/.auto-router and ~/.local/bin. Read it before you pipe it to a
# shell - that goes for every installer, including this one.
#
# Then:
#   export OPENROUTER_API_KEY=...   # your key, on your machine
#   export TYPESAFE_API_KEY=...     # optional: the Jev classifier
#   auto-router                     # http://127.0.0.1:8787/v1
#
# Environment you can set: AUTO_ROUTER_HOME, AUTO_ROUTER_BIN, AUTO_ROUTER_PORT,
# AUTO_ROUTER_REPO, AUTO_ROUTER_REF.

set -eu

REPO=${AUTO_ROUTER_REPO:-https://github.com/fstandhartinger/auto-model-router.git}
REF=${AUTO_ROUTER_REF:-main}
ROOT=${AUTO_ROUTER_HOME:-$HOME/.auto-router}
BIN=${AUTO_ROUTER_BIN:-$HOME/.local/bin}
PORT=${AUTO_ROUTER_PORT:-8787}
SRC="$ROOT/src"
VENV="$ROOT/venv"
CONFIG="$ROOT/config.yaml"

say() { printf '  %s\n' "$*"; }
die() { printf '\n  %s\n\n' "$*" >&2; exit 1; }

printf '\n  auto-model-router\n  -----------------\n'

# ---------------------------------------------------------------- what we need
command -v git >/dev/null 2>&1 || die "git is not installed."
PYTHON=""
for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
  command -v "$candidate" >/dev/null 2>&1 || continue
  if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    PYTHON=$candidate
    break
  fi
done
[ -n "$PYTHON" ] || die "Python 3.10 or newer is not installed."
say "python: $($PYTHON --version 2>&1)"

# ------------------------------------------------------------------- the code
mkdir -p "$ROOT" "$BIN"
if [ -d "$SRC/.git" ]; then
  say "updating $SRC"
  git -C "$SRC" fetch --quiet origin "$REF"
  git -C "$SRC" checkout --quiet FETCH_HEAD
else
  say "cloning the router into $SRC"
  rm -rf "$SRC"
  git clone --quiet --depth 1 --branch "$REF" "$REPO" "$SRC" 2>/dev/null \
    || git clone --quiet "$REPO" "$SRC"
fi
say "commit: $(git -C "$SRC" rev-parse --short HEAD)"

# ------------------------------------------------------------- the virtualenv
if [ ! -x "$VENV/bin/python" ]; then
  say "creating a virtualenv in $VENV"
  "$PYTHON" -m venv "$VENV" || die "could not create a virtualenv (is python3-venv installed?)"
fi
say "installing dependencies"
"$VENV/bin/python" -m pip install --quiet --upgrade pip >/dev/null 2>&1 || true
"$VENV/bin/python" -m pip install --quiet -r "$SRC/requirements.txt" \
  || die "installing the router's dependencies failed."

# ------------------------------------------------------------------ the config
if [ -f "$CONFIG" ]; then
  say "keeping your config at $CONFIG"
else
  say "writing a starter config to $CONFIG"
  cat > "$CONFIG" <<YAML
# Starter configuration for the auto model router.
#
# Keys are referenced by the *name* of an environment variable, never written
# here. Capability per topic, list prices and cache prices come from the
# benchmark API at request time through bench_id; cache hit rates below are
# measured, and yours will differ - measure them and correct them.
#
# Add or remove models freely: nothing in the router privileges a route by
# name. A route enters with a price, a context length, a cache rule and a
# per-topic capability, and competes on those.

providers:
  openrouter:
    base_url: https://openrouter.ai/api/v1
    api_key_env: OPENROUTER_API_KEY
    cache: openai

policy:
  name: F_expected
  escalate_after_tool_errors: 3
  # Jev reports difficulty on a compressed scale; this maps it back to 0..1.
  jev_difficulty_calibration: [0.27, 0.51]
  success:
    # Measured success rates beat capability read off a leaderboard by a wide
    # margin. These are ours, from a 78-task run; re-measure for your own work
    # with experiments/calibrate.py.
    table: $SRC/examples/success.measured.json

models:
  - name: glm-5.3-flash
    provider: openrouter
    upstream_id: z-ai/glm-5.3-flash
    bench_id: glm-5.3-flash::default
    bench_offer: {platform: OpenRouter}
    cache: {ttl_seconds: 300, hit_rate: 0.95}

  - name: gpt-5.6-luna
    provider: openrouter
    upstream_id: openai/gpt-5.6-luna
    bench_id: gpt-5.6-luna::medium
    bench_offer: {platform: OpenRouter}
    cache: {ttl_seconds: 300, hit_rate: 0.98}

  - name: gpt-5.6-sol
    provider: openrouter
    upstream_id: openai/gpt-5.6-sol
    bench_id: gpt-5.6-sol::medium
    bench_offer: {platform: OpenRouter}
    cache: {ttl_seconds: 300, hit_rate: 0.99}

  - name: claude-opus-5
    provider: openrouter
    upstream_id: anthropic/claude-opus-5
    bench_id: claude-opus-5::medium
    bench_offer: {platform: OpenRouter, provider: Anthropic}
    cache_family: anthropic
    cache: {ttl_seconds: 300, hit_rate: 0.95}
    vision: true
YAML
fi

# ----------------------------------------------------------------- the launcher
cat > "$BIN/auto-router" <<LAUNCHER
#!/bin/sh
# Start the auto model router. Written by the installer; edit freely.
set -eu
export AUTO_ROUTER_CONFIG="\${AUTO_ROUTER_CONFIG:-$CONFIG}"
export AUTO_ROUTER_CACHE_DIR="\${AUTO_ROUTER_CACHE_DIR:-$ROOT/cache}"
cd "$SRC"
exec "$VENV/bin/uvicorn" auto_router.server:app \\
  --host "\${AUTO_ROUTER_HOST:-127.0.0.1}" --port "\${AUTO_ROUTER_PORT:-$PORT}" "\$@"
LAUNCHER
chmod +x "$BIN/auto-router"

printf '\n  Installed.\n\n'
say "router:   $SRC"
say "config:   $CONFIG"
say "launcher: $BIN/auto-router"
printf '\n  Next:\n\n'
say "export OPENROUTER_API_KEY=...      # your key, on your machine"
say "export TYPESAFE_API_KEY=...        # optional: the Jev classifier"
say "auto-router                        # serves http://127.0.0.1:$PORT/v1"
printf '\n'
case ":$PATH:" in
  *":$BIN:"*) ;;
  *) say "$BIN is not on your PATH - add it, or run $BIN/auto-router"; printf '\n' ;;
esac
say "Point a tool at it: https://whichmodel.app.mintapis.com/how"
printf '\n'
