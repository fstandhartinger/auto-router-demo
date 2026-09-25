/* Auto-router playground — page router, playground, cache chat, catalog. */
import { renderResults } from "/assets/charts.js";
import { renderEvidence } from "/assets/evidence.js";
import { escapeHtml, renderPlain, renderRich, renderersReady, warmRenderers } from "/assets/render.js";

const state = {
  meta: null,
  colors: new Map(),
  session: localStorage.getItem("ar-session") || null,
  running: false,
  limits: null,
};

const SERIES = ["--s1", "--s2", "--s3", "--s4", "--s5", "--s6", "--s7", "--s8"];

/* ----------------------------------------------------------------- utils */
const el = (sel, root = document) => root.querySelector(sel);
const els = (sel, root = document) => [...root.querySelectorAll(sel)];

function colorFor(name) {
  if (!state.colors.has(name)) {
    const slot = SERIES[state.colors.size % SERIES.length];
    state.colors.set(name, `var(${slot})`);
  }
  return state.colors.get(name);
}

function usd(v, digits) {
  if (v === null || v === undefined) return "—";
  if (v === 0) return "$0";
  const d = digits ?? (v < 0.01 ? 4 : v < 1 ? 3 : 2);
  return "$" + v.toFixed(d);
}

function perM(v) {
  if (v === null || v === undefined) return "—";
  return v === 0 ? "0" : v < 1 ? v.toFixed(2) : v.toFixed(v < 10 ? 1 : 0);
}

function pct(v, digits = 0) {
  return v === null || v === undefined ? "—" : (v * 100).toFixed(digits) + " %";
}

function tokens(n) {
  if (!n) return "0";
  if (n >= 1e6) return (n / 1e6).toFixed(n >= 1e7 ? 0 : 1) + "M";
  if (n >= 1000) return Math.round(n / 1000) + "k";
  return String(n);
}

function duration(s) {
  if (!s) return "none";
  if (s < 60) return s + " s";
  if (s < 3600) return Math.round(s / 60) + " min";
  return (s / 3600).toFixed(s % 3600 ? 1 : 0) + " h";
}

/** Markdown, maths and highlighted code once the renderers are in; escaped
 *  text with fenced code until then, and for good if the CDN never answers. */
function renderMarkdown(text) {
  return renderersReady() ? renderRich(text) : renderPlain(text);
}

/** Paint an answer that is still arriving.
 *
 * Re-parsing markdown and typesetting maths on every token is wasted work on a
 * fast route, so a stream repaints on a short timer and once more when it ends.
 */
function streamInto(node, text, final) {
  const now = performance.now();
  if (!final && node._paintedAt && now - node._paintedAt < 120) {
    clearTimeout(node._paintTimer);
    node._paintTimer = setTimeout(() => streamInto(node, node._pending ?? text, false), 130);
    node._pending = text;
    return;
  }
  clearTimeout(node._paintTimer);
  node._paintedAt = now;
  node._pending = null;
  node.innerHTML = renderMarkdown(text);
}

function checkedTag(checked) {
  // The judge is already inside the number next to this tag: a checked route's
  // failures cost a retry instead of a wrong answer, and the check and its
  // false alarms are charged for. The tag says which rows that applies to.
  if (!checked) return "";
  return '<span class="checked-tag" title="A cheap route: Jev checks this answer before you ' +
         'see it, and the expected cost already includes the check and the escalation it ' +
         'sometimes buys.">judged</span>';
}

function badge(row) {
  if (!row.badge) return "";
  return `<span class="badge badge-${row.badge}">${escapeHtml(row.badge)}</span>`;
}

/* -------------------------------------------------------------- SSE read */
async function readStream(response, onEvent) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let cut;
    while ((cut = buffer.indexOf("\n\n")) >= 0) {
      const chunk = buffer.slice(0, cut);
      buffer = buffer.slice(cut + 2);
      let event = "message";
      const dataLines = [];
      for (const line of chunk.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
      }
      if (!dataLines.length) continue;
      try { onEvent(event, JSON.parse(dataLines.join("\n"))); } catch { /* ignore */ }
    }
  }
}

/* ------------------------------------------------------------------ meta */
async function loadMeta() {
  if (state.meta) return state.meta;
  const resp = await fetch("/api/meta");
  state.meta = await resp.json();
  state.meta.models.forEach((m) => colorFor(m.name));
  paintP2P(state.meta.p2p);
  if (state.meta.repoUrl) el("#repo-link").href = state.meta.repoUrl;
  return state.meta;
}

function paintP2P(p2p) {
  const pill = el("#p2p-pill");
  if (!p2p || !p2p.configured) { pill.hidden = true; return; }
  pill.hidden = false;
  pill.textContent = p2p.online
    ? `P2P live · ${p2p.providersReady} volunteer GPU${p2p.providersReady === 1 ? "" : "s"}`
    : "P2P network offline";
  pill.style.opacity = p2p.online ? "1" : ".6";
  if (p2p.siteUrl) pill.href = p2p.siteUrl;
}

/* ------------------------------------------------------- copy to clipboard */
/* Every block on this site that is meant to be pasted somewhere gets a button,
 * because selecting eleven lines of JSON with a trackpad is a small misery. */
function addCopyButtons(root) {
  for (const block of root.querySelectorAll("pre.code")) {
    if (block.parentElement.classList.contains("code-wrap")) continue;
    const wrap = document.createElement("div");
    wrap.className = "code-wrap";
    block.replaceWith(wrap);
    wrap.appendChild(block);
    const button = document.createElement("button");
    button.type = "button";
    button.className = "copy-btn";
    button.textContent = "copy";
    button.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(block.textContent);
        button.textContent = "copied";
      } catch {
        button.textContent = "select and copy";
      }
      setTimeout(() => { button.textContent = "copy"; }, 1800);
    });
    wrap.appendChild(button);
  }
}

/* One-line commands (the install line) copy on click, wherever they appear. */
document.addEventListener("click", async (event) => {
  const code = event.target.closest("[data-copy]");
  if (!code) return;
  const text = code.textContent;
  try {
    await navigator.clipboard.writeText(code.dataset.copy);
    code.textContent = code.dataset.copied || "copied — paste it into a terminal";
  } catch {
    const range = document.createRange();
    range.selectNodeContents(code);
    getSelection().removeAllRanges();
    getSelection().addRange(range);
    return;
  }
  setTimeout(() => { code.textContent = text; }, 1600);
});

/* ------------------------------------------------------------ install bar */
/* The router is the product; this page is its demo. Every page but the setup
 * guide itself carries the one line that installs it, until it is dismissed. */
