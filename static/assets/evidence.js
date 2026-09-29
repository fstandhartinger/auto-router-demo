/* Evidence page: the A/B study, drawn from one JSON file, no chart library.
   Arm A (always the frontier model) is orange, arm B (the router) is blue on
   every chart on this page, and difficulty is a shape, never a third colour. */
import { showTip, hideTip, esc, money, section, barChart, tableView } from "/assets/charts.js";

const COLOR = { A: "var(--s2)", B: "var(--s1)" };
const DIFF_ORDER = ["easy", "medium", "hard"];
const SHAPE_NAME = { easy: "circle", medium: "square", hard: "triangle" };

const usd = (v) => (v === null || v === undefined || Number.isNaN(v)) ? "—"
  : v === 0 ? "$0" : money(v);
const mean = (xs) => xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null;
const sum = (xs) => xs.reduce((a, b) => a + (b || 0), 0);
const ratio = (a, b) => (b > 0 ? a / b : null);
const times = (r) => r === null || r === undefined ? "—" : (r >= 10 ? r.toFixed(0) : r.toFixed(1)) + "×";

function diffRank(d) {
  const i = DIFF_ORDER.indexOf(d);
  return i < 0 ? DIFF_ORDER.length : i;
}

/** Scores may come as 0–1, 0–10 or 0–100; the page says which. */
function scoreScale(tasks) {
  const all = tasks.flatMap((t) => [t.A?.score, t.B?.score]).filter((v) => typeof v === "number");
  const max = Math.max(0, ...all);
  return max <= 1 ? 1 : max <= 10 ? 10 : 100;
}

function scoreFmt(scale) {
  if (scale === 1) return (v) => v === null || v === undefined ? "—" : (v * 100).toFixed(0) + " %";
  if (scale === 10) return (v) => v === null || v === undefined ? "—" : v.toFixed(1);
  return (v) => v === null || v === undefined ? "—" : v.toFixed(0);
}

function shape(kind, x, y, fill) {
  const s = `fill="${fill}" stroke="var(--surface)" stroke-width="2"`;
  if (kind === "medium") return `<rect x="${x - 6}" y="${y - 6}" width="12" height="12" rx="2" ${s}/>`;
  if (kind === "hard") return `<path d="M${x},${y - 8} L${x + 7.5},${y + 5.5} L${x - 7.5},${y + 5.5} Z" ${s}/>`;
  return `<circle cx="${x}" cy="${y}" r="6.5" ${s}/>`;
}

function shapeIcon(kind) {
  return `<svg viewBox="0 0 20 20" width="12" height="12" aria-hidden="true">${
    shape(kind, 10, 11, "var(--ink-2)").replace('stroke="var(--surface)"', 'stroke="none"')}</svg>`;
}

function wireTips(root) {
  root.querySelectorAll("[data-tip]").forEach((node) => {
    node.addEventListener("pointermove", (e) => showTip(e, node.dataset.tip));
    node.addEventListener("pointerleave", hideTip);
  });
}

