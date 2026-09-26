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
    "One week of one team's real coding-agent traffic was analyzed in a replay: " +
    "(1,638 sessions, 57,696 calls), re-priced as if each policy had routed it, using measured " +
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

  const s1 = section(`A/B study: ${tasks.length} tasks, run twice`,
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
  root.append(s1);

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