const BAR_KEY = "ar-install-bar-hidden";
function paintInstallBar(page) {
  const bar = el("#install-bar");
  if (!bar) return;
  let dismissed = false;
  try { dismissed = localStorage.getItem(BAR_KEY) === "1"; } catch { /* private mode */ }
  bar.hidden = dismissed || page === "run";
  document.body.classList.toggle("has-install-bar", !bar.hidden);
}
el("#install-bar-close")?.addEventListener("click", () => {
  try { localStorage.setItem(BAR_KEY, "1"); } catch { /* private mode */ }
  paintInstallBar("");
});

/* ------------------------------------------------------------ page router */
const ROUTES = {
  "/": "playground", "/playground": "playground", "/cache": "cache",
  "/results": "results", "/how": "how", "/run": "run",
  "/privacy": "privacy", "/impressum": "impressum",
  "/evidence": "evidence", "/claims": "evidence",
};

function navigate(path, push = true, hash = location.hash) {
  const name = ROUTES[path] || "playground";
  state.path = path;
  if (push && location.pathname + location.hash !== path + hash) history.pushState({}, "", path + hash);
  const tpl = el(`#page-${name}`);
  const main = el("#main");
  main.replaceChildren(tpl.content.cloneNode(true));
  els(".nav a").forEach((a) => {
    a.toggleAttribute("aria-current", a.dataset.nav === name);
    if (a.dataset.nav === name) a.setAttribute("aria-current", "page");
  });
  addCopyButtons(main);
  paintInstallBar(name);
  const ready = ({ playground: initPlayground, cache: initCache, results: initResults, how: initHow,
     run: initRun, evidence: initEvidence }[name] || (() => {}))();
  // A deep link (/evidence#claims, /how#models, or /claims) lands on its section,
  // once the page has drawn whatever it loads; otherwise the page starts at the top.
  const anchor = path === "/claims" ? "claims" : decodeURIComponent(hash.slice(1));
  window.scrollTo({ top: 0, behavior: "instant" });
  if (anchor) Promise.resolve(ready).catch(() => {}).then(() => {
    document.getElementById(anchor)?.scrollIntoView({ behavior: "instant", block: "start" });
  });
}

document.addEventListener("click", (event) => {
  const link = event.target.closest("a[data-link]");
  if (!link || event.metaKey || event.ctrlKey) return;
  const url = new URL(link.href);
  if (url.origin !== location.origin) return;
  event.preventDefault();
  navigate(url.pathname, true, url.hash);
});
// A same-page fragment link (#claims) fires popstate too; the page is already
// drawn, so let the browser scroll instead of rebuilding it.
addEventListener("popstate", () => {
  if (location.pathname === state.path && location.hash) return;
  navigate(location.pathname, false);
});

/* ------------------------------------------------------------- playground */
async function initPlayground() {
  const meta = await loadMeta();
  const box = el("#examples");
  box.replaceChildren(...meta.examples.map((ex) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "example";
    button.innerHTML = `<b>${escapeHtml(ex.title)}</b> <span>· ${escapeHtml(ex.hint)}</span>`;
    button.addEventListener("click", () => {
      el("#prompt").value = ex.prompt;
      el("#prompt").dispatchEvent(new Event("input"));
      el("#prompt").focus();
    });
    return button;
  }));
  paintLimits(meta.limits);

  const prompt = el("#prompt");
  prompt.addEventListener("input", () => {
    el("#counter").textContent = `${prompt.value.length} / ${meta.limits.maxPromptChars}`;
  });
  prompt.addEventListener("keydown", (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") el("#composer").requestSubmit();
  });

  el("#composer").addEventListener("submit", (event) => {
    event.preventDefault();
    if (!prompt.value.trim() || state.running) return;
    const soft = localLimitMessage();
    if (soft) {
      el("#stage").hidden = false;
      el("#decide-body").innerHTML = `<p class="notice">${escapeHtml(soft)}</p>`;
      el("#stage").scrollIntoView({ behavior: "smooth", block: "start" });
      return;
    }
    runPlayground(prompt.value.trim());
  });
}

function paintLimits(limits) {
  const note = el("#limit-note");
  if (!note || !limits) return;
  state.limits = limits;
  note.classList.toggle("is-paused", Boolean(limits.paidPaused));
  note.textContent =
    `${limits.perIpLeft} of ${limits.perIpPerHour} runs left for you this hour ` +
    `(${limits.perIpPerDay} a day) · ` +
    `answers are capped at ${limits.maxOutputTokens} tokens · ` +
    (limits.paidPaused
      ? `today's ${usd(limits.budgetUsd, 2)} budget for paid routes is spent — the routing still ` +
        "runs and free routes still answer, until midnight UTC."
      : `${usd(limits.budgetLeftUsd, 2)} of today's ${usd(limits.budgetUsd, 2)} demo budget left.`);
}

/* --------------------------------------------------------- local counter */
/* A courtesy check in the browser, so someone who has used up their runs is
 * told so at once instead of watching a request go out and come back refused.
 * It is not a security boundary - the server counts independently and is the
 * only counter that decides anything. Clearing site data resets this one and
 * changes nothing about that. */
const RUNS_KEY = "ar-runs";

function localRuns() {
  let list;
  try { list = JSON.parse(localStorage.getItem(RUNS_KEY) || "[]"); } catch { list = []; }
  const cutoff = Date.now() - 86400e3;
  const kept = Array.isArray(list) ? list.filter((t) => typeof t === "number" && t > cutoff) : [];
  return kept;
}

function recordLocalRun() {
  const kept = localRuns();
  kept.push(Date.now());
  try { localStorage.setItem(RUNS_KEY, JSON.stringify(kept.slice(-400))); } catch { /* private mode */ }
}

/** A friendly refusal if this browser is already over a cap, else null. */
function localLimitMessage() {
  const limits = state.limits;
  if (!limits) return null;
  const runs = localRuns();
  const hour = Date.now() - 3600e3;
  const inHour = runs.filter((t) => t > hour).length;
  if (inHour >= limits.perIpPerHour) {
    const oldest = runs.filter((t) => t > hour)[0];
    const mins = Math.max(1, Math.ceil((oldest + 3600e3 - Date.now()) / 60000));
    return `You have used all ${limits.perIpPerHour} runs this hour. This demo pays for every ` +
           `answer, so it keeps the limit low — try again in about ${mins} minute${mins === 1 ? "" : "s"}. ` +
           "Everything except the answer is free: the cache explorer below still works.";
  }
  if (runs.length >= limits.perIpPerDay) {
    return `You have used all ${limits.perIpPerDay} runs this demo allows one visitor per day. ` +
           "Run the router locally instead — it is one command, and then it is your own keys " +
           "and no limit at all.";
  }
  return null;
}

