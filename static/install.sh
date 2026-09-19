#!/bin/sh
# Install the auto model router locally.
#
#   curl -fsSL https://whichmodel.app.mintapis.com/install.sh | sh
#
# What it does, and nothing else:
#   * clones https://github.com/fstandhartinger/auto-model-router (MIT) into
#     ~/.auto-router/src
#   * makes a virtualenv there and installs the router's dependencies into it
#   * writes a starter config, ~/.auto-router/config.yaml, if you have none,
#     and a starter job-launcher config, ~/.auto-router/launcher.yaml
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
#   auto-router check               # in a second terminal: proves it routes
#   auto-router claude              # Claude Code through the router
#   auto-router run "a task"        # job-level: pick the tool (Codex, Claude,
#                                   # opencode) for a whole job, then start it
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
LAUNCHER_CONFIG="$ROOT/launcher.yaml"

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
  anthropic:
    base_url: https://api.anthropic.com/v1
    api: anthropic
    cache: anthropic

# Your Claude plan, for switch mode (auto-router switch). The plan opens for
# automatic switching only once usage_command reports how full it is - a
# command printing {"claude": {"week_percent": 12, "session_percent": 30}}.
# Until then, start a prompt with ~plan to send it to the plan yourself.
subscriptions:
  claude:
    # usage_command: [/path/to/your/usage-reader]
    weekly_reserve: 0.65
    hard_stop: 0.80

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

  # The two frontier models. The router sends a turn here only when the risk
  # of a cheaper model getting it wrong costs more than the difference - on
  # your key, at \$10 / \$50 per million tokens. Delete them to cap your spend.
  - name: claude-fable-5.1
    provider: openrouter
    upstream_id: anthropic/claude-fable-5.1
    bench_id: claude-fable-5.1::medium
    bench_offer: {platform: OpenRouter, provider: Anthropic}
    cache_family: anthropic
    cache: {ttl_seconds: 300, hit_rate: 0.95}
    vision: true

  - name: gpt-6-astra
    provider: openrouter
    upstream_id: openai/gpt-6-astra
    bench_id: gpt-6-astra::medium
    bench_offer: {platform: OpenRouter, provider: OpenAI}
    cache: {ttl_seconds: 300, hit_rate: 0.95}
    vision: true

  # Your Claude plan. Only reachable in switch mode, where Claude Code talks to
  # Anthropic directly with your own login - the router never sees it.
  - name: claude-plan
    provider: anthropic
    upstream_id: claude-opus-5
    bench_id: claude-opus-5::medium
    bench_offer: {platform: OpenRouter, provider: Anthropic}
    subscription: claude
    list_price_model: claude-opus-5
    cache_family: anthropic
    vision: true
YAML
fi

# ------------------------------------------------------ the job-launcher config
# Job-level routing: the router picks which *program* does a whole job and
# starts it unmodified - Codex on your ChatGPT plan, Claude Code on your Claude
# plan, or a cheap model through opencode. Nothing intercepts their traffic,
# which is what makes this the way to use a flat-rate plan (see TERMS.md).
if [ -f "$LAUNCHER_CONFIG" ]; then
  say "keeping your job-launcher config at $LAUNCHER_CONFIG"
else
  say "writing a starter job-launcher config to $LAUNCHER_CONFIG"
  cat > "$LAUNCHER_CONFIG" <<YAML
# Job-level routing: \`auto-router run "<task>"\` decides which program should
# do a whole job, then starts that program as its vendor ships it.
#
# A plan is only used while it has headroom. Codex writes its own rate-limit
# readings to ~/.codex/sessions, so that plan paces itself out of the box.
# Claude Code keeps no such file: give the router a command that prints your
# usage as JSON (see usage_command below) or the Claude plan stays closed -
# the router never guesses and never reads a login token.

providers:
  openrouter:
    base_url: https://openrouter.ai/api/v1
    api_key_env: OPENROUTER_API_KEY
    cache: openai
  anthropic:
    base_url: https://api.anthropic.com/v1
    api: anthropic
    cache: anthropic
  openai:
    base_url: https://api.openai.com/v1
    cache: openai

