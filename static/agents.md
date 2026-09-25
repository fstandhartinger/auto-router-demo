# Install the auto router — instructions for AI agents

You are a coding agent and your user asked you to install the auto model router
(https://github.com/fstandhartinger/auto-model-router, MIT). Follow these steps in
order, stop and ask when a step does not hold, and never print an API key value.

> This is a short interim version. The full guide lives in the router repository:
> the "Running" and "Install it with a coding agent" sections of
> https://github.com/fstandhartinger/auto-model-router#running

## 1. Ask how the user wants to use it

Ask one question before you install anything: which tool do they use (Claude Code,
Codex, opencode, Cursor, another OpenAI-compatible client), and do they have a
Claude or ChatGPT subscription they want to keep using? The answer picks the mode:

- **Claude Code with a Claude plan:** switch mode (`auto-router switch`) —
  cheap models through the router by default, the plan when it matters. The
  router never sees the plan's login in this mode.
- **Claude Code or Codex, plan stays in charge:** the MCP `delegate` tool —
  the plan model hands self-contained sub-tasks to cheap models.
- **Whole jobs:** `auto-router run "task"` picks which official CLI runs a job.
- **Any OpenAI-compatible client:** point it at `http://127.0.0.1:8787/v1`,
  model `auto`.

## 2. Install

macOS, Linux, WSL:

```sh
curl -fsSL https://whichmodel.app.mintapis.com/install.sh | sh
```

Read the script before you run it and tell the user in one line what it does.
It needs Python 3.10+ and git, installs nothing system-wide, and only touches
`~/.auto-router` and `~/.local/bin`.

## 3. Keys, by name only

Open `~/.auto-router/config.yaml`. It references API keys by environment-variable
name. Tell the user which variables it wants and which are set (check with
`${VAR:+set}`, never echo the value). `TYPESAFE_API_KEY` (Jev) is optional; without
it a local or cautious default classifier is used.

## 4. Start and prove it

```sh
auto-router          # serves http://127.0.0.1:8787/v1
auto-router check    # in a second terminal: prints the chosen model and ends with OK
```

If `check` does not end with `OK`, stop and show the user the router's output.

## 5. Configure the user's tool

Back up any file you change and show the diff. Per-tool settings:
https://whichmodel.app.mintapis.com/run

## Rules

- Keys stay on the user's machine, referenced by variable name only.
- A subscription login is only ever used by its own official client, or — opt-in,
  own login, own machine — forwarded unchanged to its vendor. The router never
  stores it. Anthropic does not support routing Claude Code to non-Claude models.
- Evidence for what the router does and does not do:
  https://whichmodel.app.mintapis.com/evidence