async function runPlayground(text) {
  state.running = true;
  const button = el("#run-btn");
  button.disabled = true;
  el("#stage").hidden = false;
  el("#classify-body").innerHTML =
    '<div class="skeleton-row"></div><div class="skeleton-row short"></div>';
  el("#decide-body").innerHTML = '<p class="muted">Pricing every route…</p>';
  el("#candidates-table").querySelector("tbody").replaceChildren();
  el("#candidates-foot").textContent = "";
  el("#answer").textContent = "";
  el("#answer").classList.add("cursor");
  el("#answer-foot").textContent = "";
  el("#answer-note").hidden = true;
  el("#verify-chip").hidden = true;
  el("#verify-chip").className = "verify-chip";
  el("#first-answer").hidden = true;
  el("#first-answer").open = false;
  el("#first-answer-body").textContent = "";
  stopThinking();
  el("#thinking-box").hidden = true;
  el("#answer-model").textContent = "…";
  el("#stage").scrollIntoView({ behavior: "smooth", block: "start" });

  let answer = "";
  recordLocalRun();
  try {
    const resp = await fetch("/api/run", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt: text }),
    });
    if (resp.status === 429) {
      const data = await resp.json();
      el("#decide-body").innerHTML =
        `<p class="notice">${escapeHtml(limitMessage(data))}</p>`;
      paintLimits(data.limits);
      return;
    }
    if (resp.status === 503) {
      el("#decide-body").innerHTML =
        '<p class="notice">The demo is still reading the model catalog from the benchmark API. ' +
        'Give it a few seconds and try again.</p>';
      return;
    }
    if (!resp.ok) {
      el("#decide-body").innerHTML = '<p class="notice">The demo could not start this run.</p>';
      return;
    }
    await readStream(resp, (event, data) => {
      if (event === "decision") {
        paintClassification(data);
        paintCandidates(data);
        paintDecision(data);
        paintP2P(data.p2p);
      } else if (event === "answering") {
        el("#answer-model").textContent = data.label;
        if (data.note) { el("#answer-note").hidden = false; el("#answer-note").textContent = data.note; }
        if (data.thinkingNote) el("#answer-foot").textContent = data.thinkingNote;
      } else if (event === "delta") {
        answer += data.text;
        streamInto(el("#answer"), answer, false);
      } else if (event === "thinking") {
        startThinking();
      } else if (event === "reasoning") {
        appendThinking(data.text);
      } else if (event === "thinking_done") {
        stopThinking(data);
      } else if (event === "rerouted") {
        el("#answer-note").hidden = false;
        el("#answer-note").textContent =
          `${data.from} did not start answering in time. The router moved this turn to ${data.to}.`;
      } else if (event === "verify") {
        paintVerdict(data);
      } else if (event === "escalated") {
        // The first answer does not disappear - it moves into a collapsed
        // block under the verdict, struck through, and the second answer
        // streams into the empty space below it.
        streamInto(el("#answer"), answer, true);
        el("#first-answer-body").innerHTML = el("#answer").innerHTML;
        el("#first-answer-label").innerHTML =
          `<s>${escapeHtml(data.from)}'s answer</s> — replaced`;
        el("#first-answer").hidden = false;
        el("#answer").innerHTML = "";
        answer = "";
        el("#answer-model").textContent = data.to;
      } else if (event === "answer_error") {
        el("#answer-note").hidden = false;
        el("#answer-note").textContent = data.message;
      } else if (event === "done") {
        paintLimits(data.limits);
        streamInto(el("#answer"), answer, true);
        const usage = data.usage || {};
        if (usage.reasoningTokens) noteThinkingTokens(usage.reasoningTokens);
        el("#answer-foot").textContent = data.costUsd === null ? "" :
          `This answer cost ${data.costUsd === 0 ? "nothing (a free route)" : usd(data.costUsd, 5)} · ` +
          `${usage.promptTokens || 0} prompt tokens` +
          (usage.cachedTokens ? ` (${usage.cachedTokens} read from cache)` : "") +
          ` · ${usage.completionTokens || 0} tokens out.`;
      }
    });
  } catch {
    el("#answer-note").hidden = false;
    el("#answer-note").textContent = "The connection dropped before the answer finished.";
  } finally {
    streamInto(el("#answer"), answer, true);
    el("#answer").classList.remove("cursor");
    button.disabled = false;
    state.running = false;
  }
}

function paintVerdict(data) {
  const chip = el("#verify-chip");
  if (!data.chip && !data.reason) { chip.hidden = true; return; }
  chip.hidden = false;
  const scale = "0 to 1, where 1 means Jev is sure the answer fully and correctly " +
                "answers the request. Below the threshold for this topic, the turn escalates.";
  if (!data.checked) {
    // Not every answer can be checked, and the reason is worth reading: a
    // frontier model is not graded by a classifier, and a question about a
    // pasted document cannot be graded without the document.
    chip.className = "verify-chip is-skipped";
    chip.title = "";
    chip.textContent = `Not checked — ${data.reason}.`;
    return;
  }
  chip.title = `P(adequate) = ${data.p} on a scale of ${scale}`;
  if (data.escalate) {
    // Said once, above the answer it explains: this answer is the second one.
    chip.className = "verify-chip is-flagged";
    chip.innerHTML =
      `<b>${escapeHtml(data.label)} failed Jev's check</b> — ${escapeHtml(data.failure)}, ` +
      `${fixed(data.p, 2)} of 1 against a threshold of ${fixed(data.threshold, 2)}` +
      (data.escalatedTo
        ? `. The answer below is ${escapeHtml(data.escalatedTo)}'s.`
        : `, and no stronger route was available, so its answer stands.`);
  } else {
    chip.className = "verify-chip";
    chip.textContent =
      `Checked by Jev: adequate — ${fixed(data.p, 2)} of 1, and this topic escalates below ` +
      `${fixed(data.threshold, 2)} · ${Math.round(data.latencyMs)} ms`;
  }
}

function fixed(value, digits) {
  return Number(value || 0).toFixed(digits);
}

