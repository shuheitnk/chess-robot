// 画面1：Day · Play — 盤をクリックして指す。状態はすべてサーバ（session.py）から届く
"use strict";
(() => {
  const $ = (id) => document.getElementById(id);
  const FILES_LEFT_TO_RIGHT = "hgfedcba";          // あなた（黒）が手前：左が h
  const JOINTS = ["SHOULDER_PAN", "SHOULDER_LIFT", "ELBOW_FLEX", "WRIST_FLEX", "WRIST_ROLL", "GRIPPER"];
  let S = null;                 // 最新の状態
  let sel = null;               // 選んだ駒のマス
  let frames = [];              // 映像が届いた時刻（fps の表示用）
  const conn = KOMA.connect(onMessage, (ok) => $("banner").classList.toggle("show", !ok));

  // ---------------------------------------------------------------- 盤
  const squares = {};
  function buildBoard() {
    const board = $("board");
    for (let r = 0; r < 8; r++) {
      for (let c = 0; c < 8; c++) {
        const name = FILES_LEFT_TO_RIGHT[c] + (r + 1);
        const b = document.createElement("button");
        const fi = "abcdefgh".indexOf(name[0]);
        b.className = "sq" + ((fi + r) % 2 === 0 ? " d" : "");
        b.dataset.sq = name;
        b.setAttribute("aria-label", name);
        b.addEventListener("click", () => clickSquare(name));
        board.appendChild(b);
        squares[name] = b;
      }
    }
    $("ranks").innerHTML = [1, 2, 3, 4, 5, 6, 7, 8].map((n) => `<span>${n}</span>`).join("");
    $("files").innerHTML = [...FILES_LEFT_TO_RIGHT].map((f) => `<span>${f}</span>`).join("");
  }

  function legalFrom(sq) {
    return (S && S.legal ? S.legal : []).filter((u) => u.slice(0, 2) === sq);
  }

  function drawBoard() {
    if (!S || !S.fen) return;
    const pos = KOMA.fenBoard(S.fen);
    const targets = sel ? legalFrom(sel).map((u) => u.slice(2, 4)) : [];
    const canPlay = S.my_turn && S.phase === "play";
    for (const [name, el] of Object.entries(squares)) {
      const ch = pos[name];
      el.classList.toggle("last", !!(S.last && (S.last.from === name || S.last.to === name)));
      el.classList.toggle("sel", sel === name);
      el.classList.toggle("check", S.check === name);
      const isTarget = targets.includes(name);
      el.classList.toggle("cap", isTarget && !!ch);
      el.classList.toggle("can", canPlay && ((ch && ch === ch.toLowerCase() && legalFrom(name).length > 0) || isTarget));
      let html = "";
      if (ch) {
        const g = KOMA.glyph(ch);
        html += `<span class="${g.cls}">${g.text}</span>`;
      }
      if (isTarget && !ch) html += `<span class="dot"></span>`;
      el.innerHTML = html;
      const who = ch ? (ch === ch.toUpperCase() ? "KOMA's " : "your ") : "";
      el.setAttribute("aria-label", `${name} ${ch ? who + PIECE[ch.toLowerCase()] : "empty"}${isTarget ? ", can move here" : ""}`);
    }
  }
  const PIECE = { k: "king", q: "queen", r: "rook", b: "bishop", n: "knight", p: "pawn" };

  function clickSquare(name) {
    hidePromo();
    if (!S || !S.my_turn || S.phase !== "play") return;
    const pos = KOMA.fenBoard(S.fen);
    if (sel && sel !== name) {
      const moves = legalFrom(sel).filter((u) => u.slice(2, 4) === name);
      if (moves.length === 1) return send(moves[0]);
      if (moves.length > 1) return showPromo(name, moves);
    }
    const ch = pos[name];
    sel = ch && ch === ch.toLowerCase() && legalFrom(name).length && sel !== name ? name : null;
    drawBoard();
  }

  function send(uci) {
    sel = null;
    S.my_turn = false;           // 返事が来るまでは触れないように
    drawBoard();
    conn.send({ type: "move", uci });
  }

  function showPromo(target, moves) {
    const box = $("promo");
    box.innerHTML = "";
    for (const p of ["q", "r", "b", "n"]) {
      const u = moves.find((m) => m[4] === p);
      if (!u) continue;
      const b = document.createElement("button");
      b.textContent = KOMA.glyph(p).text;
      b.setAttribute("aria-label", "Promote to " + PIECE[p]);
      b.addEventListener("click", () => { hidePromo(); send(u); });
      box.appendChild(b);
    }
    const sqEl = squares[target], wrap = box.parentElement;
    const a = sqEl.getBoundingClientRect(), w = wrap.getBoundingClientRect();
    box.hidden = false;
    const left = Math.min(Math.max(a.left - w.left, 0), w.width - box.offsetWidth);
    const top = a.top - w.top + (a.top - w.top > w.height / 2 ? -box.offsetHeight - 4 : a.height + 4);
    box.style.left = left + "px";
    box.style.top = top + "px";
    box.querySelector("button").focus();
  }
  function hidePromo() { $("promo").hidden = true; }
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") { hidePromo(); sel = null; drawBoard(); } });

  // ---------------------------------------------------------------- 右：相棒
  function drawHud(active) {
    const arc = (r, a0, a1) => {
      const p = (a) => [60 + r * Math.cos((a - 90) * Math.PI / 180), 60 + r * Math.sin((a - 90) * Math.PI / 180)];
      const [x0, y0] = p(a0), [x1, y1] = p(a1);
      return `M${x0.toFixed(2)} ${y0.toFixed(2)} A${r} ${r} 0 ${a1 - a0 > 180 ? 1 : 0} 1 ${x1.toFixed(2)} ${y1.toFixed(2)}`;
    };
    const pair = S && S.pair ? S.pair : 0;
    $("hud").innerHTML = `
      <circle cx="60" cy="60" r="56" fill="none" stroke="#CFCAC0" stroke-width="1"/>
      <circle cx="60" cy="60" r="50" fill="none" stroke="#1F6FA8" stroke-width="1" stroke-dasharray="0.6 3.4" stroke-linecap="round"/>
      <path d="M60 0v8M60 112v8M0 60h8M112 60h8" stroke="#1C2025" stroke-width="1"/>
      <path d="${arc(44, 10, 10 + 70 * Math.min(pair, 2) / 2 + 20)}" fill="none" stroke="#1F6FA8" stroke-width="3"/>
      <path d="${arc(44, 120, 165)}" fill="none" stroke="#1F6FA8" stroke-width="3"/>
      <path d="${arc(44, 200, 250)}" fill="none" stroke="#9AA6B2" stroke-width="1.2"/>
      ${active ? `<path class="spin" d="${arc(38, 0, 60)}" fill="none" stroke="#1F6FA8" stroke-width="1.5"/>` : ""}
      <circle cx="60" cy="60" r="30" fill="none" stroke="#1C2025" stroke-width="1"/>
      <circle cx="60" cy="60" r="9" fill="#1F6FA8"/>`;
  }

  function drawGauge(ev) {
    const g = $("gauge");
    const ticks = [0, 85, 170, 255, 340].map((x) => `<path d="M${x} ${x === 170 ? 5 : 8}v${x === 170 ? 14 : 8}" stroke="#CFCAC0" stroke-width="1"/>`).join("");
    let mark = "";
    if (ev && (ev.v !== null || ev.mate)) {
      const t = ev.mate ? (ev.mate === "koma" ? 1 : -1) : Math.tanh(ev.v / 3);
      const x = 170 + 168 * t;
      const col = t >= 0 ? "#1F6FA8" : "#A04E0A";
      mark = `<path d="M170 12H${x.toFixed(1)}" stroke="${col}" stroke-width="3"/>
              <rect x="${(x - 4.5).toFixed(1)}" y="7.5" width="9" height="9" fill="${col}" transform="rotate(45 ${x.toFixed(1)} 12)"/>`;
    }
    g.innerHTML = `<path d="M0 12H340" stroke="#CFCAC0" stroke-width="1"/>${ticks}${mark}`;
    let txt = "—";
    if (ev) txt = ev.mate ? ev.label : `${KOMA.fmt(ev.v, 2, true)} ${ev.label}`;
    $("eval-txt").textContent = txt;
  }

  function drawValues(vals) {
    const box = $("values");
    if (!vals || !vals.length || vals.every((v) => Math.abs(v.w) < 1e-9)) {
      box.innerHTML = `<p class="empty">I don't value anything yet — every weight is 0. I learn only from our games.</p>`;
      return;
    }
    const scale = Math.max(1, ...vals.map((v) => Math.abs(v.w)));
    box.innerHTML = vals.slice(0, 5).map((v) => {
      const pct = (Math.abs(v.w) / scale) * 50;
      const style = v.w >= 0 ? `left:50%;width:${pct}%;background:#1F6FA8` : `left:${50 - pct}%;width:${pct}%;background:#A04E0A`;
      return `<div class="val-row"><span>${KOMA.esc(v.name)}</span><span class="bar"><i style="${style}"></i></span><span class="v">${KOMA.fmt(v.w, 2, true)}</span></div>`;
    }).join("");
  }

  function drawMoves() {
    const m = S.moves || [];
    let html = "";
    for (let i = 0; i < m.length; i += 2) {
      const last = m.length - 1;
      const cls = (k) => (k === last ? (k % 2 === 0 ? "hl-koma" : "hl-you") : "");
      html += `<span class="no">${i / 2 + 1}</span><span class="${cls(i)}">${KOMA.esc(m[i])}</span><span class="${cls(i + 1)}">${KOMA.esc(m[i + 1] || "")}</span>`;
    }
    const box = $("moves");
    box.innerHTML = html || `<span class="no">—</span><span></span><span></span>`;
    box.scrollTop = box.scrollHeight;
    $("ply").textContent = `Ply ${m.length} / ${S.max_plies ?? "—"}`;
  }

  // ---------------------------------------------------------------- 左：カメラと関節
  function drawGrid() {
    const svg = $("cam-grid"), tag = $("cam-tag");
    const g = S && S.grid;
    if (!g || S.mode !== "physics") { svg.innerHTML = ""; tag.hidden = true; return; }
    const X = (u) => u * 1000, Y = (v) => v * 1000;
    let d = "";
    for (let i = 0; i <= 8; i++) {
      const u = g.left + (g.right - g.left) * i / 8, v = g.top + (g.bottom - g.top) * i / 8;
      d += `M${X(u)} ${Y(g.top)}V${Y(g.bottom)}M${X(g.left)} ${Y(v)}H${X(g.right)}`;
    }
    let mark = "";
    const vis = S.vision;
    if (vis && vis.changed && vis.changed.length) {
      const sq = (S.last && vis.changed.includes(S.last.to)) ? S.last.to : vis.changed[vis.changed.length - 1];
      const col = FILES_LEFT_TO_RIGHT.indexOf(sq[0]), row = Number(sq[1]) - 1;
      const cx = X(g.left + (g.right - g.left) * (col + 0.5) / 8), cy = Y(g.top + (g.bottom - g.top) * (row + 0.5) / 8);
      const right = cx < 560;
      mark = `<rect x="${cx - 16}" y="${cy - 16}" width="32" height="32" fill="none" stroke="#1F6FA8" stroke-width="3" transform="rotate(45 ${cx} ${cy})" vector-effect="non-scaling-stroke"/>
              <path d="M${cx + (right ? 24 : -24)} ${cy}H${right ? 1000 : 0}" stroke="#1F6FA8" stroke-width="1" vector-effect="non-scaling-stroke"/>`;
      const label = S.last && vis.match ? `${S.last.from} → ${S.last.to}` : vis.changed.join(" · ");
      tag.innerHTML = `Change detected<br><b>${KOMA.esc(label)}</b>${vis.match ? "<br>matches move" : ""}`;
      tag.hidden = false;
      tag.style.top = `calc(${(cy / 10).toFixed(1)}% - 22px)`;
      tag.style.left = right ? "auto" : "-22px";
      tag.style.right = right ? "-22px" : "auto";
    } else {
      tag.hidden = true;
    }
    svg.innerHTML = `<path d="${d}" fill="none" stroke="#1F6FA8" stroke-width="1" vector-effect="non-scaling-stroke" opacity="0.85"/>${mark}`;
  }

  function drawActuators(act) {
    const rows = act || JOINTS.map((n) => ({ name: n, deg: null, load: null }));
    $("act").innerHTML = rows.map((a) => {
      const on = a.load === null ? 0 : Math.round(a.load * 20);
      const segs = Array.from({ length: 20 }, (_, i) => `<i class="${i < on ? (a.load > 0.85 ? "hot" : "on") : ""}"></i>`).join("");
      const deg = a.deg === null ? "—" : (a.state ? `${a.state} ` : "") + `${KOMA.fmt(a.deg, 1)}°`;
      const ld = a.load === null ? "—" : `${Math.round(a.load * 100)}%`;
      return `<div class="act-row"><span class="nm">${a.name}</span><span class="deg">${deg}</span><span class="seg" aria-label="load ${ld}">${segs}</span><span class="ld">${ld}</span></div>`;
    }).join("");
  }

  function updateRate() {
    const now = performance.now();
    frames = frames.filter((t) => now - t < 3000);
    if (!S || S.mode !== "physics") { $("cam-rate").textContent = "Top cam · off"; return; }
    const recent = frames.filter((t) => now - t < 1500).length;
    $("cam-rate").textContent = recent ? `Top cam · ${(frames.length / 3).toFixed(1)} fps` : "Top cam · still";
  }
  setInterval(updateRate, 1000);

  // ---------------------------------------------------------------- 状態を画面へ
  function onMessage(msg) {
    if (msg.type === "frame") {
      frames.push(performance.now());
      const img = $("cam-img");
      img.src = "data:image/jpeg;base64," + msg.jpg;
      $("cam-view").hidden = false;
      $("cam-empty").hidden = true;
      drawActuators(msg.act);
      $("cam-foot").textContent = `Grid 8×8 · Pieces ${msg.pieces ?? "—"}`;
      return;
    }
    if (msg.type !== "state") return;
    const prevFen = S && S.fen;
    S = msg;
    if (S.fen !== prevFen || !S.my_turn) sel = null;
    render();
  }

  function render() {
    const physics = S.mode === "physics";
    $("unit").textContent = `Unit SO-101 · Day ${KOMA.pad(S.day)} · Game ${KOMA.pad(S.game_no, 3)}`;
    setDia("st-arm", physics ? "on" : "off", physics ? "Arm online" : "Arm off · text mode");
    setDia("st-vision", physics ? "on" : "off", physics ? "Vision tracking" : "Vision off");
    const sync = { SYNCED: ["on", "Brain synced"], OFFLINE: ["warn", "Brain offline"], LOCAL: ["off", "Brain local"],
                   "NOT SYNCING": ["off", "Sync off"] }[S.sync] || ["off", "Brain —"];
    setDia("st-brain", sync[0], sync[1]);

    $("koma-sub").textContent = `White · Depth ${S.depth ?? "—"}`;
    const chip = $("koma-chip");
    chip.textContent = S.koma || "—";
    chip.className = "chip lbl" + (S.koma === "THINKING" || S.koma === "LEARNING" ? " thinking" : S.koma === "MOVING" ? " moving" : "");
    $("you-text").textContent = S.you_text || "—";

    const over = S.phase === "over";
    const fix = $("btn-fix");
    fix.hidden = over || !physics;
    fix.disabled = !S.my_turn;
    $("btn-resign").hidden = over;
    $("btn-resign").disabled = !S.my_turn;
    $("btn-quit").hidden = over;
    $("btn-quit").disabled = !S.my_turn;
    $("btn-again").hidden = !over;
    $("btn-again").disabled = S.koma === "LEARNING";

    $("age").textContent = `Age ${S.day ?? "—"}d`;
    $("games").textContent = `Games ${S.games ?? "—"} · Pair ${S.pair ?? "—"}/2`;
    $("say").textContent = S.say || "—";
    drawHud(S.koma === "THINKING" || S.koma === "MOVING" || S.koma === "LEARNING");
    drawGauge(S.eval);
    drawValues(S.values);
    drawMoves();
    $("pair-note").textContent = S.pair_note || "";
    drawBoard();
    drawResult();

    if (!physics) {
      $("cam-view").hidden = true;
      $("cam-empty").hidden = false;
      $("cam-empty").innerHTML = "[ No camera — text mode ]<br>Physics simulation is off";
      $("cam-foot").textContent = "Grid — · Pieces —";
      drawActuators(null);
      $("act-note").textContent = "No arm in text mode.";
    }
    $("act-state").textContent = `${S.koma || "—"} · Err ${KOMA.fmt(S.err_mm, 1)} mm`;
    drawGrid();
  }

  function setDia(id, cls, text) {
    const el = $(id);
    el.className = "dia" + (cls === "on" ? "" : " " + cls);
    el.textContent = text;
  }

  function drawResult() {
    const box = $("result"), r = S.result;
    if (S.phase !== "over" || !r) { box.hidden = true; return; }
    box.hidden = false;
    const chg = (r.changes || []).map((c) =>
      `<span>${KOMA.esc(c.name)}</span><span>${KOMA.fmt(c.before, 2, true)} → <b class="blue">${KOMA.fmt(c.after, 2, true)}</b></span>`).join("");
    box.innerHTML = `
      <div class="lbl">Game over · Game ${KOMA.pad(S.game_no, 3)} · Pair ${S.pair}/2</div>
      <h2>${KOMA.esc(r.title)}</h2>
      <div class="nums">
        <div><div class="lbl">Reward</div><div class="big ${r.reward > 0 ? "blue" : r.reward < 0 ? "amber" : ""}">${KOMA.fmt(r.reward, 0, true)}</div></div>
        <div><div class="lbl">I captured</div><div class="big">${KOMA.fmt(r.captured, 0)}</div></div>
        <div><div class="lbl">I lost</div><div class="big">${KOMA.fmt(r.lost, 0)}</div></div>
      </div>
      <p>${KOMA.esc(r.note)}</p>
      ${chg ? `<div class="chg">${chg}</div>` : ""}
      ${S.koma === "LEARNING" ? `<p class="lbl">Learning…</p>` : ""}`;
  }

  // ---------------------------------------------------------------- ボタン
  $("btn-fix").addEventListener("click", () => conn.send({ type: "fix" }));
  $("btn-resign").addEventListener("click", () => {
    if (confirm("Resign this game? KOMA will learn from it.")) conn.send({ type: "resign" });
  });
  $("btn-quit").addEventListener("click", () => {
    if (confirm("Quit this game? KOMA won't learn from it.")) conn.send({ type: "quit" });
  });
  $("btn-again").addEventListener("click", () => conn.send({ type: "new" }));

  buildBoard();
  drawHud(false);
  drawGauge(null);
  drawActuators(null);
})();
