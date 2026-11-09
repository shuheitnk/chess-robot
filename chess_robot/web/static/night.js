// 画面2：Night · Practice — 練習中は WebSocket で、終わったあとは night_log.jsonl の最後の記録で表示する
"use strict";
(() => {
  const $ = (id) => document.getElementById(id);
  const pct = (v) => (v === null || v === undefined ? "—" : Math.round(v * 100) + "%");
  let live = false;

  fetch("/api/night").then((r) => r.json()).then((d) => { if (!live) render(d); }).catch(() => render({ none: true }));
  KOMA.connect((m) => { if (m.type === "night") { live = true; render(m); } });

  function render(st) {
    if (!st || st.none || st.error) return renderEmpty(st && st.error);
    const done = st.phase === "done";
    const day = st.day;
    $("unit").textContent = day ? `Night practice · Day ${KOMA.pad(day)} → ${KOMA.pad(day + 1)}` : "Night practice";
    const phase = { starting: "Starting", baseline: "Measuring today’s skill", practicing: "Training in sim",
                    testing: "Testing on new tasks", done: done && st.adopted ? "Done · new skill adopted" : "Done · kept current skill" }[st.phase] || st.phase;
    const el = $("st-phase");
    el.textContent = phase;
    el.className = "dia" + (done ? " off" : "");
    const started = st.started ? new Date(st.started) : null;
    $("st-start").textContent = started ? `Started ${hhmm(started)}` : "—";

    const now = st.now ? new Date(st.now) : new Date();
    const mins = started ? Math.max(0, Math.round((now - started) / 60000)) : null;
    const dur = mins === null ? "—" : mins >= 60 ? `${Math.floor(mins / 60)}h ${mins % 60}m` : `${mins}m`;
    $("clock").textContent = done ? `— Finished ${hhmm(new Date(st.time || st.now))} · ${dur} of practice`
                                  : `— ${hhmm(now)} · ${dur} into practice`;
    $("lede").textContent = st.n_cases
      ? "I rebuild the moments that went wrong in today’s games inside the simulation, and refine how I grip and carry each piece. A new skill replaces tomorrow’s only if it does better."
      : "No moves went wrong in our recent games, so tonight I’m practicing on random boards. A new skill replaces tomorrow’s only if it does better.";
    $("h1").innerHTML = done ? "While you slept,<br>I practiced today’s mistakes." : "While you sleep,<br>I’m practicing today’s mistakes.";
    const v = $("verdict");
    v.hidden = !(done && st.message);
    v.textContent = st.message || "";

    drawRing(st);
    drawGens(st);
    drawChart(st);
    drawStats(st);
    drawCases(st);
    drawParams(st);
  }

  function renderEmpty(err) {
    $("clock").textContent = "— Not yet";
    $("lede").textContent = err ? "I couldn’t read last night’s record." :
      "I haven’t practiced at night yet. Run scripts/night_practice.py before bed and I’ll rebuild today’s mistakes in the simulation.";
    $("st-phase").textContent = "Idle"; $("st-phase").className = "dia off";
    drawRing({}); drawGens({ gens: 15, gen: 0 }); drawChart({}); drawStats({}); drawCases({ cases: [] }); drawParams({});
  }

  const hhmm = (d) => d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

  function arc(cx, cy, r, frac) {
    if (frac <= 0) return "";
    if (frac >= 0.9999) return `M${cx} ${cy - r}A${r} ${r} 0 1 1 ${cx - 0.01} ${cy - r}`;
    const a = frac * 2 * Math.PI;
    return `M${cx} ${cy - r}A${r} ${r} 0 ${frac > 0.5 ? 1 : 0} 1 ${(cx + r * Math.sin(a)).toFixed(2)} ${(cy - r * Math.cos(a)).toFixed(2)}`;
  }

  function drawRing(st) {
    const cur = st.current ? st.current.sr : null, bef = st.before ? st.before.sr : null;
    $("ring").innerHTML = `
      <circle cx="110" cy="110" r="104" fill="none" stroke="#CFCAC0" stroke-width="1"/>
      <circle cx="110" cy="110" r="97" fill="none" stroke="#1F6FA8" stroke-width="1" stroke-dasharray="0.6 4" stroke-linecap="round"/>
      <path d="M110 0v12M110 208v12M0 110h12M208 110h12" stroke="#1C2025" stroke-width="1"/>
      <circle cx="110" cy="110" r="86" fill="none" stroke="#E2DFD8" stroke-width="7"/>
      <path d="${arc(110, 110, 86, cur ?? 0)}" fill="none" stroke="#1F6FA8" stroke-width="7"/>
      <circle cx="110" cy="110" r="74" fill="none" stroke="#E2DFD8" stroke-width="2"/>
      <path d="${arc(110, 110, 74, bef ?? 0)}" fill="none" stroke="#9AA6B2" stroke-width="2"/>
      <circle cx="110" cy="110" r="58" fill="none" stroke="#1C2025" stroke-width="1"/>
      <text x="110" y="88" text-anchor="middle" font-family="JetBrains Mono" font-size="7.5" letter-spacing="1.2" fill="#5A616B">SUCCESS RATE</text>
      <text x="110" y="128" text-anchor="middle" font-family="Space Grotesk" font-weight="300" font-size="40" fill="#1C2025">${pct(cur)}</text>
      <text x="110" y="146" text-anchor="middle" font-family="Inter" font-size="8" fill="#5A616B">Before practice ${pct(bef)}</text>
      <text x="110" y="157" text-anchor="middle" font-family="Inter" font-size="7" fill="#5A616B">(inner arc)</text>`;
  }

  function drawGens(st) {
    const n = st.gens || 15, g = st.gen || 0, done = st.phase === "done" || st.phase === "testing";
    const box = $("gens");
    box.style.setProperty("--n", n);
    box.innerHTML = Array.from({ length: n }, (_, i) => {
      const cls = i < g - (done ? 0 : 1) ? "done" : i === g - 1 && !done ? "now" : "";
      return `<span class="${cls}">${KOMA.pad(i + 1)}</span>`;
    }).join("");
    $("gen-lbl").textContent = st.gens
      ? `Generation ${KOMA.pad(g)} / ${KOMA.pad(n)} · ${st.popsize} candidates · ${st.n_cases} of our cases + ${st.n_random} random boards`
      : "—";
    const p = st.progress;
    $("prog").textContent = p && p.total && st.phase !== "done"
      ? `${{ baseline: "Measuring today’s skill", practicing: "Solving tasks", testing: "Testing on new tasks" }[st.phase] || ""} · ${p.done} / ${p.total}`
      : "";
  }

  function drawChart(st) {
    const W = 520, H = 170, L = 34, R = 10, T = 10, Bm = 24;
    const pts = st.chart || [];
    const n = st.gens || 15;
    const vals = pts.map((p) => p.sr).concat(st.before ? [st.before.sr] : []);
    const lo = vals.length ? Math.max(0, Math.floor((Math.min(...vals) * 100 - 5) / 20) * 20) : 0;
    const y = (v) => T + (H - T - Bm) * (1 - (v * 100 - lo) / (100 - lo || 1));
    const x = (i) => L + (W - L - R) * (i / Math.max(n, 1));
    let g = "";
    for (let v = lo; v <= 100; v += 20) {
      g += `<path d="M${L} ${y(v / 100).toFixed(1)}H${W - R}" stroke="#E2DFD8" stroke-width="1"/>
            <text x="${L - 8}" y="${(y(v / 100) + 3).toFixed(1)}" text-anchor="end" font-family="JetBrains Mono" font-size="9" fill="#5A616B">${v}</text>`;
    }
    g += `<text x="${L}" y="${H - 6}" font-family="JetBrains Mono" font-size="9" fill="#5A616B">00</text>
          <text x="${W - R}" y="${H - 6}" text-anchor="end" font-family="JetBrains Mono" font-size="9" fill="#5A616B">${KOMA.pad(n)}</text>`;
    let line = "";
    if (st.before) {
      const all = [{ gen: 0, sr: st.before.sr }].concat(pts);
      line = `<path d="${all.map((p, i) => `${i ? "L" : "M"}${x(p.gen).toFixed(1)} ${y(p.sr).toFixed(1)}`).join("")}" fill="none" stroke="#1F6FA8" stroke-width="1.6"/>`;
      const last = all[all.length - 1];
      const lx = x(last.gen), ly = y(last.sr);
      line += `<rect x="${lx - 4}" y="${ly - 4}" width="8" height="8" fill="#1F6FA8" transform="rotate(45 ${lx} ${ly})"/>
               <text x="${Math.min(lx + 10, W - 80)}" y="${ly + (ly < 30 ? 16 : -8)}" font-family="JetBrains Mono" font-size="9" fill="#1C2025">GEN ${KOMA.pad(last.gen)} · ${pct(last.sr)}</text>`;
    }
    $("chart").innerHTML = g + line;
  }

  function drawStats(st) {
    const c = st.current || {}, b = st.before || {};
    const item = (code, t, v, bv) => `<div><div class="lbl">${code}</div><div class="t">${t}</div><div class="v">${v}</div><div class="b">Before: ${bv}</div></div>`;
    $("stats").innerHTML =
      item("SR", "Success rate", pct(c.sr), pct(b.sr)) +
      item("ERR", "Placement error", `${KOMA.fmt(c.err_mm, 1)} mm`, `${KOMA.fmt(b.err_mm, 1)} mm`) +
      item("FALL", "Pieces toppled", c.falls ?? "—", b.falls ?? "—") +
      item("T", "Time per move", `${KOMA.fmt(c.time_s, 1)} s`, `${KOMA.fmt(b.time_s, 1)} s`);
  }

  function miniBoard(b) {
    const komaWhite = b.koma_white !== false;
    const V = KOMA.view(komaWhite);          // あなたの駒が手前。KOMA の駒は青
    let s = "";
    for (let r = 0; r < 8; r++) for (let c = 0; c < 8; c++) {
      const sq = V.files[c] + V.ranks[r], fi = "abcdefgh".indexOf(sq[0]), ri = Number(sq[1]) - 1;
      const dark = (fi + ri) % 2 === 0;
      const hl = sq === b.frm || sq === b.to;
      s += `<rect x="${c * 5.5}" y="${r * 5.5}" width="5.5" height="5.5" fill="${hl ? "#C5DCEC" : dark ? "#E3DED4" : "#F6F4EF"}"/>`;
      const ch = b.squares[sq];
      if (ch) s += `<circle cx="${c * 5.5 + 2.75}" cy="${r * 5.5 + 2.75}" r="1.6" fill="${(ch === ch.toUpperCase()) === komaWhite ? "#1F6FA8" : "#1C2025"}"/>`;
    }
    return `<svg viewBox="0 0 44 44" aria-hidden="true">${s}<rect x="0.25" y="0.25" width="43.5" height="43.5" fill="none" stroke="#CFCAC0" stroke-width="0.5"/></svg>`;
  }

  function drawCases(st) {
    const cs = st.cases || [];
    $("cases-lbl").textContent = cs.length ? `${st.n_cases ?? cs.length} from our games${st.n_queued ? ` · ${st.n_queued} waiting` : ""}` : "None recorded";
    $("cases").innerHTML = cs.length ? cs.map((c) => `
      <div class="case">${miniBoard(c.board)}
        <div><div class="ti">${KOMA.esc(c.title)}</div><div class="de">${KOMA.esc(c.desc)}</div></div>
        <span class="tag ${c.status}">${c.status}</span></div>`).join("")
      : `<p class="empty">No mistakes recorded in our games yet. When I knock over or nudge a piece, the moment is saved here for practice.</p>`;
  }

  function drawParams(st) {
    const ps = st.params || [];
    $("best-lbl").textContent = st.phase === "done" ? (st.adopted ? "Adopted for tomorrow" : "Not adopted") : "So far tonight";
    $("params").innerHTML = ps.length ? ps.map((p) => {
      const d = Math.abs(p.delta) < Math.pow(10, -p.digits) / 2 ? "±0" : KOMA.fmt(p.delta, p.digits, true);
      return `<div class="param"><div class="l">${KOMA.esc(p.label)}</div><div class="row"><span class="v">${KOMA.fmt(p.value, p.digits)} ${p.unit}</span><span class="d">${d}</span></div></div>`;
    }).join("") : `<p class="empty">—</p>`;
  }
})();