function limitMessage(data) {
  const limits = data.limits || {};
  const mins = Math.max(1, Math.ceil((data.retryAfter || 0) / 60));
  const later = mins < 90 ? `about ${mins} minute${mins === 1 ? "" : "s"}`
                          : `about ${Math.round(mins / 60)} hours`;
  const selfHost = " You can run the router locally with your own keys instead — " +
                   "one command, no limits; see “Run it yourself”.";
  switch (data.error) {
    case "budget":
      return "The demo's budget for today is used up. It resets at midnight UTC — the routing " +
             "decision still works and free routes still answer.";
    case "daily-cap":
      return "The whole demo has hit its daily run cap. Please come back tomorrow." + selfHost;
    case "per-ip-day":
      return `That is all ${limits.perIpPerDay ?? 30} runs this demo allows one visitor per day.` +
             selfHost;
    case "per-subnet":
    case "per-subnet-day":
      return "That is the limit for your whole network — this demo pays for every answer, so it " +
             `counts the network as well as the address. Try again in ${later}.` + selfHost;
    default:
      return `That is ${limits.perIpPerHour ?? 10} runs this hour from your address. ` +
             `Try again in ${later}.` + selfHost;
  }
}

const DIFFICULTY_WORDS = ["trivial", "easy", "moderate", "hard", "frontier"];

function paintClassification(data) {
  const c = data.classification;
  const meta = data.classifier || {};
  el("#classifier-chip").textContent =
    meta.kind === "jev" ? "Jev by TypeSafe AI"
      : meta.kind === "fallback" ? `fallback: ${meta.model}` : "no classifier";
  el("#classifier-chip").className = "chip " + (meta.kind === "jev" ? "chip-quiet" : "chip-warn");

  const probs = Object.entries(data.categoryProbabilities || {})
    .filter(([, v]) => v > 0.001).sort((a, b) => b[1] - a[1]).slice(0, 4);
  const level = Math.min(4, Math.round(c.difficulty * 4));
  const flags = [
    ["needs tools", c.needs_tools], ["long context", c.needs_long_context],
    ["an image", c.needs_vision], ["builds on the last turn", c.follow_up > 0.5],
  ];

  el("#classify-body").innerHTML = `
    <div class="topline">
      <span class="big">${escapeHtml(c.category.replace("_", " "))}</span>
      <span class="muted">${c.category_confidence ? pct(c.category_confidence) + " confident" : ""}</span>
    </div>
    ${probs.length > 1 ? `<div class="prob-list">${probs.map(([k, v]) => `
      <div class="prob"><span>${escapeHtml(k.replace("_", " "))}</span>
        <span class="bar"><i style="width:${Math.max(2, v * 100)}%"></i></span>
        <span class="val">${pct(v)}</span></div>`).join("")}</div>` : ""}
    <dl class="kv" style="margin-top:.9rem">
      <dt>Difficulty</dt>
      <dd><strong>${level} / 4</strong> — ${DIFFICULTY_WORDS[level]}
        <div class="meter">${[1, 2, 3, 4].map((i) =>
          `<i class="${i <= level ? "on" : ""}"></i>`).join("")}</div></dd>
      <dt>Stakes</dt><dd>${usd(c.stakes_usd, 2)} if it is subtly wrong and nobody notices</dd>
      <dt>Latency</dt><dd>${Math.round(c.latency_ms)} ms</dd>
      <dt>Prompt</dt><dd>${data.promptTokens} tokens · a whole turn is priced at ${data.outputTokensAssumed} out</dd>
    </dl>
    <div class="flags">${flags.every(([, v]) => !(v === true || v > 0.5))
        ? '<span class="flags-label">none of these:</span>' : ""}${flags.map(([label, value]) => {
      const on = value === true || value > 0.5;
      return `<span class="flag ${on ? "on" : "off"}">${on ? "✓ " : ""}${escapeHtml(label)}</span>`;
    }).join("")}</div>`;
}

function paintCandidates(data) {
  const tbody = el("#candidates-table").querySelector("tbody");
  tbody.replaceChildren(...data.candidates.map((row) => {
    const tr = document.createElement("tr");
    if (row.chosen) tr.className = "is-chosen";
    const weak = ["weak", "none"].includes(row.evidenceStrength);
    tr.innerHTML = `
      <td>
        <span class="m-name">
          <i class="dot" style="background:${colorFor(row.name)}"></i>
          <span><b>${escapeHtml(row.label)}</b> ${badge(row)}${row.frontier
            ? '<span class="checked-tag frontier-tag" title="Priced and choosable; this free demo does not run it">not run here</span>' : ""}
            <span class="m-org">${escapeHtml(row.org)}</span></span>
        </span>
      </td>
      <td class="num"><span title="${escapeHtml(row.capabilityBasis)}"
        class="${weak ? "weak" : ""}">${row.capabilityHere.toFixed(1)}</span></td>
      <td class="num"><span class="price-stack">${perM(row.prices.input)} · ${perM(row.prices.output)}
        <span>· ${row.prices.cacheRead === null ? "no cache" : perM(row.prices.cacheRead)}</span></span></td>
      <td class="num">${pct(row.pSuccess)}</td>
      <td class="num">${usd(row.callUsd)}</td>
      <td class="num">${secs(row.expectedSeconds)}${thinkTag(row.thinking)}</td>
      <td class="num"><b>${usd(row.expectedUsd)}</b>${checkedTag(row.checked)}</td>`;
    return tr;
  }));
  const weakCount = data.candidates.filter((c) =>
    ["weak", "none"].includes(c.evidenceStrength)).length;
  el("#candidates-foot").innerHTML =
    `Capability is the score for <em>this</em> topic, from Benchmark Heaven where a benchmark ` +
    `measures it. P(success) is our own measurement on 78 graded tasks where we have one, ` +
    `otherwise a curve fitted on them. Expected cost adds what a failure would cost: a retry ` +
    `on a stronger route, or the price of a wrong answer nobody notices — and what the wait ` +
    `is worth, at ${usd(data.secondUsd || 0.002, 4)} a second. Expected time is measured first-token ` +
    `latency and decode speed for that route, plus the length of answer it was measured to ` +
    `write for a request like this and the tokens it is expected to spend thinking. Every route ` +
    `here is one you could use yourself: an OpenRouter public endpoint at its public price — the ` +
    `<span class="chip chip-quiet">free</span> ones included — or the peer-to-peer swarm. ` +
    `A route marked <span class="checked-tag">judged</span> is cheap enough that Jev checks its ` +
    `answer before you see it, so its expected cost is priced with the check in it — the failures ` +
    `it catches cost a second call rather than a wrong answer.` +
    (weakCount ? ` ${weakCount} capability number${weakCount === 1 ? " rests" : "s rest"} on ` +
      `weak evidence and is pulled toward a neutral prior before it is used — hover it.` : "");
}