subscriptions:
  codex:
    codex_rollouts: ~/.codex/sessions
    weekly_reserve: 0.65
    hard_stop: 0.80
    clear_env: [OPENAI_API_KEY]
  claude:
    # usage_command: [my-claude-usage, --json]   # prints {"claude": {"week_percent": 41, ...}}
    weekly_reserve: 0.65
    hard_stop: 0.80
    clear_env: [ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN]

policy:
  name: F_expected
  jev_difficulty_calibration: [0.27, 0.51]
  success:
    table: $SRC/examples/success.measured.json

models:
  # Cheap: an easy job lands here, on your OpenRouter key, through opencode.
  - name: glm-5.3-flash
    provider: openrouter
    upstream_id: z-ai/glm-5.3-flash
    bench_id: glm-5.3-flash::default
    bench_offer: {platform: OpenRouter}
    runner:
      cmd: [opencode, run, -m, openrouter/z-ai/glm-5.3-flash, "{task}"]
      timeout_s: 1800

  # Your ChatGPT plan, through the Codex CLI. Launch-only: no HTTP request
  # from another client can ever be served from it.
  - name: codex-plan
    provider: openai
    upstream_id: gpt-5.6-sol
    bench_id: gpt-5.6-sol::medium
    bench_offer: {platform: OpenRouter, provider: OpenAI}
    subscription: codex
    launch_only: true
    list_price_model: gpt-5.6-sol-metered
    runner:
      cmd: [codex, exec, "{task}"]
      clear_env: [OPENAI_API_KEY]
      timeout_s: 3600

  # Your Claude plan, through Claude Code. Closed until usage_command is set.
  - name: claude-plan
    provider: anthropic
    upstream_id: claude-opus-5
    bench_id: claude-opus-5::medium
    bench_offer: {platform: OpenRouter, provider: Anthropic}
    subscription: claude
    launch_only: true
    list_price_model: gpt-5.6-sol-metered
    cache_family: anthropic
    runner:
      cmd: [claude, -p, --model, opus, --permission-mode, acceptEdits]
      stdin: true
      clear_env: [ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN]
      timeout_s: 3600

  # Metered and strong, for when both plans are paced out - and the price
  # reference a plan is shadow-priced against as it fills up.
  - name: gpt-5.6-sol-metered
    provider: openrouter
    upstream_id: openai/gpt-5.6-sol
    bench_id: gpt-5.6-sol::medium
    bench_offer: {platform: OpenRouter, provider: OpenAI}
    runner:
      cmd: [opencode, run, -m, openrouter/openai/gpt-5.6-sol, "{task}"]
      timeout_s: 3600
YAML
fi

# ----------------------------------------------------------------- the launcher
cat > "$BIN/auto-router" <<LAUNCHER
#!/bin/sh
# The auto model router. Written by the installer; edit freely.
#
#   auto-router                 start the router on http://127.0.0.1:$PORT/v1
#   auto-router check           prove a running router answers and routes
#   auto-router claude [args]   Claude Code through the router (API-key mode)
#   auto-router switch [args]   Claude Code, cheap by default, on your Claude plan when needed
#   auto-router run "<task>"    job-level: choose Codex / Claude / opencode, start it
#   auto-router delegate        MCP server: lets a plan session hand sub-tasks to cheap models
#   auto-router update          re-run the installer
set -eu
export AUTO_ROUTER_CACHE_DIR="\${AUTO_ROUTER_CACHE_DIR:-$ROOT/cache}"
URL="http://\${AUTO_ROUTER_HOST:-127.0.0.1}:\${AUTO_ROUTER_PORT:-$PORT}"
case "\${1:-serve}" in
  serve)
    [ "\$#" -gt 0 ] && shift
    export AUTO_ROUTER_CONFIG="\${AUTO_ROUTER_CONFIG:-$CONFIG}"
    cd "$SRC"
    exec "$VENV/bin/uvicorn" auto_router.server:app \\
      --host "\${AUTO_ROUTER_HOST:-127.0.0.1}" --port "\${AUTO_ROUTER_PORT:-$PORT}" "\$@" ;;
  check)
    exec "$VENV/bin/python" - "\$URL" <<'PY'
