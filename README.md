# auto-router demo

A public playground for [auto-model-router](https://github.com/fstandhartinger/auto-model-router):
type a prompt, watch a router classify it, price every model for *that* request, pick one, and
stream the answer back from whichever model that turned out to be.

**Live:** https://whichmodel.app.mintapis.com

It is not a mock-up. The page drives the published router package at a pinned commit — the same
catalog, cost model, success model, expected-cost policy and decision record that produced the
measured results. This repository only adds the web surface, the guard rails a public demo needs,
and the peer-to-peer integration.

## What it shows

| Page | What it is |
|---|---|
| **Playground** | One turn end to end: the classification with its probabilities and latency, every candidate priced with its capability, cache price, measured success rate and expected cost, the decision with a reason and the saving against always using a frontier model, then the streamed answer with what it actually cost. |
| **Cache** | The differentiator. A decision explorer that asks the policy what it would do at an agent-sized prefix — nothing is sent to a model — and shows what a warm cache is worth, plus where the decision flips once the cache expires. Below it, a real multi-turn conversation over a pasted document, with a slider that moves the clock. |
| **Results** | The measured findings: policy comparison, price against quality, cache read share per route, how good the classifier is, and the limits of all of it. |
| **How it works** | The four moving parts, how to run the router yourself, and the peer-to-peer network as a routing target. |

## Architecture

```
browser ──SSE──► FastAPI (app/)
                   ├── classify.py   Jev, with a labelled free-model fallback
                   ├── engine.py     builds the catalog, drives the router's policy
                   ├── providers.py  OpenAI-compatible streaming to whoever serves a model
                   ├── bonsai.py     the peer-to-peer network as one more route
                   └── limits.py     per-IP, per-day and per-dollar caps
                         │
                         └── auto_router/  (cloned at a pinned commit by the Dockerfile)
```

Everything the browser sees comes from `RoutingExplanation.to_dict()` — the router's own
prompt-free decision record — plus presentation on top. The demo never second-guesses the routing:
if the chosen model is too expensive for a public demo to run, the page says so and answers from
the best route it may use, leaving the decision untouched.

## Running it

```bash
./scripts/vendor_router.sh          # fetches auto_router/ at the pinned commit
pip install -r requirements.txt

export TYPESAFE_API_KEY=...         # the classifier; without it the fallback is used
export DEMO_ROUTES_FILE=routes.local.json
uvicorn app.main:app --port 8080
```

`routes.local.json` says which endpoint serves each model in `app/data/catalog.json` and which
environment variable holds that endpoint's key — see `examples/routes.example.json`. It is
deliberately **not** in this repository: the catalog (what the router reasons about) is public,
the account it runs on is not. A model with no route is still priced and can still be chosen; the
demo just cannot run it.

### Configuration

| Variable | Default | What it does |
|---|---|---|
| `DEMO_ROUTES_JSON` / `DEMO_ROUTES_FILE` | – | providers and routes, as JSON or a path |
| `TYPESAFE_API_KEY` | – | Jev. Missing or unreachable ⇒ the labelled fallback classifier |
| `DEMO_RUNS_PER_IP_PER_HOUR` | `20` | per-visitor rate limit |
| `DEMO_RUNS_PER_DAY` | `1200` | global run cap |
| `DEMO_DAILY_BUDGET_USD` | `3.0` | metered spend the demo may use per day |
| `DEMO_MAX_CALL_USD` | `0.03` | a single answer may not be estimated above this |
| `DEMO_MAX_PROMPT_CHARS` | `4000` | prompts are truncated, not refused |
| `DEMO_MAX_OUTPUT_TOKENS` | `900` | answers are capped |
| `BONSAI_BASE_URL` | – | the peer-to-peer network's base URL; unset ⇒ the route is off |
| `BONSAI_API_TOKEN` | – | the demo's account on that network |
| `AUTO_ROUTER_BENCH_URL` | `https://benchmarkheaven.com` | the benchmark API |

## The peer-to-peer route

The [Bonsai swarm](https://bonsai-swarm.app.mintapis.com) runs a ternary 27B model on WebGPU in
volunteers' browsers and exposes an OpenAI-compatible endpoint. Nothing in the router privileges
it: it enters the catalog with a price of zero, a 32k context window, no prefix cache and a modest
hand-set capability, and competes on those numbers — which means it wins easy and medium turns and
loses hard ones. Its public stats endpoint says how many volunteers are online; when none are, the
route is dropped from the catalog for the next decision and the page says so, rather than sending
requests nobody will answer.

## Privacy and limits

No prompt and no answer is stored. The server keeps counters (a salted, restart-scoped hash of the
address, a run count, a dollar total) and the router's prompt-free decision record in memory for
the page you are looking at. Multi-turn conversations live in memory for the length of a session
and are never written to disk. Prompts are sent to the classifier and to whichever model answers,
and — when that is the peer-to-peer route — to a volunteer's computer, which the page says before
you use it.

The routing results this demo displays rest on 78 graded tasks and one week of one team's traffic.
The dollar figures on the results page are replay arithmetic at public list prices, not an invoice.

## Tests

```bash
python3 -m pytest -q
```

Hermetic: a fixed catalog with explicit prices, a stub classifier and a fake upstream. They cover
classification, the candidate table and its ordering, the saving against the baseline, the
peer-to-peer route being dropped when nobody is online, the cache arithmetic and where it flips,
the three rate limits, the budget cap, and that a prompt never reaches the decision record.

## Credit

Classifier: **Jev** by [TypeSafe AI](https://typesafe.ai). Model data: **Benchmark Heaven**, which
serves numbers from [Artificial Analysis](https://artificialanalysis.ai) and
[Epoch AI](https://epoch.ai). Router: [auto-model-router](https://github.com/fstandhartinger/auto-model-router), MIT.

MIT licensed.
