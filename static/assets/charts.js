/* Results page: charts built from the measured run, no chart library.
   Forms follow the job: magnitude -> bars, two measures -> two charts (never a
   second y-axis), relationship -> a scatter, change over an ordered gap -> lines.
   Every chart has a hover layer and a table view underneath it. */

const tip = Object.assign(document.createElement("div"), { className: "tip" });
document.body.append(tip);

function showTip(event, html) {
  tip.innerHTML = html;
  tip.classList.add("on");
  const pad = 14;
  const x = Math.min(event.clientX + pad, innerWidth - tip.offsetWidth - 8);
  const y = Math.max(8, event.clientY - tip.offsetHeight - pad);
  tip.style.left = x + "px";
  tip.style.top = y + "px";
}
const hideTip = () => tip.classList.remove("on");
addEventListener("scroll", hideTip, { passive: true });

const esc = (s) => String(s).replace(/[&<>"]/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const money = (v) => v >= 1000 ? "$" + Math.round(v).toLocaleString("en-US")
  : v >= 1 ? "$" + v.toFixed(2) : "$" + v.toFixed(v < 0.01 ? 4 : 3);
const pct1 = (v) => (v * 100).toFixed(1) + " %";

function section(title, sub) {
  const wrap = document.createElement("section");
  wrap.className = "section";
  wrap.innerHTML = `<div class="section-head"><h2>${esc(title)}</h2>${sub ? `<p>${sub}</p>` : ""}</div>`;
  return wrap;
}

/** Horizontal bars, one series. Values are direct-labelled; the axis is implied. */
function barChart({ title, sub, rows, format, highlight, color }) {
  const max = Math.max(...rows.map((r) => r.value), 0) || 1;
  const figure = document.createElement("figure");
  figure.className = "chart";
  figure.innerHTML = `<h3>${esc(title)}</h3>${sub ? `<p class="sub">${sub}</p>` : ""}
    <div class="bars"></div>`;
  const host = figure.querySelector(".bars");
  rows.forEach((row) => {
    const share = Math.max(0.004, row.value / max);
    const div = document.createElement("div");
    div.className = "bar-row";
    const isPick = highlight && highlight(row);
    div.innerHTML = `
      <span class="lbl ${isPick ? "strong" : ""}">${esc(row.label)}</span>
      <span class="bar-track">
        <span class="bar-fill ${isPick ? "pick" : ""}" style="width:${share * 100}%;${color ? `background:${color}` : ""}"></span>
      </span>
      <span class="bar-val">${format(row.value)}</span>`;
    const target = div.querySelector(".bar-track");
    target.addEventListener("pointermove", (e) =>
      showTip(e, `<b>${esc(row.label)}</b><span class="t-sub">${row.tip || format(row.value)}</span>`));
    target.addEventListener("pointerleave", hideTip);
    host.append(div);
  });
  return figure;
}

function tableView(headers, rows) {
  const details = document.createElement("details");
  details.className = "table-view";
  details.innerHTML = `<summary>Table view</summary>
    <table class="tbl"><thead><tr>${headers.map((h, i) =>
      `<th class="${i ? "num" : ""}">${esc(h)}</th>`).join("")}</tr></thead>
    <tbody>${rows.map((r) => `<tr>${r.map((c, i) =>
      `<td class="${i ? "num" : ""}">${esc(c)}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
  return details;
}

/** A dot plot. The right form when every value sits in a narrow band near the
    top of the scale: a bar chart would have to start at zero and then all the
    bars look identical, which hides the only thing worth seeing. */
function dotPlot({ title, sub, rows, min, max, format, highlight, color }) {
  const rowH = 30, L = 232, R = 62, T = 12;
  const H = T + rows.length * rowH + 40;
  const W = 620;
  const px = (v) => L + ((v - min) / (max - min)) * (W - L - R);
  const ticks = [];
  const step = (max - min) / 4;
  for (let i = 0; i <= 4; i += 1) ticks.push(min + i * step);
  const figure = document.createElement("figure");
  figure.className = "chart";
  figure.innerHTML = `<h3>${esc(title)}</h3>${sub ? `<p class="sub">${sub}</p>` : ""}
    <svg class="svg-chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(title)}">
      ${ticks.map((t) => `<line class="grid-line" x1="${px(t)}" x2="${px(t)}" y1="${T}" y2="${T + rows.length * rowH}"/>
        <text x="${px(t)}" y="${T + rows.length * rowH + 18}" text-anchor="middle">${format(t)}</text>`).join("")}
      ${rows.map((r, i) => {
        const y = T + i * rowH + rowH / 2;
        const pick = highlight && highlight(r);
        return `<g class="pt" data-tip="${esc(`<b>${r.label}</b><span class="t-sub">${r.tip || format(r.value)}</span>`)}">
          <rect x="0" y="${y - rowH / 2}" width="${W}" height="${rowH}" fill="transparent"/>
          <text x="${L - 12}" y="${y + 4}" text-anchor="end" class="${pick ? "lbl-strong" : ""}">${
            esc(r.label.length > 33 ? r.label.slice(0, 32) + "…" : r.label)}</text>
          <line class="axis-line" x1="${L}" x2="${px(r.value)}" y1="${y}" y2="${y}" stroke-dasharray="2 3"/>
          <circle cx="${px(r.value)}" cy="${y}" r="${pick ? 7 : 5.5}" fill="${color || "var(--s3)"}"
                  stroke="var(--surface)" stroke-width="2"/>
          <text x="${px(r.value) + 12}" y="${y + 4}" class="lbl-strong">${format(r.value)}</text>
        </g>`;
      }).join("")}
    </svg>
    <p class="sub" style="margin:.6rem 0 0">Axis starts at ${format(min)}, not at zero — every
      policy solves most turns, and the differences are what matters.</p>`;
  figure.querySelectorAll(".pt").forEach((node) => {
    node.addEventListener("pointermove", (e) => showTip(e, node.dataset.tip));
    node.addEventListener("pointerleave", hideTip);
  });
  return figure;
}

/* -------------------------------------------------------------- scatter */
function scatterChart({ title, sub, points, xLabel, yLabel }) {
  const W = 680, H = 370, L = 58, R = 26, T = 16, B = 48;
  const xs = points.map((p) => p.x);
  const lo = Math.log10(Math.min(...xs) * 0.7), hi = Math.log10(Math.max(...xs) * 1.4);
  const yMin = Math.min(...points.map((p) => p.y)) - 4;
  const yMax = Math.max(...points.map((p) => p.y)) + 3;
  const px = (v) => L + ((Math.log10(v) - lo) / (hi - lo)) * (W - L - R);
  const py = (v) => H - B - ((v - yMin) / (yMax - yMin)) * (H - T - B);
  const ticks = [0.1, 0.25, 0.5, 1, 2.5, 5, 10].filter((t) => t >= 10 ** lo && t <= 10 ** hi);
  const yTicks = [];
  for (let v = Math.ceil(yMin / 5) * 5; v <= yMax; v += 5) yTicks.push(v);

  const figure = document.createElement("figure");
  figure.className = "chart";
  figure.innerHTML = `<h3>${esc(title)}</h3>${sub ? `<p class="sub">${sub}</p>` : ""}
    <svg class="svg-chart" viewBox="0 0 ${W} ${H}" role="img"
         aria-label="${esc(title)}. ${esc(sub || "")}">
      ${yTicks.map((v) => `<line class="grid-line" x1="${L}" x2="${W - R}" y1="${py(v)}" y2="${py(v)}"/>
        <text x="${L - 8}" y="${py(v) + 4}" text-anchor="end">${v}</text>`).join("")}
      <line class="axis-line" x1="${L}" x2="${W - R}" y1="${H - B}" y2="${H - B}"/>
      ${ticks.map((t) => `<text x="${px(t)}" y="${H - B + 18}" text-anchor="middle">${money(t)}</text>`).join("")}
      <text x="${W / 2}" y="${H - 6}" text-anchor="middle" class="lbl-strong">${esc(xLabel)}</text>
      <text x="18" y="${(H - B) / 2}" text-anchor="middle" class="lbl-strong"
            transform="rotate(-90 18 ${(H - B) / 2})">${esc(yLabel)}</text>
      ${points.map((p) => `
        <g class="pt" data-tip="${esc(p.tip)}">
          <circle cx="${px(p.x)}" cy="${py(p.y)}" r="7" fill="var(--s1)" stroke="var(--surface)" stroke-width="2"/>
          <text x="${px(p.x) + (p.right === false ? -12 : 12)}" y="${py(p.y) + 4 + (p.dy || 0)}"
                text-anchor="${p.right === false ? "end" : "start"}" class="lbl-strong">${esc(p.label)}</text>
        </g>`).join("")}
    </svg>`;
  figure.querySelectorAll(".pt").forEach((node) => {
    node.addEventListener("pointermove", (e) => showTip(e, node.dataset.tip));
    node.addEventListener("pointerleave", hideTip);
  });
  return figure;
}

/* ----------------------------------------------------------------- lines */
function lineChart({ title, sub, buckets, series, yFormat }) {
  const W = 620, H = 300, L = 48, R = 108, T = 14, B = 40;
  const px = (i) => L + (i / (buckets.length - 1)) * (W - L - R);
  const py = (v) => H - B - v * (H - T - B);
  const yTicks = [0, 0.25, 0.5, 0.75, 1];
  const colors = ["var(--s1)", "var(--s2)", "var(--s3)"];
  const figure = document.createElement("figure");
  figure.className = "chart";
  figure.innerHTML = `<h3>${esc(title)}</h3>${sub ? `<p class="sub">${sub}</p>` : ""}
    <div class="legend">${series.map((s, i) =>
      `<span><i style="background:${colors[i]}"></i>${esc(s.label)}</span>`).join("")}</div>
    <svg class="svg-chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(title)}">
      ${yTicks.map((v) => `<line class="grid-line" x1="${L}" x2="${W - R}" y1="${py(v)}" y2="${py(v)}"/>
        <text x="${L - 8}" y="${py(v) + 4}" text-anchor="end">${yFormat(v)}</text>`).join("")}
      <line class="axis-line" x1="${L}" x2="${W - R}" y1="${H - B}" y2="${H - B}"/>
      ${buckets.map((b, i) =>
        `<text x="${px(i)}" y="${H - B + 18}" text-anchor="middle">${esc(b)}</text>`).join("")}
      ${series.map((s, si) => `
        <path d="${s.values.map((v, i) => `${i ? "L" : "M"}${px(i)},${py(v)}`).join(" ")}"
              fill="none" stroke="${colors[si]}" stroke-width="2" stroke-linejoin="round"/>
        ${s.values.map((v, i) => `<circle cx="${px(i)}" cy="${py(v)}" r="4.5" fill="${colors[si]}"
              stroke="var(--surface)" stroke-width="2"/>`).join("")}
        <text x="${px(s.values.length - 1) + 10}" y="${py(s.values[s.values.length - 1]) + 4}"
              class="lbl-strong">${esc(s.short)}</text>`).join("")}
      ${buckets.map((b, i) => `<rect class="hit" x="${px(i) - 24}" y="${T}" width="48" height="${H - T - B}"
            fill="transparent" data-tip="${esc(`<b>${b}</b>` + series.map((s) =>
              `<span class="t-sub">${s.label}: ${yFormat(s.values[i])}</span>`).join("<br>"))}"/>`).join("")}
    </svg>`;
  figure.querySelectorAll(".hit").forEach((node) => {
    node.addEventListener("pointermove", (e) => showTip(e, node.dataset.tip));
    node.addEventListener("pointerleave", hideTip);
  });
  return figure;
}

/* ------------------------------------------------------------------ page */
export function renderResults(root, data) {
  root.replaceChildren();

  /* headline numbers */
  const listRows = data.replay_list.rows;
  const best = listRows.find((r) => r.key === "F_expected");
  const baseline = listRows.find((r) => r.key === "A_static");
  const stats = document.createElement("div");
  stats.className = "stat-row";
  stats.innerHTML = `
    <div class="stat"><span class="v">${(baseline.usd_per_solved / best.usd_per_solved).toFixed(1)}×</span>
      <span class="k">cheaper per solved turn</span>
      <div class="s">than sending every turn to the frontier model, in the replay</div></div>
    <div class="stat"><span class="v">${((baseline.success - best.success) * 100).toFixed(1)} pt</span>
      <span class="k">of task success given up</span>
      <div class="s">${pct1(best.success)} versus ${pct1(baseline.success)}</div></div>
    <div class="stat"><span class="v">78</span><span class="k">graded tasks</span>
      <div class="s">five categories, three difficulty levels, 8 models</div></div>
    <div class="stat"><span class="v">0.65 s</span><span class="k">to classify a turn</span>
      <div class="s">one Jev call on a scrubbed summary</div></div>`;
  root.append(stats);

  /* 1. policies ---------------------------------------------------------- */
  const s1 = section("Which routing rule wins",
    esc(data.replay_list.about) +
    " Two measures, so two charts: nothing here is plotted against a second axis.");
  const grid1 = document.createElement("div");
  grid1.className = "chart-grid";
  grid1.append(
    barChart({
      title: "Cost per solved turn",
      sub: "Lower is better. Public list prices, no subscription.",
      rows: listRows.filter((r) => r.usd_per_solved !== null).map((r) => ({
        label: r.label, value: r.usd_per_solved,
        tip: `${money(r.usd_week)} for the week · ${pct1(r.success)} of turns solved`,
      })),
      format: money,
      highlight: (r) => /expected cost/.test(r.label),
    }),
    dotPlot({
      title: "Share of turns solved",
      sub: "Higher is better. Same replay, same tasks.",
      min: 0.85, max: 0.95,
      rows: listRows.map((r) => ({
        label: r.label.replace(/^[A-F] — /, "").replace(/ \(the router's default\)/, "")
          .replace("F tuned for higher stakes (10x turn cost)", "the same rule, higher stakes"),
        value: r.success,
        tip: `${pct1(r.success)} of turns solved · ${money(r.usd_week)} for the week`,
      })),
      format: pct1,
      highlight: (r) => /minimise expected cost/.test(r.label),
    }),
  );
  s1.append(grid1);
  s1.append(tableView(["policy", "turns solved", "cost / week", "cost / solved turn"],
    listRows.map((r) => [r.label, pct1(r.success), money(r.usd_week),
      r.usd_per_solved === null ? "—" : money(r.usd_per_solved)])));
  const note = document.createElement("p");
  note.className = "credits";
  note.style.marginTop = ".9rem";
  note.innerHTML = "The rule that starts cheap and escalates on failure — the obvious one — " +
    "loses to pricing failure up front: every hard turn goes through two paid attempts instead of one.";
  s1.append(note);
  root.append(s1);

  /* 2. our own setup ----------------------------------------------------- */
  const s2 = section("What it does to a real team's bill",
    esc(data.replay_ours.about));
  const oursGrid = document.createElement("div");
  oursGrid.className = "chart-grid";
  oursGrid.append(
    barChart({
      title: "Flat-rate plan used per week",
      sub: "Percent of the weekly quota the simulated week would consume.",
      rows: data.replay_ours.rows.map((r) => ({
        label: r.label, value: r.quota_pct_week,
        tip: `${r.quota_pct_week} % of the weekly quota · ${pct1(r.success)} of turns solved`,
      })),
      format: (v) => v.toFixed(0) + " %",
      highlight: (r) => /one cheap metered/.test(r.label),
    }),
    barChart({
      title: "API spend per week",
      sub: "Dollars actually billed by metered providers in the same week.",
      rows: data.replay_ours.rows.map((r) => ({
        label: r.label, value: r.usd_week,
        tip: `${money(r.usd_week)} a week · ${pct1(r.success)} of turns solved`,
      })),
      format: money,
      highlight: (r) => /one cheap metered/.test(r.label),
    }),
  );
  s2.append(oursGrid);
  s2.append(tableView(["setup", "turns solved", "API spend / week", "plan quota / week"],
    data.replay_ours.rows.map((r) => [r.label, pct1(r.success), money(r.usd_week),
      r.quota_pct_week + " %"])));
  root.append(s2);

  /* 3. price vs quality -------------------------------------------------- */
  const qm = data.quality_matrix;
  const s3 = section("Price barely predicts quality", esc(qm.about));
  s3.append(scatterChart({
    title: "What the whole 78-task run cost, against how many it solved",
    sub: "One point per model. The x-axis is logarithmic.",
    xLabel: "list-price cost of the run (log scale)",
    yLabel: "tasks solved of 78",
    points: qm.models.map((label, i) => ({
      label, x: qm.run_cost_usd[i], y: qm.solved[i],
      right: !qm.run_cost_usd.some((x, j) => j !== i && x > qm.run_cost_usd[i]
                                     && x < qm.run_cost_usd[i] * 3.2),
      dy: qm.solved.filter((v, j) => v === qm.solved[i] && j < i).length * 15,
      tip: `<b>${esc(label)}</b><span class="t-sub">${qm.solved[i]} of 78 solved · ` +
        `${money(qm.run_cost_usd[i])} for the run${qm.cost_estimated[i] ? " (estimated from tokens, it ran on a free tier)" : ""}</span>`,
    })),
  }));
  s3.append(barChart({
    title: "Tasks solved, by model",
    sub: "Out of 78. The differences live in hard coding, hard agent loops and hard math.",
    rows: qm.models.map((label, i) => ({
      label, value: qm.solved[i],
      tip: `${qm.solved[i]} of 78 · ${money(qm.run_cost_usd[i])} for the run`,
    })).sort((a, b) => b.value - a.value),
    format: (v) => v + " / 78",
  }));
  s3.append(tableView(["model", "solved of 78", "cost of the run"],
    qm.models.map((label, i) => [label, qm.solved[i],
      money(qm.run_cost_usd[i]) + (qm.cost_estimated[i] ? " (estimated)" : "")])));
  const qnote = document.createElement("p");
  qnote.className = "credits";
  qnote.style.marginTop = ".9rem";
  qnote.textContent = qm.note;
  s3.append(qnote);
  root.append(s3);

  /* 4. cache ------------------------------------------------------------- */
  const s4 = section("Caching is a property of the route, not the model",
    esc(data.cache.about));
  const cacheGrid = document.createElement("div");
  cacheGrid.className = "chart-grid";
  cacheGrid.append(
    barChart({
      title: "Prefix-cache read share, by route",
      sub: "The same kind of model on a different host reads back nothing.",
      rows: data.cache.routes.map((r) => ({
        label: r.route,
        value: (r.read_share[0] + r.read_share[1]) / 2,
        tip: r.note ? `${pct1(r.read_share[0])} — ${r.note}` : pct1(r.read_share[0]),
      })),
      format: (v) => (v * 100).toFixed(0) + " %",
    }),
    lineChart({
      title: "Read share by the gap since the previous call",
      sub: data.cache.by_gap.about,
      buckets: data.cache.by_gap.buckets,
      yFormat: (v) => (v * 100).toFixed(0) + " %",
      series: data.cache.by_gap.series.map((s) => ({
        label: s.client, short: s.client.startsWith("a coding") ? "flat-rate plan" : "free tier",
        values: s.values,
      })),
    }),
  );
  s4.append(cacheGrid);
  root.append(s4);

  /* 5. the classifier ---------------------------------------------------- */
  const s5 = section("How good the classifier is", esc(data.jev.about));
  const jstats = document.createElement("div");
  jstats.className = "stat-row";
  jstats.innerHTML = `
    <div class="stat"><span class="v">78 / 78</span><span class="k">topics named correctly</span>
      <div class="s">${esc(data.jev.category_accuracy)}</div></div>
    <div class="stat"><span class="v">r = ${data.jev.difficulty_r}</span><span class="k">difficulty against the task's level</span>
      <div class="s">${esc(data.jev.calibration)}</div></div>
    <div class="stat"><span class="v">0.96 / 0.99</span><span class="k">as a judge, coding / math</span>
      <div class="s">and useless on questions about a document it cannot see</div></div>`;
  s5.append(jstats);
  s5.append(barChart({
    title: "Does the difficulty score predict failure?",
    sub: esc(data.jev.auc_about) + " 0.5 would be a coin flip.",
    rows: data.jev.auc.map((r) => ({ label: r.model, value: r.auc, tip: `AUC ${r.auc}` })),
    format: (v) => v.toFixed(2),
  }));
  root.append(s5);

  /* 6. live -------------------------------------------------------------- */
  const s6 = section("The router, run for real", esc(data.live.about));
  s6.append(barChart({
    title: "Cost per solved task in the live run",
    sub: "Three policies, 30 tasks each, against real providers. At this size they cannot be ranked.",
    rows: data.live.rows.map((r) => ({
      label: r.policy, value: r.usd_per_solved,
      tip: `${r.solved} of ${r.tasks} solved · ${money(r.usd)} total · ${r.calls} calls · ${r.switches} model switches`,
    })),
    format: (v) => "$" + v.toFixed(4),
    highlight: (r) => /minimise expected cost/.test(r.label),
  }));
  s6.append(tableView(["policy", "solved", "total cost", "calls", "mid-turn switches"],
    data.live.rows.map((r) => [r.policy, `${r.solved} / ${r.tasks}`, money(r.usd), r.calls, r.switches])));
  root.append(s6);

  /* 7. limits ------------------------------------------------------------ */
  const s7 = section("What these numbers are not");
  const ul = document.createElement("ul");
  ul.className = "limits";
  ul.innerHTML = data.limits.map((l) => `<li>${esc(l)}</li>`).join("");
  s7.append(ul);
  const src = document.createElement("p");
  src.className = "credits";
  src.innerHTML = `Method, raw ledgers and the code that produced all of this: ` +
    `<a href="${esc(data.sources.experiments)}" rel="noopener">EXPERIMENTS.md</a> in the ` +
    `<a href="${esc(data.sources.repo)}" rel="noopener">router repository</a>. Measured ${esc(data.sources.measured_on)}.`;
  s7.append(src);
  root.append(s7);
}
