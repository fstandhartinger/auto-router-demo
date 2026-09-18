/* Auto-router playground — page router, playground, cache chat, catalog. */
import { renderResults } from "/assets/charts.js";

const state = {
  meta: null,
  colors: new Map(),
  session: localStorage.getItem("ar-session") || null,
  running: false,
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

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/** Minimal, safe markdown: fenced code, inline code, bold. Everything escaped first. */
function renderMarkdown(text) {
  let html = escapeHtml(text);
  html = html.replace(/```([a-z]*)\n([\s\S]*?)(?:```|$)/g,
    (_m, _lang, code) => `<pre><code>${code}</code></pre>`);
  html = html.replace(/`([^`\n]+)`/g, "<code>$1</code>");
  html = html.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
  return html;
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
}

/* ------------------------------------------------------------ page router */
const ROUTES = {
  "/": "playground", "/playground": "playground", "/cache": "cache",
  "/results": "results", "/how": "how", "/privacy": "privacy", "/impressum": "impressum",
};

function navigate(path, push = true) {
  const name = ROUTES[path] || "playground";
  if (push && location.pathname !== path) history.pushState({}, "", path);
  const tpl = el(`#page-${name}`);
  const main = el("#main");
  main.replaceChildren(tpl.content.cloneNode(true));
  els(".nav a").forEach((a) => {
    a.toggleAttribute("aria-current", a.dataset.nav === name);
    if (a.dataset.nav === name) a.setAttribute("aria-current", "page");
  });
  window.scrollTo({ top: 0, behavior: "instant" });
  ({ playground: initPlayground, cache: initCache, results: initResults, how: initHow }[name]
    || (() => {}))();
}

document.addEventListener("click", (event) => {
  const link = event.target.closest("a[data-link]");
  if (!link || event.metaKey || event.ctrlKey) return;
  const url = new URL(link.href);
  if (url.origin !== location.origin) return;
  event.preventDefault();
  navigate(url.pathname);
});
addEventListener("popstate", () => navigate(location.pathname, false));

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
    runPlayground(prompt.value.trim());
  });
}

function paintLimits(limits) {
  const note = el("#limit-note");
  if (!note || !limits) return;
  note.textContent =
    `${limits.perIpLeft} of ${limits.perIpPerHour} runs left for you this hour · ` +
    `answers are capped at ${limits.maxOutputTokens} tokens · ` +
    `${usd(limits.budgetLeftUsd, 2)} of today's ${usd(limits.budgetUsd, 2)} demo budget left.`;
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
  stopThinking();
  el("#thinking-box").hidden = true;
  el("#answer-model").textContent = "…";
  el("#stage").scrollIntoView({ behavior: "smooth", block: "start" });

  let answer = "";
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
        el("#answer").innerHTML = renderMarkdown(answer);
      } else if (event === "thinking") {
        startThinking();
      } else if (event === "thinking_done") {
        stopThinking(data);
      } else if (event === "rerouted") {
        el("#answer-note").hidden = false;
        el("#answer-note").textContent =
          `${data.from} did not start answering in time. The router moved this turn to ${data.to}.`;
      } else if (event === "answer_error") {
        el("#answer-note").hidden = false;
        el("#answer-note").textContent = data.message;
      } else if (event === "done") {
        paintLimits(data.limits);
        const usage = data.usage || {};
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
    el("#answer").classList.remove("cursor");
    button.disabled = false;
    state.running = false;
  }
}

function limitMessage(data) {
  if (data.error === "budget")
    return "The demo's budget for today is used up. It resets at midnight UTC — " +
           "the routing decision above still works, only the answers are paused.";
  if (data.error === "daily-cap")
    return "The demo has hit its daily run cap. Please come back tomorrow.";
  const mins = Math.ceil((data.retryAfter || 0) / 60);
  return `That is ${data.limits?.perIpPerHour ?? 20} runs this hour from your address. ` +
         `Try again in about ${mins} minute${mins === 1 ? "" : "s"}.`;
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
    <div class="flags">${flags.map(([label, value]) => {
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
          <span><b>${escapeHtml(row.label)}</b> ${badge(row)}
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
      <td class="num"><b>${usd(row.expectedUsd)}</b></td>`;
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
    `latency and decode speed for that route, plus the tokens it is expected to spend thinking.` +
    (weakCount ? ` ${weakCount} capability number${weakCount === 1 ? " rests" : "s rest"} on ` +
      `weak evidence and is pulled toward a neutral prior before it is used — hover it.` : "");
}

function paintDecision(data) {
  const sel = data.selection;
  const chosen = data.candidates.find((c) => c.chosen) || {};
  const saving = data.saving;
  const exec = data.execution;
  const notes = (data.notes || []).map((n) => `<li>${escapeHtml(n)}</li>`).join("");
  el("#decide-body").innerHTML = `
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
      ${exec && exec.substituted ? `<dt>Ran on</dt><dd>${escapeHtml(exec.label)} <span class="muted">(demo limit)</span></dd>` : ""}
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
      `, and paying more would buy less than the risk it removes.`;
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
      : (usable.find((m) => m.badge === "strong") || usable[0] || { name: "" }).name;
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
      <td class="num"><b>${usd(row.expectedUsd)}</b></td>`;
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
        body.innerHTML = renderMarkdown(answer);
      } else if (event === "answer_error") {
        body.textContent = data.message;
      }
    });
  } catch {
    body.textContent = "The connection dropped.";
  } finally {
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
navigate(location.pathname, false);


// ---------------------------------------------------------------------------
// "thinking…" with a clock
//
// Reasoning tokens used to be streamed into the page, which on an easy question
// meant several hundred lines of a model talking to itself before a one-line
// answer. The server now sends only the start and the end, and the visitor sees
// how long it took.
// ---------------------------------------------------------------------------
let thinkingTimer = null;
let thinkingStart = 0;

function startThinking() {
  const box = el("#thinking-box");
  if (!box) return;
  box.hidden = false;
  box.classList.add("is-live");
  thinkingStart = performance.now();
  const tick = () => {
    const s = (performance.now() - thinkingStart) / 1000;
    el("#thinking-text").textContent = `thinking… ${s.toFixed(1)}s`;
  };
  tick();
  clearInterval(thinkingTimer);
  thinkingTimer = setInterval(tick, 100);
}

function stopThinking(data) {
  clearInterval(thinkingTimer);
  thinkingTimer = null;
  const box = el("#thinking-box");
  if (!box) return;
  box.classList.remove("is-live");
  if (!data) return;
  box.hidden = false;
  el("#thinking-text").textContent =
    `thought for ${Number(data.seconds).toFixed(1)}s before answering`;
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