function paintDecision(data) {
  const sel = data.selection;
  const chosen = data.candidates.find((c) => c.chosen) || {};
  const saving = data.saving;
  const exec = data.execution;
  const notes = (data.notes || []).map((n) => `<li>${escapeHtml(n)}</li>`).join("");
  const fr = data.frontier;
  const frontierBox = fr ? `<div class="frontier-callout">
      <p class="frontier-kicker">Frontier pick — not run on this free demo</p>
      <p class="frontier-line">The router would send this to <strong>${escapeHtml(fr.label)}</strong>
        <span class="muted">(expected cost ${usd(fr.callUsd)} for this answer)</span>.</p>
      <p class="frontier-sub">That is too expensive for a free page to give away${
        exec && exec.model !== fr.model ? `, so the answer below is from <b>${escapeHtml(exec.label)}</b>,
        the best affordable route` : ""}. Run the router on your own machine and
        ${escapeHtml(fr.label)} answers requests like this one — on your key, at that price.</p>
      <p class="frontier-actions"><a class="primary-btn small-btn" href="/run" data-link>
        <span class="btn-label">Run it yourself</span></a>
        <code class="hero-cmd copyable" data-copy="curl -fsSL https://whichmodel.app.mintapis.com/install.sh | sh">curl -fsSL whichmodel.app.mintapis.com/install.sh | sh</code></p>
    </div>` : "";
  el("#decide-body").innerHTML = `${frontierBox}
    <div class="decision-model">
      <i class="dot" style="background:${colorFor(sel.selected)}"></i>
      <strong>${escapeHtml(chosen.label || sel.selected)}</strong> ${badge(chosen)}
    </div>
    <p class="decision-reason">${escapeHtml(sentence(sel.reason, chosen, data))}</p>
    ${saving ? `<div class="saving">
      <span class="num">${saving.chosenUsd === 0 ? "Free here"
        : saving.factor ? saving.factor + "× cheaper" : usd(saving.savedUsd) + " cheaper"}</span>
      <div class="sub">${usd(saving.savedUsd)} saved on this turn against sending every turn to
        ${escapeHtml(saving.baseline.label)}, which would cost ${usd(saving.baseline.callUsd)}${
        Math.abs(chosen.pSuccess - saving.baseline.pSuccess) < 0.02
          ? " and is no more likely to get this right"
          : `, at ${pct(chosen.pSuccess)} versus ${pct(saving.baseline.pSuccess)} chance of getting it right`}.</div>
    </div>` : ""}
    <dl class="kv" style="margin-top:.9rem">
      <dt>Cache</dt><dd>${cacheWords(data.cache)}</dd>
      <dt>Second choice</dt><dd>${escapeHtml(labelOf(data, sel.fallback) || "—")}</dd>
      <dt>Considered</dt><dd>${sel.candidates_considered} routes · confidence in the evidence ${pct(sel.evidence_confidence)}</dd>
      ${exec && exec.substituted ? `<dt>Ran on</dt><dd>${escapeHtml(exec.label)} <span class="muted">(${fr ? "frontier routes are not run here" : "demo limit"})</span></dd>` : ""}
    </dl>
    ${notes ? `<ul class="limits" style="margin-top:.6rem;font-size:.84rem">${notes}</ul>` : ""}`;
}

function labelOf(data, name) {
  const row = (data.candidates || []).find((c) => c.name === name);
  return row ? row.label : name;
}

function sentence(reason, chosen, data) {
  const category = data.classification.category.replace("_", " ");
  if (/min expected cost/.test(reason)) {
    return `Lowest expected total for a ${category} turn at this difficulty: ` +
      `${chosen.pSuccess >= 0.95 ? "it is very likely to get this right" :
        chosen.pSuccess >= 0.8 ? "it is likely enough to get this right" :
        "nothing here is safe, so the router buys the best chance per dollar"}` +
      `, and paying more — in money or in waiting — would buy less than the risk it removes` +
      `${chosen.expectedSeconds ? `. Expected time to a finished answer: about ${secs(chosen.expectedSeconds)}` : ""}` +
      `${chosen.thinking === "off" ? ", with the thinking pass switched off for a request this easy" : ""}.`;
  }
  return reason;
}

function cacheWords(cache) {
  if (!cache) return "—";
  if (cache.status === "warm")
    return `warm — ${tokens(cache.warm_tokens)} tokens already there, ` +
      `${cache.estimated_usd_avoided !== null ? usd(cache.estimated_usd_avoided) + " saved" : "no price basis recorded"}`;
  if (cache.status === "too-short")
    return `nothing cacheable — the prompt is under this route's ${tokens(cache.min_cacheable_tokens)}-token minimum`;
  if (cache.status === "no-cache") return "this route has no prefix cache";
  return "cold — first turn on this route";
}

/* ------------------------------------------------------------------ chat */
const PREFIX_STEPS = [2000, 8000, 20000, 50000, 100000, 160000, 250000, 400000];
const DIFF_STEPS = [0.05, 0.3, 0.5, 0.72, 0.95];
const DIFF_NAMES = ["trivial", "easy", "moderate", "hard", "frontier"];

async function initCache() {
  const meta = await loadMeta();
  await initWhatIf(meta);

  const scenario = await fetch("/api/scenario").then((r) => r.json());
  el("#attachment").innerHTML = `
    <div class="file">📄 ${escapeHtml(scenario.name)}
      <span class="sz">· about ${tokens(scenario.approxTokens)} tokens, pasted once</span></div>
    <p>${escapeHtml(scenario.about)}</p>
    <pre>${escapeHtml(scenario.preview)}</pre>
    <div class="suggest">${scenario.suggestions.map((q) =>
      `<button type="button" class="example" data-q="${escapeHtml(q)}">${escapeHtml(q)}</button>`).join("")}</div>`;
  els("#attachment .example").forEach((b) => b.addEventListener("click", () => {
    el("#chat-prompt").value = b.dataset.q;
    el("#chat-prompt").focus();
  }));

  const steps = meta.pauseSteps || [0, 60, 300, 600, 1800, 3600];
  const slider = el("#pause");
  slider.max = String(steps.length - 1);
  const out = el("#pause-out");
  const paint = () => { out.textContent = duration(steps[+slider.value]); };
  slider.addEventListener("input", paint);
  paint();

  el("#chat-reset").addEventListener("click", async () => {
    const d = await fetch("/api/session/reset", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session: state.session }),
    }).then((r) => r.json());
    state.session = d.session;
    localStorage.setItem("ar-session", d.session);
    el("#chat").replaceChildren();
    el("#cache-decision").innerHTML = '<p class="muted">Send the first message.</p>';
    el("#cache-curve").innerHTML = '<p class="muted">Appears after the first answer.</p>';
  });

  el("#chat-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const text = el("#chat-prompt").value.trim();
    if (!text || state.running) return;
    el("#chat-prompt").value = "";
    sendChat(text, steps[+slider.value]);
  });
}

