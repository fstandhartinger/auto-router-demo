# Install the auto model router — instructions for AI agents

The router is open source: <https://github.com/fstandhartinger/auto-model-router>.
Install it only after you have explained the choices and the user has selected what to enable.
Never print, copy into a report, or send an API key or login token anywhere.

## 1. Detect the current setup without reading credentials

Check for these harnesses using their command names and standard configuration directories:

- Claude Code: `claude`, `~/.claude`
- Codex: `codex`, `~/.codex`
- OpenCode: `opencode`, `~/.config/opencode`
- OpenClaw: `openclaw`, `~/.openclaw`
- Hermes Agent: `hermes`, `~/.hermes`
- GitHub Copilot: `copilot` and the VS Code Copilot Chat extension
- Cursor: `cursor`, `cursor-agent`, `~/.cursor`

Check only whether these environment-variable names are set. Do not run `env`, `set`,
`printenv NAME`, or any command that displays their values:

```python
import os

for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY",
             "TENSORX_API_KEY", "TYPESAFE_API_KEY"):
    print(f"{name}: {'set' if name in os.environ else 'not set'}")
```

Also check whether a local model server is already listening on the usual LM Studio,
llama.cpp, or Ollama ports. Do not inspect its environment or logs for credentials.

## 2. Ask which models and harnesses to configure

Show the detection results, then ask the user which model groups and harnesses to include.
Do not enable every detected tool automatically.

- **Cloud API models:** only providers for which the user has configured their own key.
- **Subscription tools:** the user's installed official Claude Code or Codex CLI, through
  switch mode, `route-run`, or the MCP delegation tool.
- **Claude Code gateway pass-through:** a separate opt-in for `ANTHROPIC_BASE_URL` on this
  machine, using the user's own login. Explain the terms note below before enabling it.
- **Bonsai 2 local:** optional download of `prism-ml/Ternary-Bonsai-2-27B-gguf` for
  llama.cpp or LM Studio. It is treated as approximately zero marginal cost; its capability
  is an **assumption**, roughly like Qwen3.8-27B, not a measured router result.
- **Local Jev-class decision model:** the installer checks OS, RAM and GPU memory and
  recommends a compatible model. It prefers the measured open JevK5 tiers where they fit,
  and uses Laya as a last fallback. It skips models whose listed licence is
  non-commercial, evaluation-only, or otherwise unsuitable for general use.

Explain that Bonsai weights are several gigabytes and ask before downloading them. Ask the
user to choose which detected harnesses to configure. Windows can run the HTTP router and
harness integrations; `route-run` and the MCP delegate process-control features require WSL.

## 3. Review and run the installer for this OS

Read the installer source before running it. It creates a private virtual environment under
`~/.auto-router`, writes a configuration that refers to keys by variable name, and backs up
existing harness configuration before editing it. It does not install system-wide.

Linux, macOS, or WSL:

```sh
curl -fsSL https://whichmodel.app.mintapis.com/install.sh -o /tmp/auto-router-install.sh
cat /tmp/auto-router-install.sh
sh /tmp/auto-router-install.sh
```

Windows 11 PowerShell:

```powershell
$script = Join-Path $env:TEMP 'auto-router-install.ps1'
Invoke-WebRequest https://whichmodel.app.mintapis.com/install.ps1 -OutFile $script
Get-Content $script
powershell -ExecutionPolicy Bypass -File $script
```

Pass the user's model and harness selections as installer options only after they have
chosen them. Do not use `--yes` to skip those choices unless the user requested unattended
setup. Do not opt into Claude gateway mode unless the user selected it explicitly.

## 4. Configure and smoke-test the selected setup

Configure only the selected harnesses. Review the diffs and backup locations with the user.
Run `auto-router doctor` first. Then run one small smoke request through a model the user
selected; prefer a local model if one is available. Tell the user if the request may use a
paid API or subscription quota before sending it. Confirm that the router answers and that
the chosen harness can reach it. Never put request text, credentials, or full environment
output in a log or report.

If setup fails, stop and show the error without printing environment values. The installer
records its changes so `auto-router uninstall` can undo them.

## Subscription and terms note

Use the user's own official Claude Code or Codex client and login on their machine. Anthropic
documents `ANTHROPIC_BASE_URL` without a gateway credential as a way to keep a saved Claude
subscription login active, but says it does not support routing Claude Code to non-Claude
models through a gateway and restricts developers from handling Claude.ai session tokens.
The optional local pass-through does not store or log credentials, but this is not a legal
opinion or a guarantee of vendor support. Check the user's organization policy and agreement.
Switch mode, `route-run`, and the MCP delegation tool keep the plan login in its official
client and are the recommended defaults. Codex's documented proxy settings are not enabled
by this installer; use its official CLI through `route-run` or the MCP tool.

Sources: <https://code.claude.com/docs/en/llm-gateway>,
<https://code.claude.com/docs/en/legal-and-compliance>, and
<https://learn.chatgpt.com/docs/config-file/config-advanced>.
