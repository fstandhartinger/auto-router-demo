#!/usr/bin/env bash
# Publication gate for the demo repo. Writes PUBLISH-GATE.md next to the repo; exit 1 on failure.
set -u
cd "$(dirname "$0")/.."
OUT=../PUBLISH-GATE.md
EXCL="--exclude-dir=.git --exclude-dir=__pycache__ --exclude-dir=.pytest_cache --exclude-dir=.venv --exclude-dir=auto_router --exclude-dir=node_modules --exclude-dir=.bench-cache"
fail=0
{
echo "# Publish gate — $(date -u +%Y-%m-%dT%H:%MZ)"
echo
echo '## 1. No provider or organisation names that must stay private'
echo '```'
hits=$(grep -rniE "chutes|tensorx|aimlapi|mintapis|llm\.chutes|api\.tensorx" . $EXCL | grep -v '^\./README.md:.*app\.mintapis\.com' | grep -v 'bonsai-swarm.app.mintapis.com' | grep -v 'whichmodel.app.mintapis.com')
echo "$hits"
echo '```'
[ -n "$hits" ] && { echo "**FAIL**"; fail=1; } || echo "PASS"
echo
echo '## 2. No secrets'
echo '```'
grep -rnoE "(sk-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{20,}|cpk_[A-Za-z0-9.]{20,}|AKIA[0-9A-Z]{16}|Bearer [A-Za-z0-9._-]{30,}|eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,})" . $EXCL
echo '```'
if command -v ~/.local/bin/gitleaks >/dev/null 2>&1; then
  echo "gitleaks $(~/.local/bin/gitleaks version):"
  echo '```'
  ~/.local/bin/gitleaks dir . --no-banner --redact 2>&1 | tail -4
  echo '```'
  ~/.local/bin/gitleaks dir . --no-banner >/dev/null 2>&1 || fail=1
fi
echo
echo '## 3. No live routes file committed'
echo '```'
git ls-files | grep -iE "routes\.local|\.env$|secret" || echo "(none)"
echo '```'
git ls-files | grep -qiE "routes\.local|\.env$" && { echo "**FAIL**"; fail=1; } || echo "PASS"
echo
echo '## 4. Tests'
echo '```'
python3 -m pytest -q -p no:warnings 2>&1 | tail -3
echo '```'
} > "$OUT" 2>&1
python3 -m pytest -q -p no:warnings >/dev/null 2>&1 || fail=1
grep -q "FAIL" "$OUT" && fail=1
echo "gate result: $([ $fail = 0 ] && echo PASS || echo FAIL)" | tee -a "$OUT"
exit $fail