/* -------------------------------------------------------------- what-if */
async function initWhatIf(meta) {
  const select = el("#w-current");
  const fill = () => {
    const allowFree = el("#w-free").checked;
    const usable = meta.models.filter((m) =>
      !m.peerToPeer && (allowFree || m.prices.input > 0));
    const keep = select.value;
    select.replaceChildren(...[{ name: "", label: "nothing yet (cold start)" }, ...usable]
      .map((m) => new Option(m.label, m.name)));
    select.value = usable.some((m) => m.name === keep) ? keep
      : (usable.find((m) => m.name === "gpt-5.6-sol") || usable.find((m) => m.badge === "strong")
        || usable[0] || { name: "" }).name;
  };
  el("#w-free").addEventListener("change", () => { fill(); runWhatIf(); });
  fill();

  const bind = (id, outId, format) => {
    const input = el(id);
    const paint = () => { el(outId).textContent = format(+input.value); };
    input.addEventListener("input", paint);
    input.addEventListener("change", runWhatIf);
    paint();
  };
  bind("#w-prefix", "#w-prefix-out", (i) => tokens(PREFIX_STEPS[i]) + " tokens");
  bind("#w-diff", "#w-diff-out", (i) => DIFF_NAMES[i]);
  select.addEventListener("change", runWhatIf);
  await runWhatIf();
}

async function runWhatIf() {
  const body = {
    promptTokens: PREFIX_STEPS[+el("#w-prefix").value],
    outputTokens: 1500,
    category: "coding",
    difficulty: DIFF_STEPS[+el("#w-diff").value],
    current: el("#w-current").value || null,
    excludeFree: !el("#w-free").checked,
  };
  const data = await fetch("/api/what-if", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }).then((r) => r.json());
  if (data.error) return;

  const chosen = data.rows.find((r) => r.chosen) || {};
  const current = data.rows.find((r) => r.isCurrent);
  const stayed = current && chosen.name === current.name;
  el("#w-verdict").innerHTML = `
    <div class="head">
      <i class="dot" style="width:10px;height:10px;border-radius:50%;background:${colorFor(chosen.name)}"></i>
      <strong>${stayed ? "Stay on " : current ? "Switch to " : "Start on "}${escapeHtml(chosen.label || "—")}</strong>
      ${badge(chosen)}
    </div>
    <p class="why">${escapeHtml(data.reason)}${
      stayed && current.cacheSavesUsd
        ? ` — leaving would throw away ${usd(current.cacheSavesUsd)} of warm prefix on this turn alone.`
        : current && !stayed
          ? ` — the cache on ${escapeHtml(current.label)} is worth ${usd(current.cacheSavesUsd)} here, and that is less than the gap.`
          : ""}</p>`;

  el("#w-table").querySelector("tbody").replaceChildren(...data.rows.map((row) => {
    const tr = document.createElement("tr");
    if (row.chosen) tr.className = "is-chosen";
    tr.innerHTML = `
      <td><span class="m-name"><i class="dot" style="background:${colorFor(row.name)}"></i>
        <span><b>${escapeHtml(row.label)}</b> ${badge(row)}
        ${row.isCurrent ? '<span class="badge">you are here</span>' : ""}
        <span class="m-org">${escapeHtml(row.org)}</span></span></span></td>
      <td class="num">${row.warmTokens ? tokens(row.warmTokens) + " warm" : "cold"}</td>
      <td class="num">${usd(row.warmUsd)}</td>
      <td class="num">${usd(row.coldUsd)}</td>
      <td class="num">${row.cacheSavesUsd ? usd(row.cacheSavesUsd) : "—"}</td>
      <td class="num"><b>${usd(row.expectedUsd)}</b>${checkedTag(row.checked)}</td>`;
    return tr;
  }));

  const first = data.curve[0].selected;
  el("#w-curve").innerHTML = data.curve.map((row) => `
    <div class="curve-row ${row.selected !== first ? "flip" : ""}">
      <span class="t">${duration(row.pauseSeconds)}</span>
      <span class="m"><i class="dot" style="background:${colorFor(row.selected)}"></i>
        ${escapeHtml(row.label)}${row.stayed ? " — stay" : ""}
        <span class="muted">${row.currentWarm ? "cache still warm" : "cache expired"}</span></span>
      <span class="t">${usd(row.callUsd)}</span>
    </div>`).join("");
}

/* ------------------------------------------------------- the live chat */
function addMessage(who, text) {
  const div = document.createElement("div");
  div.className = "msg " + who;
  div.innerHTML = `<div class="who">${who === "user" ? "You" : "Router"}</div><div class="body"></div>`;
  el(".body", div).textContent = text;
  el("#chat").append(div);
  div.scrollIntoView({ behavior: "smooth", block: "nearest" });
  return div;
}

async function sendChat(text, pauseSeconds) {
  state.running = true;
  if (pauseSeconds) {
    const note = document.createElement("p");
    note.className = "muted";
    note.style.fontSize = ".82rem";
    note.textContent = `— ${duration(pauseSeconds)} passes —`;
    el("#chat").append(note);
  }
  addMessage("user", text);
  const bubble = addMessage("assistant", "");
  const body = el(".body", bubble);
  body.classList.add("cursor");
  let answer = "";
  try {
    const resp = await fetch("/api/chat", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt: text, session: state.session, pauseSeconds }),
    });
    if (resp.status === 429) {
      body.textContent = limitMessage(await resp.json());
      return;
    }
    await readStream(resp, (event, data) => {
      if (event === "decision") {
        state.session = data.session;
        localStorage.setItem("ar-session", data.session);
        paintCacheDecision(data, bubble);
        paintChatCurve(data);
        paintP2P(data.p2p);
      } else if (event === "delta") {
        answer += data.text;
        streamInto(body, answer, false);
      } else if (event === "answer_error") {
        body.textContent = data.message;
      }
    });
  } catch {
    body.textContent = "The connection dropped.";
  } finally {
    if (answer) streamInto(body, answer, true);
    body.classList.remove("cursor");
    state.running = false;
  }
}