function firstChoiceStudy(data) {
  const q = data.quality;
  const costs = data.accounted_cost_usd_per_task;
  const latency = data.latency_ms_per_task;
  const s = section("First-choice API A/B (29 Sep 2026)",
    "Forty fixed public-safe tasks compared one Claude Opus 5.5 completion with the source router's " +
    "F_expected first choice. Task-level results, prompts and the protocol summary are linked below.");
  s.id = "router-ab-2026-09-29";
  const tiles = document.createElement("div");
  tiles.className = "stat-row";
  const tile = (value, key, note) => `<div class="stat"><span class="v">${esc(value)}</span>` +
    `<span class="k">${esc(key)}</span><span class="s">${esc(note)}</span></div>`;
  const pp = (v) => `${v > 0 ? "+" : ""}${v.toFixed(1)}`;
  tiles.innerHTML =
    tile("+2.5 pp", "router minus Opus pass rate",
      `30/40 vs 29/40; 95% paired CI ${pp(q.delta_router_minus_control.bootstrap_95_ci[0] * 100)} to ${pp(q.delta_router_minus_control.bootstrap_95_ci[1] * 100)} pp`) +
    tile("5.27%", "router / Opus accounted cost",
      `$${costs.control.mean.toFixed(6)} vs $${costs.router.mean.toFixed(6)} per task; 95% CI 4.49–6.13%`) +
    tile("+7.66 s", "router minus Opus latency",
      `${(latency.control.mean / 1000).toFixed(3)} s vs ${(latency.router.mean / 1000).toFixed(3)} s; 95% CI 3.91–12.28 s`) +
    tile("$0.269939", "total model API spend", "80 completions; no provider errors");
  s.append(tiles);

  const cats = Object.entries(data.categories).map(([name, row]) => {
    const delta = row.quality_delta_router_minus_control * 100;
    const [lo, hi] = row.quality_delta_bootstrap_95_ci.map((v) => v * 100);
    const sign = (v) => `${v > 0 ? "+" : ""}${v.toFixed(0)}`;
    return [name, `${row.control.passed}/${row.control.n}`, `${row.router.passed}/${row.router.n}`,
      `${sign(delta)} pp (${sign(lo)} to ${sign(hi)})`,
      `${sign(row.e2e_latency_delta_router_minus_control_ms_per_task / 1000)} s`];
  });
  s.append(tableView(["category", "Opus passed", "router passed", "pass-rate delta (95% CI)", "latency delta"], cats));

  const routes = Object.entries(data.route_distribution.router).map(([model, count]) => `${model}: ${count}`);
  const note = document.createElement("p");
  note.className = "section-note";
  note.textContent = `The router chose ${routes.join("; ")}. The overall quality interval is broad and spans both a 7.5-point drop and a 12.5-point gain. ` +
    "This measures the source router policy; the live playground adds a value-of-time term. " +
    "Router latency includes warm local classification; one-time Weiche initialization took 4.595 s and is separate. " +
    "The small synthetic task set used one completion per task and arm; answer checks and retries were disabled. HTML grading checks structure, not appearance.";
  s.append(note);
  const links = document.createElement("p");
  links.className = "credits";
  links.innerHTML = '<a href="/data/paired-router-ab-20260929/README.md">Task set and per-task results</a> · ' +
    '<a href="/data/first-choice-ab-20260929.json">Machine-readable summary</a> · ' +
    '<a href="https://github.com/fstandhartinger/auto-model-router/blob/2299e35cc75f7d21eabb5433787f5e2db4779690/EXPERIMENTS.md#19-paired-first-choice-api-ab-29-september-2026" rel="noopener">Method in EXPERIMENTS.md</a>';
  s.append(links);
  return s;
}

function legend(labels) {
  return `<div class="legend">
    <span><i style="background:${COLOR.A}"></i>A · ${esc(labels.A)}</span>
    <span><i style="background:${COLOR.B}"></i>B · ${esc(labels.B)}</span>
  </div>`;
}