import json, sys, urllib.request
url = sys.argv[1]
try:
    health = json.load(urllib.request.urlopen(url + "/health", timeout=5))
except Exception as exc:
    sys.exit(f"  no router answering at {url} ({exc}). Start it with: auto-router")
print(f"  router up at {url}: {health}")
body = json.dumps({"model": "auto", "max_tokens": 20,
                   "messages": [{"role": "user", "content": "Reply with the single word OK."}]}).encode()
req = urllib.request.Request(url + "/v1/chat/completions", body, {"Content-Type": "application/json"})
try:
    resp = urllib.request.urlopen(req, timeout=120)
except urllib.error.HTTPError as exc:
    sys.exit(f"  the router answered {exc.code}: {exc.read()[:300]!r} - is OPENROUTER_API_KEY set where the router runs?")
data = json.load(resp)
print("  chosen model:", resp.headers.get("X-Router-Model"))
print("  why:         ", resp.headers.get("X-Router-Reason"))
print("  answer:      ", (data.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()[:80])
print("  OK - point your tools at", url + "/v1")
PY
    ;;
  claude)
    shift
    # A gateway credential makes Claude Code send every turn through the router
    # and bill the router's providers, not your Claude plan: Anthropic's docs -
    # "the credential replaces the subscription login for that session, and the
    # subscription's usage limits don't apply". The value never leaves this machine.
    ANTHROPIC_BASE_URL="\$URL" ANTHROPIC_API_KEY="\${AUTO_ROUTER_CLAUDE_KEY:-local-router}" exec claude "\$@" ;;
  switch)
    shift
    # Cheap mode: Claude Code through the router with its own credential (your
    # plan is not used). Plan mode: Claude Code with your own login, straight
    # to Anthropic, no router in between. One conversation moves between them.
    export AUTO_ROUTER_CONFIG="\${AUTO_ROUTER_CONFIG:-$CONFIG}" AUTO_ROUTER_URL="\$URL"
    export PYTHONPATH="$SRC\${PYTHONPATH:+:\$PYTHONPATH}"
    exec "$VENV/bin/python" -m auto_router.switch "\$@" ;;
  delegate)
    shift
    export AUTO_ROUTER_CONFIG="\${AUTO_ROUTER_LAUNCHER_CONFIG:-$LAUNCHER_CONFIG}"
    export PYTHONPATH="$SRC\${PYTHONPATH:+:\$PYTHONPATH}"
    exec "$VENV/bin/python" -m auto_router.delegate ;;
  run)
    shift
    export AUTO_ROUTER_CONFIG="\${AUTO_ROUTER_LAUNCHER_CONFIG:-$LAUNCHER_CONFIG}"
    exec "$VENV/bin/python" "$SRC/scripts/route-run" "\$@" ;;
  update)
    exec sh -c "curl -fsSL https://whichmodel.app.mintapis.com/install.sh | sh" ;;
  -h|--help|help)
    sed -n '2,10p' "\$0" ;;
  *)
    export AUTO_ROUTER_CONFIG="\${AUTO_ROUTER_CONFIG:-$CONFIG}"
    cd "$SRC"
    exec "$VENV/bin/uvicorn" auto_router.server:app \\
      --host "\${AUTO_ROUTER_HOST:-127.0.0.1}" --port "\${AUTO_ROUTER_PORT:-$PORT}" "\$@" ;;
esac
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
say "auto-router check                  # (second terminal) proves it routes"
say "auto-router claude                 # Claude Code through the router"
say "auto-router switch                 # Claude Code: cheap routes, your plan when needed"
say "auto-router run --dry-run \"a task\" # job-level: which tool would do it"
printf '\n'
case ":$PATH:" in
  *":$BIN:"*) ;;
  *) say "$BIN is not on your PATH - add it, or run $BIN/auto-router"; printf '\n' ;;
esac
say "Per-tool setup (Claude Code, Codex, opencode, Cursor): https://whichmodel.app.mintapis.com/run"
printf '\n'