function paintCacheDecision(data, bubble) {
  const sel = data.selection;
  const chosen = data.candidates.find((c) => c.chosen) || {};
  const moved = sel.switched_from && sel.switched_from !== sel.selected;
  const warm = data.cache && data.cache.status === "warm";
  const strip = document.createElement("div");
  strip.className = "turn-strip";
  strip.innerHTML = `
    <span class="verdict ${moved ? "switch" : "stay"}">${
      moved ? "switched" : data.turn > 1 ? "stayed" : "first turn"}</span>
    <span><i class="dot" style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${colorFor(sel.selected)}"></i>
      ${escapeHtml(chosen.label || sel.selected)}</span>
    <span>· ${warm ? `cache warm, ${tokens(data.cache.warm_tokens)} tokens` : "cache cold"}</span>
    <span>· ${usd(chosen.callUsd)} this turn</span>`;
  bubble.append(strip);

  el("#cache-decision").innerHTML = `
    <div class="decision-model">
      <i class="dot" style="background:${colorFor(sel.selected)}"></i>
      <strong>${escapeHtml(chosen.label || sel.selected)}</strong>
    </div>
    <p class="decision-reason">${escapeHtml(sel.reason)}</p>
    <dl class="kv" style="margin-top:.7rem">
      <dt>Turn</dt><dd>${data.turn}</dd>
      <dt>Cache</dt><dd>${cacheWords(data.cache)}</dd>
      <dt>Simulated clock</dt><dd>${duration(data.clockOffset)} of pauses so far</dd>
      <dt>Prompt</dt><dd>${data.promptTokens} tokens</dd>
    </dl>
    ${data.saving ? `<div class="saving"><span class="num">${usd(data.saving.savedUsd)}</span>
      <div class="sub">saved on this turn against sending every turn to
      ${escapeHtml(data.saving.baseline.label)}.</div></div>` : ""}`;
}

function paintChatCurve(data) {
  const curve = data.cacheCurve || [];
  if (!curve.length) return;
  const first = curve[0].selected;
  const flips = curve.some((r) => r.selected !== first);
  el("#cache-curve").innerHTML = `
    <p class="muted" style="font-size:.84rem">The same next turn, decided after different pauses.
      Only the clock changes.</p>
    <div class="curve">${curve.map((row) => `
      <div class="curve-row ${row.selected !== first ? "flip" : ""}">
        <span class="t">${duration(row.pauseSeconds)}</span>
        <span class="m"><i class="dot" style="background:${colorFor(row.selected)}"></i>
          ${escapeHtml(row.label)}</span>
        <span class="t">${usd(row.callUsd)}</span>
      </div>`).join("")}</div>
    <p class="muted" style="font-size:.8rem;margin-top:.7rem">${flips
      ? "The highlighted rows are where the decision flips: the cache has expired, so staying buys nothing."
      : "No flip here. These routes are free, so their cache is worth nothing in cash — the panel above shows the same question with metered routes, where it decides everything."}</p>`;
}

/* --------------------------------------------------------------- results */
async function initResults() {
  const resp = await fetch("/api/results");
  renderResults(el("#results-root"), await resp.json());
}

/* -------------------------------------------------------------- evidence */
/* The study file is dropped in when the study is done. Until then the page draws
 * the sample file and says so, rather than showing nothing or passing an
 * invented number off as a measured one. */
async function loadJson(url) {
  const resp = await fetch(url, { cache: "no-cache" });
  if (!resp.ok) throw new Error(`${url}: ${resp.status}`);
  return resp.json();
}

async function initEvidence() {
  const root = el("#evidence-root");
  let data, sample = false;
  try {
    data = await loadJson("/data/ab-study.json");
  } catch {
    try {
      data = await loadJson("/data/ab-study.sample.json");
      sample = true;
    } catch {
      root.innerHTML = '<p class="muted">The study data could not be loaded.</p>';
      return;
    }
  }
  if (data.sample === true) sample = true;
  const replay = await loadJson("/api/results").then((r) => r.replay_list).catch(() => null);
  if (!root.isConnected) return;
  renderEvidence(root, data, { sample, replay });
}

/* ------------------------------------------------------------------- how */
async function initHow() {
  const meta = await loadMeta();
  const tbody = el("#catalog-table").querySelector("tbody");
  tbody.replaceChildren(...meta.models.map((m) => {
    const tr = document.createElement("tr");
    const all = Object.entries(m.capabilityBasis || {}).map(([k, v]) => `${k}: ${v}`).join("\n");
    const one = (m.capabilityBasis || {}).coding || (m.capabilityBasis || {}).general || "";
    tr.innerHTML = `
      <td><span class="m-name"><i class="dot" style="background:${colorFor(m.name)}"></i>
        <span><b>${escapeHtml(m.label)}</b> ${badge(m)}<span class="m-org">${escapeHtml(m.org)}</span></span></span></td>
      <td class="num">${tokens(m.contextTokens)}</td>
      <td class="num"><span class="price-stack">${perM(m.prices.input)} · ${perM(m.prices.output)}
        <span>· ${m.prices.cacheRead === null ? "no cache" : perM(m.prices.cacheRead)}</span></span></td>
      <td class="num">${m.cache.ttlSeconds ? duration(m.cache.ttlSeconds) : "none"} · ${pct(m.cache.hitRate)}</td>
      <td class="basis" title="${escapeHtml(all)}">${escapeHtml(one || "set in the demo's own config")}</td>`;
    return tr;
  }));

  const p2p = meta.p2p || {};
  const box = el("#p2p-status-box");
  if (!p2p.configured) {
    box.innerHTML = `<div class="notice-box"><strong>Not wired up yet.</strong>
      The swarm is being built. The route, its price, its context window and its
      capability are already in the catalog; it is switched on from the server the
      moment its endpoint answers.</div>`;
  } else if (p2p.online) {
    box.innerHTML = `<div class="notice-box p2p-on"><strong>${p2p.providersReady} volunteer GPU${p2p.providersReady === 1 ? "" : "s"} online.</strong>
      The route is in the catalog for every decision right now, and a request sent there
      is answered on somebody's computer. ${p2p.queueLength ? `${p2p.queueLength} request(s) queued.` : ""}
      <a href="${escapeHtml(p2p.siteUrl)}" rel="noopener">Join the swarm</a>.</div>`;
  } else {
    box.innerHTML = `<div class="notice-box"><strong>No volunteers online right now.</strong>
      So the router cannot use the route, and it is taken out of the catalog rather than
      being sent requests that nobody will answer.
      <a href="${escapeHtml(p2p.siteUrl)}" rel="noopener">Share a GPU</a> and it comes back.</div>`;
  }
}

/* ----------------------------------------------------------------- theme */
function initTheme() {
  // Dark-first: the page ships dark and only a deliberate toggle changes it.
  const saved = localStorage.getItem("ar-theme");
  if (saved) document.documentElement.dataset.theme = saved;
  paintThemeIcon();
  el("#theme-toggle").addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    localStorage.setItem("ar-theme", next);
    paintThemeIcon();
  });
}