/* ------------------------------------------------------------- scatter */
function pairScatter({ tasks, labels, fmt, scale }) {
  // A phone gets a taller, narrower drawing: the SVG scales to its box, so a
  // wide viewBox would shrink the type and the marks to a few pixels.
  const narrow = innerWidth < 600;
  const W = narrow ? 420 : 820, H = narrow ? 460 : 420, L = narrow ? 64 : 58, R = 24, T = 16, B = 56;
  const costs = tasks.flatMap((t) => [t.A.cost_list_usd, t.B.cost_list_usd]);
  const positive = costs.filter((c) => c > 0);
  const floor = positive.length ? Math.min(...positive) / 2 : 0.001;
  const cx = (c) => Math.max(c || 0, floor);
  const lo = Math.log10(floor * 0.8), hi = Math.log10(Math.max(...costs.map(cx)) * 1.5);
  const scores = tasks.flatMap((t) => [t.A.score, t.B.score]).filter((v) => typeof v === "number");
  const step = scale === 1 ? 0.1 : scale === 10 ? 1 : 10;
  const yMin = Math.max(0, Math.floor(Math.min(...scores) / step) * step - step);
  const yMax = Math.min(scale, Math.ceil(Math.max(...scores) / step) * step);
  const px = (v) => L + ((Math.log10(cx(v)) - lo) / (hi - lo)) * (W - L - R);
  const py = (v) => H - B - ((v - yMin) / ((yMax - yMin) || 1)) * (H - T - B);
  const candidates = [0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 25, 50, 100];
  let xTicks = candidates.filter((t) => t >= 10 ** lo && t <= 10 ** hi);
  while (xTicks.length > (narrow ? 4 : 6)) xTicks = xTicks.filter((_, i) => i % 2 === 0);
  const yTicks = [];
  for (let v = yMin; v <= yMax + 1e-9; v += step) yTicks.push(+v.toFixed(3));

  const point = (t, arm) => {
    const r = t[arm];
    const tip = `<b>${esc(t.title)}</b><span class="t-sub">Arm ${arm} · ${esc(r.model || labels[arm])}<br>` +
      `${esc(t.difficulty)} · score ${fmt(r.score)} · ${r.cost_list_usd === 0 ? "$0 (free or local)" : usd(r.cost_list_usd)} at list price</span>`;
    return `<g class="pt" data-tip="${esc(tip)}">${shape(t.difficulty, px(r.cost_list_usd), py(r.score), COLOR[arm])}</g>`;
  };

  const figure = document.createElement("figure");
  figure.className = "chart";
  figure.innerHTML = `<h3>Cost against score, every task in both arms</h3>
    <p class="sub">Each grey line joins one task's two runs. Left is cheaper, up is better. The x-axis
      is logarithmic${positive.length < costs.length ? "; runs that cost nothing sit at the left edge" : ""}.</p>
    ${legend(labels)}
    <div class="legend">${DIFF_ORDER.filter((d) => tasks.some((t) => t.difficulty === d)).map((d) =>
      `<span>${shapeIcon(d)}${esc(d)} (${SHAPE_NAME[d]})</span>`).join("")}</div>
    <svg class="svg-chart" viewBox="0 0 ${W} ${H}" role="img"
         aria-label="Scatter of list-price cost against score for ${tasks.length} tasks, arm A and arm B">
      ${yTicks.map((v) => `<line class="grid-line" x1="${L}" x2="${W - R}" y1="${py(v)}" y2="${py(v)}"/>
        <text x="${L - 8}" y="${py(v) + 4}" text-anchor="end">${fmt(v)}</text>`).join("")}
      <line class="axis-line" x1="${L}" x2="${W - R}" y1="${H - B}" y2="${H - B}"/>
      ${xTicks.map((t) => `<text x="${px(t)}" y="${H - B + 24}" text-anchor="middle">${money(t)}</text>`).join("")}
      <text x="${(W + L) / 2}" y="${H - 6}" text-anchor="middle" class="lbl-strong">${narrow ? "cost (log scale)" : "list-price cost of the task (log scale)"}</text>
      ${tasks.map((t) => `<line x1="${px(t.A.cost_list_usd)}" y1="${py(t.A.score)}" x2="${px(t.B.cost_list_usd)}"
          y2="${py(t.B.score)}" stroke="var(--line-strong)" stroke-width="1.5"/>`).join("")}
      ${tasks.map((t) => point(t, "A")).join("")}
      ${tasks.map((t) => point(t, "B")).join("")}
    </svg>`;
  wireTips(figure);
  return figure;
}

/* --------------------------------------------------------- grouped bars */
function pairedBars({ title, sub, groups, format, labels, showRatio }) {
  const max = Math.max(0, ...groups.flatMap((g) => [g.A, g.B]).filter((v) => typeof v === "number")) || 1;
  const figure = document.createElement("figure");
  figure.className = "chart";
  const row = (g, arm) => {
    const v = g[arm];
    const share = typeof v === "number" ? Math.max(0.004, v / max) : 0;
    return `<div class="pair-row" data-tip="${esc(`<b>${esc(g.label)}</b><span class="t-sub">Arm ${arm}: ${format(v)}</span>`)}">
      <span class="pair-arm">${arm}</span>
      <span class="bar-track"><span class="bar-fill" style="width:${share * 100}%;background:${COLOR[arm]}"></span></span>
      <span class="bar-val">${format(v)}</span></div>`;
  };
  figure.innerHTML = `<h3>${esc(title)}</h3>${sub ? `<p class="sub">${sub}</p>` : ""}${legend(labels)}
    <div class="pair-groups">${groups.map((g) => `
      <div class="pair-group">
        <div class="pair-head"><span>${esc(g.label)}</span>${showRatio && g.ratio !== null && g.ratio !== undefined
          ? `<span class="pair-ratio">A / B = ${times(g.ratio)}</span>` : ""}</div>
        ${row(g, "A")}${row(g, "B")}
        ${g.note ? `<p class="pair-note">${esc(g.note)}</p>` : ""}
      </div>`).join("")}</div>`;
  wireTips(figure);
  return figure;
}

