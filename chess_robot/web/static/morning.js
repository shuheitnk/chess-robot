// 画面3：Morning · Growth report — /api/morning（対局・夜の練習・頭脳の記録から作った数字）を表示する
"use strict";
(() => {
  const $ = (id) => document.getElementById(id);
  const pct = (v) => (v === null || v === undefined ? "—" : Math.round(v * 100) + "%");
  const sgn = (v, d = 1) => KOMA.fmt(v, d, true);

  fetch("/api/morning").then((r) => r.json()).then(render).catch(() => render({ error: true }));

  function render(d) {
    if (d.error) { $("body").textContent = "I couldn’t read my records."; return; }
    const now = d.now ? new Date(d.now) : new Date();
    const date = now.toLocaleDateString("en-GB", { weekday: "short", day: "2-digit", month: "short", year: "numeric" }).toUpperCase();
    const time = now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    $("unit").textContent = `Morning report · Day ${KOMA.pad(d.day)}`;
    $("date").textContent = `${date} · ${time}`;
    $("p-date").textContent = `${date.replace(/ \d{4}$/, "")} · ${time}`;

    const [g1, ...rest] = d.headline.split(/(?<=\.)\s/);
    $("head").innerHTML = `${KOMA.esc(g1)}<br>${KOMA.esc(rest.join(" "))}`;
    $("p-head").innerHTML = $("head").innerHTML;
    $("body").textContent = d.body;
    $("p-body").textContent = d.body;

    // ---- 数字4つ
    const yest = `${d.games_yesterday >= 0 ? "+" : ""}${d.games_yesterday} yesterday`;
    const wk = d.reward_week_ago === null ? "No games a week ago" : `${sgn(d.reward_week_ago)} a week ago`;
    const sk = d.skill || {};
    const skSub = sk.source === "night"
      ? (sk.adopted ? `${pct(sk.before)} → ${pct(sk.after)} on last night’s test` : `Kept after last night’s test`)
      : (sk.sr === null || sk.sr === undefined ? "No test yet" : "From the pretraining test");
    const sp = d.speed || {};
    const spSub = sp.delta === null || sp.delta === undefined ? (sk.source === "night" ? "Unchanged last night" : "From the pretraining test")
      : `${sgn(sp.delta)} s vs before practice`;
    const k = (code, t, v, s) => `<div><div class="lbl">${code}</div><div class="t">${t}</div><div class="v">${v}</div><div class="s">${s}</div></div>`;
    $("kpis").innerHTML =
      k("Games", "Games played", d.games ?? "—", yest) +
      k("Reward · last 10", "Average reward, last 10 games", sgn(d.reward_last10), wk) +
      k("Skill", "Move success rate", pct(sk.sr), skSub) +
      k("Speed", "Time per move", sp.time_s === null || sp.time_s === undefined ? "—" : `${KOMA.fmt(sp.time_s, 1)}s`, spSub);
    const ps = (code, v, s) => `<div><div class="lbl">${code}</div><div class="v">${v}</div><div class="s">${s}</div></div>`;
    $("p-stats").innerHTML =
      ps("Reward", sgn(d.reward_last10), d.reward_week_ago === null ? "Last week —" : `Last week ${sgn(d.reward_week_ago)}`) +
      ps("Skill", pct(sk.sr), sk.source === "night" ? `Before ${pct(sk.before)}` : "Pretraining") +
      ps("Games", d.games ?? "—", yest);
    $("p-foot").textContent = d.synced ? "Brain synced · GitHub" : "Brain on this PC";
    drawPhoneRing(d.day);

    drawChart(d.daily || []);
    drawValues(d.values || [], d.old_label);
    drawLesson(d.lesson);
    drawTimeline(d.timeline || []);
  }

  function drawPhoneRing(day) {
    $("p-ring").innerHTML = `
      <circle cx="120" cy="120" r="108" fill="none" stroke="#CFCAC0" stroke-width="1"/>
      <circle cx="120" cy="120" r="98" fill="none" stroke="#1F6FA8" stroke-width="1.2" stroke-dasharray="0.6 5" stroke-linecap="round"/>
      <path d="M120 4v14M120 222v14M4 120h14M222 120h14" stroke="#1C2025" stroke-width="1"/>
      <path d="M120 30A90 90 0 0 1 197 76" fill="none" stroke="#1F6FA8" stroke-width="3"/>
      <path d="M205 140A90 90 0 0 1 154 203" fill="none" stroke="#1F6FA8" stroke-width="3"/>
      <path d="M48 168A90 90 0 0 1 37 90" fill="none" stroke="#9AA6B2" stroke-width="1.2"/>
      <circle cx="120" cy="120" r="66" fill="none" stroke="#1C2025" stroke-width="1"/>
      <text x="120" y="92" text-anchor="middle" font-family="JetBrains Mono" font-size="9" letter-spacing="2" fill="#5A616B">DAY</text>
      <text x="120" y="138" text-anchor="middle" font-family="Space Grotesk" font-weight="300" font-size="48" fill="#1C2025">${day ?? "—"}</text>
      <text x="120" y="160" text-anchor="middle" font-family="JetBrains Mono" font-size="9" letter-spacing="2" fill="#5A616B">DAYS OLD</text>`;
  }

  function drawChart(daily) {
    const W = 560, H = 190, L = 34, R = 16, T = 18, B = 26;
    const svg = $("chart");
    if (!daily.length) {
      svg.innerHTML = `<text x="${L}" y="${H / 2}" font-family="Inter" font-size="13" fill="#5A616B">No games yet — the line starts with our first game.</text>`;
      return;
    }
    const vs = daily.map((p) => p.avg);
    let lo = Math.min(0, ...vs), hi = Math.max(0, ...vs);
    if (hi - lo < 4) { hi += 2; lo -= 2; }
    const y = (v) => T + (H - T - B) * (1 - (v - lo) / (hi - lo));
    const n = daily.length;
    const x = (i) => (n === 1 ? (L + W - R) / 2 : L + (W - L - R) * i / (n - 1));
    let s = `<path d="M${L} ${y(0).toFixed(1)}H${W - R}" stroke="#9AA6B2" stroke-width="1" stroke-dasharray="2 4"/>
      <text x="${L - 8}" y="${(y(0) + 3).toFixed(1)}" text-anchor="end" font-family="JetBrains Mono" font-size="9" fill="#5A616B">0</text>
      <text x="${L - 8}" y="${(y(hi) + 3).toFixed(1)}" text-anchor="end" font-family="JetBrains Mono" font-size="9" fill="#5A616B">${sgn(hi, 0)}</text>
      <text x="${L - 8}" y="${(y(lo) + 3).toFixed(1)}" text-anchor="end" font-family="JetBrains Mono" font-size="9" fill="#5A616B">${sgn(lo, 0)}</text>
      <path d="M${L} ${y(hi).toFixed(1)}H${W - R}M${L} ${y(lo).toFixed(1)}H${W - R}" stroke="#E2DFD8" stroke-width="1"/>
      <text x="${L}" y="${H - 6}" font-family="JetBrains Mono" font-size="9" fill="#5A616B">DAY ${KOMA.pad(daily[0].day)}</text>
      <text x="${W - R}" y="${H - 6}" text-anchor="end" font-family="JetBrains Mono" font-size="9" fill="#5A616B">DAY ${KOMA.pad(daily[n - 1].day)}</text>`;
    s += `<path d="${daily.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(p.avg).toFixed(1)}`).join("")}" fill="none" stroke="#1F6FA8" stroke-width="1.6"/>`;
    daily.forEach((p, i) => { s += `<circle cx="${x(i).toFixed(1)}" cy="${y(p.avg).toFixed(1)}" r="2" fill="#1F6FA8"/>`; });
    const lx = x(n - 1), ly = y(daily[n - 1].avg);
    s += `<rect x="${lx - 5}" y="${ly - 5}" width="10" height="10" fill="#1F6FA8" transform="rotate(45 ${lx} ${ly})"/>
          <text x="${lx - 10}" y="${ly - 10}" text-anchor="end" font-family="JetBrains Mono" font-size="10" fill="#1C2025">${sgn(daily[n - 1].avg)}</text>`;
    svg.innerHTML = s;
  }

  function drawValues(vals, oldLabel) {
    $("old-lbl").textContent = oldLabel || "A week ago";
    if (!vals.length || vals.every((v) => Math.abs(v.now) < 1e-9 && Math.abs(v.old) < 1e-9)) {
      $("values").innerHTML = `<p class="empty">Every weight is still 0 — my values change after each pair of games we play.</p>`;
      return;
    }
    const scale = Math.max(1, ...vals.map((v) => Math.max(Math.abs(v.now), Math.abs(v.old))));
    const bar = (w, cls, col) => {
      const p = Math.abs(w) / scale * 50;
      return `<i class="${cls}" style="${w >= 0 ? `left:50%` : `left:${50 - p}%`};width:${p}%;${col ? `background:${col}` : ""}"></i>`;
    };
    $("values").innerHTML = vals.map((v) => `<div class="vrow"><span>${KOMA.esc(v.name)}</span><span class="bars">${bar(v.old, "o")}${bar(v.now, "n", v.now >= 0 ? "#1F6FA8" : "#A04E0A")}</span><span class="v">${sgn(v.now, 2)}</span></div>`).join("");
  }

  function drawLesson(l) {
    if (!l) {
      $("lesson").innerHTML = `<div class="mb brk"><i class="c tl"></i><i class="c br"></i>${board({})}</div><p class="empty">No lesson yet. My values first move after we finish a pair of games.</p>`;
      return;
    }
    $("lesson").innerHTML = `<div class="mb brk"><i class="c tl"></i><i class="c br"></i>${board(l.board || {}, l.koma_white !== false)}</div>
      <div><div class="lbl k">Game ${KOMA.pad(l.game, 3)} · ${KOMA.esc(l.result)} · Reward ${sgn(l.reward, 0)}</div><p>${KOMA.esc(l.text)}</p></div>`;
  }

  function board(pos, komaWhite = true) {
    const V = KOMA.view(komaWhite);          // あなたの駒が手前。KOMA の駒は青
    let s = "";
    for (let r = 0; r < 8; r++) for (let c = 0; c < 8; c++) {
      const sq = V.files[c] + V.ranks[r], fi = "abcdefgh".indexOf(sq[0]), ri = Number(sq[1]) - 1;
      s += `<rect x="${c * 18}" y="${r * 18}" width="18" height="18" fill="${(fi + ri) % 2 === 0 ? "#E3DED4" : "#F6F4EF"}"/>`;
      const ch = pos[sq];
      if (ch) s += `<circle cx="${c * 18 + 9}" cy="${r * 18 + 9}" r="4.2" fill="${(ch === ch.toUpperCase()) === komaWhite ? "#1F6FA8" : "#1C2025"}"/>`;
    }
    return `<svg viewBox="0 0 144 144" aria-label="Final position">${s}</svg>`;
  }

  function drawTimeline(ev) {
    $("timeline").innerHTML = ev.map((e) => `<div class="tl${e.current ? " cur" : ""}"><span class="lbl d">Day ${KOMA.pad(e.day)}</span><span class="m"><i></i></span><span>${KOMA.esc(e.text)}</span></div>`).join("");
  }
})();