const SUN = '<path d="M12 5V3m0 18v-2m7-7h2M3 12h2m11.9-4.9 1.4-1.4M5.7 18.3l1.4-1.4m9.8 1.4 1.4 1.4M5.7 5.7 7.1 7.1" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/><circle cx="12" cy="12" r="4" fill="currentColor"/>';
const MOON = '<path d="M20 14.5A8.5 8.5 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5Z" fill="currentColor"/>';

function paintThemeIcon() {
  const dark = document.documentElement.dataset.theme !== "light";
  const button = el("#theme-toggle");
  button.querySelector("svg").innerHTML = dark ? SUN : MOON;
  button.setAttribute("aria-label", dark ? "Switch to the light theme" : "Switch to the dark theme");
}

initTheme();
// Markdown, KaTeX and the highlighter are fetched while the visitor is still
// reading the page, so the first answer is already typeset when it arrives.
// If they never arrive, answers render as escaped text and nothing else breaks.
warmRenderers();
navigate(location.pathname, false);


// ---------------------------------------------------------------------------
// "thinking…" — a collapsible block with a clock and a token count
//
// A model's private reasoning is not an answer and must never be the page's
// main column: it opens as one line with a running timer, expands if the
// visitor wants to read it, and closes itself the moment the answer starts.
// ---------------------------------------------------------------------------
let thinkingTimer = null;
let thinkingStart = 0;
let thinkingChars = 0;
let thinkingSeconds = 0;
let thinkingExactTokens = null;

/** Tokens, near enough: providers that report the real count overwrite this. */
function approxTokens(chars) {
  return Math.max(1, Math.round(chars / 4));
}

function thinkingLabel(live) {
  const count = thinkingExactTokens ?? approxTokens(thinkingChars);
  const size = thinkingChars ? ` · ${thinkingExactTokens ? "" : "~"}${tokens(count)} tokens` : "";
  return live
    ? `thinking… ${thinkingSeconds.toFixed(1)}s${size}`
    : `thought for ${thinkingSeconds.toFixed(1)}s${size}`;
}

function paintThinkingLabel(live) {
  const text = el("#thinking-text");
  if (text) text.textContent = thinkingLabel(live);
}

function startThinking() {
  const box = el("#thinking-box");
  if (!box) return;
  box.hidden = false;
  box.open = true;          // while it is the only thing happening, show it
  box.classList.add("is-live");
  const stream = el("#thinking-stream");
  if (stream) stream.textContent = "";
  thinkingStart = performance.now();
  thinkingChars = 0;
  thinkingSeconds = 0;
  thinkingExactTokens = null;
  paintThinkingLabel(true);
  clearInterval(thinkingTimer);
  thinkingTimer = setInterval(() => {
    thinkingSeconds = (performance.now() - thinkingStart) / 1000;
    paintThinkingLabel(true);
  }, 100);
}

function appendThinking(text) {
  const stream = el("#thinking-stream");
  if (!stream) return;
  thinkingChars += text.length;
  stream.textContent += text;
  if (el("#thinking-box").open) stream.scrollTop = stream.scrollHeight;
}

function stopThinking(data) {
  clearInterval(thinkingTimer);
  thinkingTimer = null;
  const box = el("#thinking-box");
  if (!box) return;
  box.classList.remove("is-live");
  if (!data) { box.hidden = true; return; }
  box.hidden = false;
  // The answer is what the visitor came for: once it starts, this folds away.
  box.open = false;
  thinkingSeconds = Number(data.seconds) || 0;
  if (data.truncated) {
    const stream = el("#thinking-stream");
    if (stream) stream.textContent += "\n\n[…the rest of the thinking is not sent to the browser]";
  }
  paintThinkingLabel(false);
}

function noteThinkingTokens(count) {
  thinkingExactTokens = count;
  paintThinkingLabel(false);
}


// One route's expected wall-clock, and whether it was allowed to think.
function secs(value) {
  if (value === null || value === undefined) return "—";
  return value < 10 ? `${value.toFixed(1)}s` : `${Math.round(value)}s`;
}

function thinkTag(level) {
  if (level === "off") return ' <span class="think-tag" title="Thinking switched off for this request">no thinking</span>';
  if (level === "low") return ' <span class="think-tag" title="Thinking capped for this request">capped</span>';
  return "";
}


/* ---------------------------------------------------------- run it yourself */
function initRun() {
  paintSwarm(state.meta && state.meta.p2p);
  loadMeta().then((meta) => paintSwarm(meta.p2p));
  // The count on this page is the swarm's own live one, not a number from
  // whenever the tab was opened: the server re-reads its public stats endpoint
  // and this asks again while the page is on screen.
  const tick = async () => {
    if (!document.body.contains(el("#swarm-card"))) { clearInterval(timer); return; }
    try {
      const fresh = await (await fetch("/api/meta")).json();
      state.meta = fresh;
      paintSwarm(fresh.p2p);
      paintP2P(fresh.p2p);
    } catch { /* a blip is not worth a broken page */ }
  };
  const timer = setInterval(tick, 30000);
  tick();
  // The subscription-aware mode is a separate piece of work; the link only
  // appears here if the server says it exists, so the page never promises it.
  const slot = el("#subscription-link");
  const url = state.meta && state.meta.subscriptionUrl;
  if (slot && url) {
    slot.innerHTML = `<a href="${escapeHtml(url)}" rel="noopener">How that is wired up here</a>.`;
  }
}

function paintSwarm(p2p) {
  const box = el("#swarm-status");
  const chip = el("#swarm-chip");
  if (!box || !chip) return;
  if (!p2p) { chip.textContent = "checking…"; return; }
  if (!p2p.configured) {
    chip.textContent = "not wired up here";
    chip.className = "chip chip-quiet";
    box.innerHTML = `<div class="notice-box"><strong>This copy of the demo has no swarm
      endpoint configured</strong>, so the route is not in its catalog. On the public demo
      it is, and it competes like any other route.</div>`;
    return;
  }
  const ready = p2p.providersReady || 0;
  chip.textContent = p2p.online ? `${ready} GPU${ready === 1 ? "" : "s"} online` : "nobody online";
  chip.className = "chip " + (p2p.online ? "chip-good" : "chip-quiet");
  box.innerHTML = p2p.online
    ? `<div class="notice-box p2p-on"><strong>${ready} volunteer GPU${ready === 1 ? "" : "s"} online right now.</strong>
        The route is in the catalog for every decision this demo makes, and a request sent
        there is answered on somebody else's computer.
        ${p2p.tokensToday ? `${tokens(p2p.tokensToday)} tokens served today.` : ""}</div>`
    : `<div class="notice-box"><strong>Nobody is online right now.</strong>
        So the router drops the route instead of sending requests nobody will answer — open
        the swarm in a tab with a GPU and it comes back within thirty seconds.</div>`;
}