/* ----------------------------------------------------------------- page */
function stat(v, k, s) {
  return `<div class="stat"><span class="v">${v}</span><span class="k">${k}</span>${s ? `<div class="s">${s}</div>` : ""}</div>`;
}

function taskTable(tasks, fmt) {
  const wrap = document.createElement("div");
  wrap.className = "table-scroll card evidence-tasks";
  const cell = (r) => `<span class="ev-score">${fmt(r.score)}</span>
    <span class="ev-sub">${usd(r.cost_list_usd)}${r.tests_total ? ` · tests ${r.tests_passed}/${r.tests_total}` : ""}${
      r.renders === false ? " · did not render" : ""}</span>`;
  const shots = (t) => ["A", "B"].filter((a) => t[a].screenshot).map((a) =>
    `<a href="${esc(t[a].screenshot)}" rel="noopener" class="shot-link" title="Arm ${a} screenshot">
      <img src="${esc(t[a].screenshot)}" alt="Arm ${a}: ${esc(t.title)}" loading="lazy" class="shot-thumb"
           style="border-color:${COLOR[a]}"><span>${a}</span></a>`).join("");
  const anyShots = tasks.some((t) => t.A.screenshot || t.B.screenshot);
  wrap.innerHTML = `<table class="candidates ev-table">
    <caption class="sr-only">Every task, both arms</caption>
    <thead><tr><th scope="col">Task</th><th scope="col" class="num">A<span class="th-sub">score · cost</span></th>
      <th scope="col" class="num">B<span class="th-sub">score · cost</span></th>
      ${anyShots ? '<th scope="col">Screens</th>' : ""}</tr></thead>
    <tbody>${tasks.map((t) => `<tr>
      <td><b>${esc(t.title)}</b>
        <span class="ev-sub"><span class="badge">${esc(t.difficulty)}</span> ${esc(t.category || "")}</span>
        ${(t.B.routes || []).length ? `<span class="ev-sub">B used: ${t.B.routes.map((r) =>
          `${esc(r.model)} ×${r.turns}`).join(", ")}${t.B.escalations ? ` · ${t.B.escalations} escalation${t.B.escalations > 1 ? "s" : ""}` : ""}</span>` : ""}</td>
      <td class="num">${cell(t.A)}</td><td class="num">${cell(t.B)}</td>
      ${anyShots ? `<td class="shots">${shots(t)}</td>` : ""}</tr>`).join("")}</tbody></table>`;
  return wrap;
}

function simulation(replay) {
  const rows = replay?.rows || [];
  const a = rows.find((r) => r.key === "A_static");
  const f = rows.find((r) => r.key === "F_expected");
  const s = section("Analyzed on real traffic",
    "One week of one team's real coding-agent traffic " +
    "(1,638 sessions, 57,696 calls) was analyzed in a replay, re-priced as if each policy had routed it, using measured " +
    "per-model success rates and public list prices.");
  s.id = "simulation";
  s.querySelector("h2").insertAdjacentHTML("afterbegin", '<span class="sim-tag">SIMULATION</span> ');
  if (!a || !f || !rows.length) {
    const unavailable = document.createElement("p");
    unavailable.className = "section-note";
    unavailable.textContent = "The replay data could not be loaded, so no savings or quality ratio is shown.";
    s.append(unavailable);
    return s;
  }
  const tiles = document.createElement("div");
  tiles.className = "stat-row";
  tiles.innerHTML =
    stat(`${money(a.usd_week)} → ${money(f.usd_week)}`, "per week, simulated",
      `${esc(a.label.replace(/^A — /, ""))} against the router's default rule`) +
    stat(times(a.usd_week / f.usd_week), "cheaper, simulated", "the source of the “about 9×” figure") +
    stat(`${(a.success * 100).toFixed(1)} → ${(f.success * 100).toFixed(1)} %`, "turns solved, simulated",
      `${((a.success - f.success) * 100).toFixed(1)} points given up`);
  s.append(tiles);
  s.append(barChart({
    title: "Weekly cost by routing rule (simulation)",
    sub: "Replay arithmetic at public list prices. Lower is cheaper.",
    rows: rows.filter((r) => r.usd_week).map((r) => ({
      label: r.label, value: r.usd_week, tip: `${money(r.usd_week)} a week · ${(r.success * 100).toFixed(1)} % solved`,
    })),
    format: money,
    highlight: (r) => /expected cost/.test(r.label),
  }));
  const note = document.createElement("p");
  note.className = "section-note";
  note.innerHTML = "This is a <strong>simulation</strong>, not a measurement: no money was saved and " +
    "none was measured, no invoice was compared, and the replay knows the whole week in advance in a " +
    "way a live router does not. It ranks rules against each other; it is not a cash result. " +
    '<a href="/results" data-link>The full replay and the 78-task run</a>.';
  s.append(note);
  return s;
}

