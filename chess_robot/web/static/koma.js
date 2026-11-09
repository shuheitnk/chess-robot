// KOMA — 共通の小道具（ロゴ・ブラケット・数字の書式・WebSocket）
"use strict";
const KOMA = (() => {
  const VS = "︎";                         // 記号を絵文字にしない
  const OUTLINE = { k: "♔", q: "♕", r: "♖", b: "♗", n: "♘", p: "♙" };
  const FILLED = { k: "♚", q: "♛", r: "♜", b: "♝", n: "♞", p: "♟" };

  function logo() {
    return `<svg viewBox="0 0 26 26" aria-hidden="true" fill="none" stroke="#1F6FA8" stroke-width="1">
      <circle cx="13" cy="13" r="11.5"/><circle cx="13" cy="13" r="3.2"/>
      <path d="M13 0.5v4M13 21.5v4M0.5 13h4M21.5 13h4"/></svg>`;
  }

  function brackets(el) {
    for (const k of ["tl", "tr", "bl", "br"]) {
      const i = document.createElement("i");
      i.className = "c " + k;
      el.appendChild(i);
    }
    el.classList.add("brk");
  }

  // 数字の書式。値がないときは "—"
  function fmt(v, digits = 1, sign = false) {
    if (v === null || v === undefined || Number.isNaN(v)) return "—";
    const s = Number(v).toFixed(digits);
    return sign && v > 0 ? "+" + s : s.replace("-", "−");
  }
  const pad = (n, w = 2) => (n === null || n === undefined ? "—" : String(n).padStart(w, "0"));

  // 駒：KOMA（白）は輪郭の記号を青、あなた（黒）は塗りの記号を墨色
  function glyph(ch) {
    const white = ch === ch.toUpperCase();
    const k = ch.toLowerCase();
    return { text: (white ? OUTLINE[k] : FILLED[k]) + VS, cls: white ? "pc w" : "pc b" };
  }

  function fenBoard(fen) {
    const out = {};
    const rows = fen.split(" ")[0].split("/");
    rows.forEach((row, i) => {
      let f = 0;
      for (const ch of row) {
        if (/\d/.test(ch)) f += Number(ch);
        else { out["abcdefgh"[f] + (8 - i)] = ch; f += 1; }
      }
    });
    return out;
  }

  // WebSocket：切れたら数秒ごとにつなぎ直す
  function connect(onMessage, onStatus) {
    let ws = null, timer = null;
    const url = (location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws";
    function open() {
      ws = new WebSocket(url);
      ws.onopen = () => onStatus && onStatus(true);
      ws.onmessage = (e) => { try { onMessage(JSON.parse(e.data)); } catch (err) { console.error(err); } };
      ws.onclose = () => { onStatus && onStatus(false); clearTimeout(timer); timer = setTimeout(open, 2000); };
    }
    open();
    return { send: (obj) => ws && ws.readyState === 1 && ws.send(JSON.stringify(obj)) };
  }

  function esc(s) {
    return String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  }

  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("[data-logo]").forEach((el) => (el.innerHTML = logo()));
    document.querySelectorAll("[data-brk]").forEach(brackets);
  });

  return { logo, brackets, fmt, pad, glyph, fenBoard, connect, esc };
})();