export function renderEvidence(root, data, { sample, replay }) {
  root.replaceChildren();
  if (data.status === "not-run") {
    root.append(simulation(replay));
    return;
  }
  const method = data.method || {};
  const labels = {
    A: method.arms?.A?.label || "always the frontier model",
    B: method.arms?.B?.label || "the router",
  };
  const tasks = [...(data.tasks || [])].filter((t) => t.A && t.B)
    .sort((x, y) => diffRank(x.difficulty) - diffRank(y.difficulty));
  const scale = scoreScale(tasks);
  const fmt = scoreFmt(scale);
  const scaleNote = scale === 1 ? "share of the rubric met" : `out of ${scale}`;

  if (sample) {
    const banner = document.createElement("div");
    banner.className = "sample-banner";
    banner.setAttribute("role", "note");
    banner.innerHTML = `<strong>Sample data.</strong> The study results are not published here yet, so
      this page is showing an invented example file to demonstrate the layout. None of the numbers
      in the A/B section below are measurements.`;
    root.append(banner);
  }

  if (!sample && data.current_paired) root.append(firstChoiceStudy(data.current_paired));

  /* headline ------------------------------------------------------------ */
  const sum_ = data.summary || {};
  const totA = sum_.A?.total_cost_list_usd ?? sum(tasks.map((t) => t.A.cost_list_usd));
  const totB = sum_.B?.total_cost_list_usd ?? sum(tasks.map((t) => t.B.cost_list_usd));
  const meanA = sum_.A?.mean_score ?? mean(tasks.map((t) => t.A.score));
  const meanB = sum_.B?.mean_score ?? mean(tasks.map((t) => t.B.score));
  const r = sum_.cost_ratio_A_over_B ?? ratio(totA, totB);
  const counts = DIFF_ORDER.map((d) => [d, tasks.filter((t) => t.difficulty === d).length])
    .filter(([, n]) => n).map(([d, n]) => `${n} ${d}`).join(" · ");
  const metered = sum_.B?.total_cost_metered_usd;

  const s1 = section(`Earlier Claude Code A/B: ${tasks.length} tasks, run twice`,
    `Every task was run in arm <b>A</b> (${esc(labels.A)}) and in arm <b>B</b> (${esc(labels.B)}),
     then scored by the same judges and tests. ${method.summary ? esc(method.summary) : ""}`);
  s1.id = "ab";
  const tiles = document.createElement("div");
  tiles.className = "stat-row";
  tiles.innerHTML =
    stat(times(r), "lower list-price cost, A ÷ B", `${usd(totA)} against ${usd(totB)} for the same tasks`) +
    stat(`${fmt(meanA)} → ${fmt(meanB)}`, `mean score, A → B (${scaleNote})`,
      `difference ${meanB - meanA >= 0 ? "+" : ""}${scale === 1 ? ((meanB - meanA) * 100).toFixed(1) + " pt" : (meanB - meanA).toFixed(2)}`) +
    stat(String(tasks.length), "tasks, each run in both arms", counts) +
    stat(`${usd(totA)} / ${usd(totB)}`, "total at list price, A / B",
      metered !== undefined && metered !== null ? `B actually billed: ${usd(metered)}` : "list-price equivalents");
  s1.append(tiles);
  const cap = document.createElement("p");
  cap.className = "section-note";
  cap.textContent = `Costs are list-price equivalents: the tokens each run used (input, output, cache ` +
    `reads and cache writes) times public list prices, so a subscription run and a metered run are ` +
    `comparable. They are not invoices. n = ${tasks.length} tasks; with a sample this small a ` +
    `difference of a few tenths of a point in the mean score is within noise.`;
  s1.append(cap);
  const pw = sum_.pairwise || {};
  const tA = sum_.A || {}, tB = sum_.B || {};
  if (tA.tests_total || Object.keys(pw).length) {
    const q = document.createElement("p");
    q.className = "section-note";
    const judged = Object.entries(pw).map(([j, w]) =>
      `${esc(j)} judge preferred A in ${w.A}, B in ${w.B}, tie in ${w.tie} (a difference it called noticeable: ${w.A_noticeable} for A, ${w.B_noticeable} for B)`);
    q.innerHTML = `<strong>Quality.</strong> ` +
      (tA.tests_total ? `Hidden unit and browser tests passed: A ${tA.tests_passed}/${tA.tests_total}, B ${tB.tests_passed}/${tB.tests_total}. ` : "") +
      (judged.length ? `Blind side-by-side: ${judged.join("; ")}. ` : "") +
      (tB.quality_checks ? `The router graded ${tB.quality_checks} final answers from models below GPT-5.6 Terra, rejected ${tB.quality_rejections} and re-ran ${tB.escalations} on a stronger model.` : "");
    s1.append(q);
  }
  root.append(s1);

  /* arm C ---------------------------------------------------------------- */
  const c = sum_.C;
  if (c) {
    const sc = section("A third arm: the router without its answer check",
      `${esc(c.label)}. Same ${c.n_tasks} tasks, same Claude Code settings. It shows what the answer check costs and what it buys.`);
    sc.id = "arm-c";
    const tbl = (headers, rows) => {
      const wrap = document.createElement("div");
      wrap.className = "tbl-wrap";
      wrap.setAttribute("role", "region");
      wrap.setAttribute("aria-label", "Three arms compared");
      wrap.tabIndex = 0;
      wrap.innerHTML = `<table class="tbl"><thead><tr>${headers.map((h, i) =>
        `<th class="${i ? "num" : ""}">${esc(h)}</th>`).join("")}</tr></thead><tbody>${rows.map((r) =>
        `<tr>${r.map((v, i) => `<td class="${i ? "num" : ""}">${esc(v)}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
      return wrap;
    };
    sc.append(tbl(["arm", "list-price cost", "cost vs A", "mean score", "tests passed"], [
      ["A · always Opus 5.5", usd(totA), "1×", fmt(meanA), tA.tests_total ? `${tA.tests_passed}/${tA.tests_total}` : "—"],
      ["B · router, answer check on (shipped default)", usd(totB), times(ratio(totA, totB)) + " cheaper", fmt(meanB), tB.tests_total ? `${tB.tests_passed}/${tB.tests_total}` : "—"],
      ["C · router, answer check off", usd(c.total_cost_list_usd), times(c.cost_ratio_A_over_C) + " cheaper", fmt(c.mean_score), c.tests_total ? `${c.tests_passed}/${c.tests_total}` : "—"],
    ]));
    root.append(sc);
  }

  if (!tasks.length) {
    root.append(Object.assign(document.createElement("p"), { className: "muted", textContent: "No tasks in the study file." }));
  } else {
    /* scatter ----------------------------------------------------------- */
    const s2 = section("Where each task landed");
    s2.append(pairScatter({ tasks, labels, fmt, scale }));
    root.append(s2);

    /* by difficulty ----------------------------------------------------- */
    const diffs = [...new Set(tasks.map((t) => t.difficulty))].sort((a, b) => diffRank(a) - diffRank(b));
    const groups = diffs.map((d) => {
      const ts = tasks.filter((t) => t.difficulty === d);
      return { d, n: ts.length,
        scoreA: mean(ts.map((t) => t.A.score)), scoreB: mean(ts.map((t) => t.B.score)),
        costA: sum(ts.map((t) => t.A.cost_list_usd)), costB: sum(ts.map((t) => t.B.cost_list_usd)) };
    });
    const s3 = section("By difficulty",
      "Two measures, so two charts. The router is meant to save most on easy work and to spend on hard work.");
    const grid = document.createElement("div");
    grid.className = "chart-grid";
    grid.append(
      pairedBars({
        title: "Mean score", sub: `Higher is better (${scaleNote}).`, labels, format: fmt,
        groups: groups.map((g) => ({ label: `${g.d} · ${g.n} task${g.n === 1 ? "" : "s"}`, A: g.scoreA, B: g.scoreB })),
      }),
      pairedBars({
        title: "List-price cost", sub: "Lower is cheaper. Sum over the tasks in each group.", labels, format: usd,
        showRatio: true,
        groups: groups.map((g) => ({ label: `${g.d} · ${g.n} task${g.n === 1 ? "" : "s"}`, A: g.costA, B: g.costB,
          ratio: ratio(g.costA, g.costB) })),
      }),
    );
    s3.append(grid);
    s3.append(tableView(["difficulty", "tasks", "score A", "score B", "cost A", "cost B", "A ÷ B"],
      groups.map((g) => [g.d, g.n, fmt(g.scoreA), fmt(g.scoreB), usd(g.costA), usd(g.costB), times(ratio(g.costA, g.costB))])));
    root.append(s3);

    /* route mix --------------------------------------------------------- */
    const mix = new Map();
    for (const t of tasks) {
      for (const rt of t.B.routes || []) {
        const m = mix.get(rt.model) || { turns: 0, cost: 0, tasks: 0 };
        m.turns += rt.turns || 0; m.cost += rt.cost_list_usd || 0; m.tasks += 1;
        mix.set(rt.model, m);
      }
    }
    if (mix.size) {
      const total = sum([...mix.values()].map((m) => m.turns));
      const s4 = section("What the router picked in arm B",
        `Turns per model across all ${tasks.length} tasks. Arm A used ${esc(tasks[0].A.model || labels.A)} for every turn.`);
      s4.append(barChart({
        title: "Route mix, arm B",
        sub: `${total} routed turns. Hover for the cost each model accounted for.`,
        rows: [...mix.entries()].sort((a, b) => b[1].turns - a[1].turns).map(([model, m]) => ({
          label: model, value: m.turns,
          tip: `${m.turns} turns (${((m.turns / total) * 100).toFixed(0)} %) in ${m.tasks} task${m.tasks === 1 ? "" : "s"} · ${usd(m.cost)} at list price`,
        })),
        format: (v) => `${v} turn${v === 1 ? "" : "s"}`,
        color: COLOR.B,
      }));
      root.append(s4);
    }

    /* per task ---------------------------------------------------------- */
    const s5 = section("Every task", "Score and list-price cost per arm; for arm B, which models answered.");
    s5.append(taskTable(tasks, fmt));
    root.append(s5);
  }

  /* what if -------------------------------------------------------------- */
  if ((data.what_if || []).length) {
    const s6 = section("What if",
      "The same token counts re-priced under other assumptions. These are arithmetic on the " +
      "measured runs, not new runs.");
    s6.append(pairedBars({
      title: "Cost of the whole study under each scenario", sub: "Lower is cheaper. The label is A ÷ B.",
      labels, format: usd, showRatio: true,
      groups: data.what_if.map((w) => ({ label: w.scenario, A: w.A_cost, B: w.B_cost,
        ratio: w.ratio ?? ratio(w.A_cost, w.B_cost), note: w.description })),
    }));
    root.append(s6);
  }

  /* method --------------------------------------------------------------- */
  const s7 = section("Method and limitations");
  s7.id = "method";
  const dl = document.createElement("dl");
  dl.className = "kv method-kv";
  const pricing = method.pricing ? Object.entries(method.pricing).map(([k, v]) =>
    `${esc(k)}: ${esc(typeof v === "object" ? JSON.stringify(v) : v)}`).join("<br>") : "—";
  dl.innerHTML = `
    <dt>Arm A</dt><dd>${esc(labels.A)}</dd>
    <dt>Arm B</dt><dd>${esc(labels.B)}</dd>
    <dt>Judges</dt><dd>${(method.judges || []).map(esc).join(", ") || "—"}</dd>
    <dt>Pricing</dt><dd>${pricing}</dd>
    <dt>Generated</dt><dd>${esc(data.generated_at || "—")}</dd>`;
  s7.append(dl);
  const ul = document.createElement("ul");
  ul.className = "limits";
  ul.innerHTML = (method.limitations || []).map((l) => `<li>${esc(l)}</li>`).join("") ||
    "<li>No limitations were listed in the study file.</li>";
  s7.append(ul);
  const src = document.createElement("p");
  src.className = "credits";
  src.innerHTML = `Raw data: <a href="${sample ? "/data/ab-study.sample.json" : "/data/ab-study.json"}">${
    sample ? "ab-study.sample.json" : "ab-study.json"}</a>.`;
  s7.append(src);
  root.append(s7);

  root.append(simulation(replay));
}
