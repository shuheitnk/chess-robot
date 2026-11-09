"""対局の進行（KOMA の画面用）

物理シミュレーションと対局の進行はこのクラスがメインスレッドで動かし、
ブラウザとは Web サーバ（web/server.py の Hub）を通して WebSocket でやりとりする。
  ブラウザ → ここ：submit({"type": "move", "uci": "e7e5"}) / fix / resign / quit / new
  ここ → ブラウザ：{"type": "state", ...}（盤・棋譜・KOMA のひとこと・評価・重み…）と
                   {"type": "frame", ...}（真上のカメラの画像と関節の状態。数フレーム/秒）
画面に出す数字はすべて実際のデータから作る。データがないときは None（画面では "—"）。
"""
import base64
import queue
import random
import threading
import time

import chess
import numpy as np

from . import brain as Bn
from . import sync

PIECE = {chess.PAWN: "pawn", chess.KNIGHT: "knight", chess.BISHOP: "bishop",
         chess.ROOK: "rook", chess.QUEEN: "queen", chess.KING: "king"}
FRAME_EVERY = 0.25            # 映像を送る間隔 [s]（実時間）


def game_outcome(board, resigned=False, max_plies=None):
    """(result, end, 英語の説明)。result は "1-0" / "0-1" / "1/2-1/2" / "*"（KOMA＝白）"""
    if resigned:
        return "1-0", "resign", "You resigned."
    if board.is_checkmate():
        return (("0-1", "checkmate", "Checkmate — you win.") if board.turn == chess.WHITE
                else ("1-0", "checkmate", "Checkmate — I win."))
    if board.is_stalemate():
        return "1/2-1/2", "stalemate", "Stalemate — a draw."
    if board.is_game_over():
        return "1/2-1/2", "draw", "A draw."
    return "*", "limit", f"We reached the move limit ({max_plies} plies)."


def material(board):
    """(KOMA が取った点数, KOMA が取られた点数)"""
    start = {chess.PAWN: 8, chess.KNIGHT: 2, chess.BISHOP: 2, chess.ROOK: 2, chess.QUEEN: 1}

    def lost(color):
        return sum(Bn.VALUE[pt] * max(n - len(board.pieces(pt, color)), 0) for pt, n in start.items())
    return lost(chess.BLACK), lost(chess.WHITE)


def describe_robot_move(board, mv):
    """KOMA が指した手を一人称で（board は指す前の局面）"""
    p = board.piece_at(mv.from_square)
    to = chess.square_name(mv.to_square)
    mine = f"my {chess.FILE_NAMES[chess.square_file(mv.from_square)]}-pawn" if p.piece_type == chess.PAWN \
        else f"my {PIECE[p.piece_type]}"
    if board.is_castling(mv):
        s = "I castled " + ("kingside." if chess.square_file(mv.to_square) == 6 else "queenside.")
    elif board.is_capture(mv):
        vsq = mv.to_square
        if board.is_en_passant(mv):
            vsq = chess.square(chess.square_file(mv.to_square), chess.square_rank(mv.from_square))
        s = f"I took your {PIECE[board.piece_type_at(vsq)]} on {chess.square_name(vsq)} with {mine}."
    elif p.piece_type == chess.PAWN:
        s = f"I pushed {mine} to {to}."
    else:
        s = f"I moved {mine} to {to}."
    if mv.promotion:
        s += f" It becomes a {PIECE[mv.promotion]}."
    after = board.copy(stack=False)
    after.push(mv)
    if after.is_checkmate():
        s += " Checkmate."
    elif after.is_check():
        s += " Check."
    return s


def describe_report(report):
    """ロボットが運んだ結果のうち、うまくいかなかったこと（一人称）"""
    if not report:
        return ""
    issues = []
    for r in report["failed"]:
        top = [n for n in r["toppled"]]
        if r.get("piece") in top:
            issues.append(f"my {PIECE.get(chess.Piece.from_symbol(r['piece'][1]).piece_type, 'piece')} "
                          "toppled when I let go")
        others = [sq for n, sq in zip(r["toppled"], r["toppled_sq"]) if n != r.get("piece")]
        if others:
            issues.append(f"I knocked over the piece on {', '.join(others)}")
        if not top and r["moved_sq"] and r["disturb_mm"] >= 3:
            sq, mm = max(r["moved_sq"].items(), key=lambda kv: kv[1])
            issues.append(f"I nudged the piece on {sq} by {mm:.1f} mm")
        if r["err_mm"] >= 3 and r.get("to") != "tray":
            issues.append(f"I placed it {r['err_mm']:.1f} mm off the square")
    if issues:
        s = "; ".join(issues)
        return " " + s[0].upper() + s[1:] + " — I've set the board right and saved it for tonight's practice."
    if report["fixed"]:
        n = report["fixed"]
        return f" I straightened {n} piece{'s' if n > 1 else ''} that had shifted."
    return ""


def eval_label(v):
    if v is None:
        return "—"
    a = abs(v)
    if a < 0.25:
        return "EVEN"
    side = "WHITE" if v > 0 else "BLACK"
    if a < 1.0:
        return f"{side} SLIGHTLY BETTER"
    if a < 3.0:
        return f"{side} BETTER"
    return f"{side} WINNING"


class Session:
    def __init__(self, physics=True, depth=2, max_plies=120, do_sync=False, record=None, viewer=True, skill=None):
        self.depth, self.max_plies, self.do_sync = depth, max_plies, do_sync
        self.brain = Bn.Brain()
        self.rng = random.Random()
        self.cmds = queue.Queue()
        self.hub = None
        self.stop = threading.Event()
        self.state_msg = {"type": "state"}
        self.frame_msg = None
        self._last_frame = 0.0
        self.sync_state = "LOCAL"
        self.g = None
        self.record = record
        if physics:
            from .sim_game import Game
            self.g = Game(record=record, skill=skill, log_cases=True)
            if viewer and not record:
                import mujoco.viewer
                v = mujoco.viewer.launch_passive(self.g.m, self.g.d)
                self.g.viewer_sync = v.sync
            self.g.on_frame = self._on_frame
        self.st = dict(mode="physics" if physics else "text", depth=depth, max_plies=max_plies)

    # ------------------------------------------------------------ ブラウザとのやりとり
    def welcome(self):
        """新しくつながったブラウザに送るもの"""
        return [m for m in (self.state_msg, self.frame_msg) if m]

    def submit(self, msg):
        if isinstance(msg, dict) and msg.get("type") in ("move", "fix", "resign", "quit", "new"):
            self.cmds.put(msg)

    def _broadcast(self, msg):
        if self.hub:
            self.hub.broadcast(msg)

    def set(self, **changes):
        self.st.update(changes)
        self.state_msg = dict(self.st, type="state")
        self._broadcast(self.state_msg)

    def _on_frame(self):
        now = time.monotonic()
        if now - self._last_frame >= FRAME_EVERY and self.hub and self.hub.has_clients():
            self.send_frame()

    def send_frame(self):
        if not self.g:
            return
        self._last_frame = time.monotonic()
        self.frame_msg = dict(type="frame", jpg=base64.b64encode(self.g.top_frame()).decode("ascii"),
                              act=self.g.actuators(), pieces=self.g.pieces_on_board())
        self._broadcast(self.frame_msg)

    def wait_cmd(self, kinds):
        while not self.stop.is_set():
            try:
                c = self.cmds.get(timeout=0.2)
            except queue.Empty:
                continue
            if c["type"] in kinds:
                return c
        return {"type": "shutdown"}

    # ------------------------------------------------------------ 画面に出す情報
    @property
    def board(self):
        return self.g.board if self.g else self._board

    def values(self):
        w = self.brain.w
        order = np.argsort(-np.abs(w))
        return [dict(name=Bn.FEATURES_EN[i], w=round(float(w[i]), 3)) for i in order]

    def evaluation(self):
        b = self.board
        if b.is_checkmate():
            return dict(v=None, mate="you" if b.turn == chess.WHITE else "koma", label="CHECKMATE")
        if b.is_game_over():
            return dict(v=0.0, mate=None, label="DRAW")
        v = float(Bn.evaluate(b, chess.WHITE, self.w_play))
        return dict(v=round(v, 2), mate=None, label=eval_label(v))

    def board_state(self, my_turn=False):
        b = self.board
        last = None
        if b.move_stack:
            mv = b.peek()
            last = {"from": chess.square_name(mv.from_square), "to": chess.square_name(mv.to_square),
                    "by": "koma" if b.turn == chess.BLACK else "you"}
        tmp = chess.Board()
        sans = []
        for mv in b.move_stack:
            sans.append(tmp.san(mv))
            tmp.push(mv)
        ks = b.king(b.turn) if b.is_check() else None
        err = None
        if self.g:
            err = round(self.g.check()[0], 1)
        return dict(fen=b.fen(), moves=sans, last=last, check=chess.square_name(ks) if ks is not None else None,
                    my_turn=my_turn, legal=[m.uci() for m in b.legal_moves] if my_turn else [],
                    eval=self.evaluation(), err_mm=err,
                    grid=self.g.top_grid() if self.g else None)

    def look(self, before_img, board_before):
        """指す前と後の真上の画像から、変わったマスを見つける。盤の状態の変化と合っているかも調べる"""
        changed = self.g.changed_squares(before_img, self.g.top_image())
        pm0, pm1 = board_before.piece_map(), self.board.piece_map()
        expected = {chess.square_name(s) for s in set(pm0) | set(pm1) if pm0.get(s) != pm1.get(s)}
        return dict(changed=changed, expected=sorted(expected), match=set(changed) == expected)

    def header(self):
        return dict(day=self.brain.day(), games=self.brain.s["games"], sync=self.sync_state)

    # ------------------------------------------------------------ 対局
    def run(self):
        """対局を続ける（Play again で次の局へ）。止めるときは stop をセットする"""
        if self.do_sync:
            self.sync_state = "SYNCED" if sync.safe(sync.pull) else "OFFLINE"
            self.brain = Bn.Brain()                   # 取ってきた脳を読み直す
        elif sync.enabled():
            self.sync_state = "NOT SYNCING"
        first = True
        while not self.stop.is_set():
            if not first:
                if self.wait_cmd({"new"})["type"] == "shutdown":
                    break
                if self.g:
                    self.g.reset()
            first = False
            if self.play_one() == "shutdown":
                break
        if self.g:
            self.g.close()

    def play_one(self):
        if not self.g:
            self._board = chess.Board()
        b = self.board
        self.w_play = self.brain.candidate()
        pair = self.brain.pair_game()
        if self.g:
            self.g.case_ctx = dict(day=self.brain.day(), game=self.brain.s["games"] + 1)
            self.send_frame()
        pair_note = ("After every game I review all its positions to refine my values. This is game 1 of an "
                     "experiment pair: I'm trying slightly nudged values, and next game the opposite nudge."
                     if pair == 1 else
                     "After every game I review all its positions to refine my values. This is game 2 of an "
                     "experiment pair: afterwards I keep whichever nudge scored better.")
        self.set(phase="play", result=None, koma="STANDBY", game_no=self.brain.s["games"] + 1, pair=pair, vision=None,
                 pair_note=pair_note, values=self.values(), you_text="Waiting for KOMA…",
                 say="New game. I'm white, so I'll open.", **self.header(), **self.board_state())
        resigned = quit_ = False
        while not b.is_game_over() and len(b.move_stack) < self.max_plies:
            if b.turn == chess.WHITE:
                self.set(koma="THINKING", you_text="Waiting for KOMA…", **self.board_state())
                mv, _ = Bn.choose_move(b, self.w_play, depth=self.depth, rng=self.rng)
                text = describe_robot_move(b, mv)
                report = vision = None
                if self.g:
                    self.set(koma="MOVING")
                    self.g.caption = f"{b.fullmove_number}. {b.san(mv)}"
                    seen, b0 = self.g.top_image(), b.copy(stack=False)
                    report = self.g.play(mv, by_robot=True)
                    vision = self.look(seen, b0)
                    self.send_frame()
                else:
                    b.push(mv)
                if not b.is_game_over():
                    text += describe_report(report) + " Your move — how will you answer?"
                else:
                    text += describe_report(report)
                self.set(koma="STANDBY", say=text, vision=vision, **self.board_state())
                continue
            self.set(koma="STANDBY", you_text="Your move — click a piece", **self.board_state(my_turn=True))
            c = self.wait_cmd({"move", "fix", "resign", "quit"})
            if c["type"] == "shutdown":
                return "shutdown"
            if c["type"] in ("resign", "quit"):
                resigned, quit_ = c["type"] == "resign", c["type"] == "quit"
                break
            if c["type"] == "fix":
                if self.g:
                    n = self.g.fix(everything=True)
                    self.send_frame()
                    say = ("I set every piece back on the center of its square." if n == 0 else
                           f"I set the pieces back on their squares — {n} had shifted.")
                else:
                    say = "There's no physical board in text mode, so nothing to fix."
                self.set(say=say, **self.board_state(my_turn=True))
                continue
            try:
                mv = chess.Move.from_uci(str(c.get("uci", "")))
            except ValueError:
                continue
            if mv not in b.legal_moves:
                self.set(say="That move isn't legal here — try another.", **self.board_state(my_turn=True))
                continue
            san = b.san(mv)
            self.set(you_text=f"You played {san}", say=f"You played {san}. Let me think…",
                     **self.board_state())
            if self.g:
                self.g.caption = f"{b.fullmove_number}... {san}"
                seen, b0 = self.g.top_image(), b.copy(stack=False)
                self.g.play(mv, by_robot=False)
                self.set(vision=self.look(seen, b0))
                self.send_frame()
            else:
                b.push(mv)
        self.finish(resigned, quit_)
        return "done"

    def finish(self, resigned, quit_):
        b = self.board
        if quit_:
            self.set(phase="over", koma="STANDBY", you_text="Game stopped", **self.board_state(),
                     result=dict(title="Game stopped", outcome="Stopped without a result.", reward=None,
                                 captured=None, lost=None, updated=None, changes=[],
                                 note="I won't learn from this game."),
                     say="Let's stop here. I won't learn from this game.")
            return
        result, end, text = game_outcome(b, resigned, self.max_plies)
        reward = Bn.material_reward(b, chess.WHITE)
        cap, lost = material(b)
        pair = self.brain.pair_game()
        self.set(phase="over", koma="LEARNING", you_text=text, say=f"{text} Let me learn from this game…",
                 **self.board_state())
        w0 = self.brain.w.copy()
        updated = self.brain.learn(reward, dict(result=result, end=end, pair=pair, plies=len(b.move_stack),
                                                moves=" ".join(m.uci() for m in b.move_stack)))
        w1 = self.brain.w
        diff = w1 - w0
        order = [i for i in np.argsort(-np.abs(diff)) if abs(diff[i]) > 1e-9][:5]
        changes = [dict(name=Bn.FEATURES_EN[i], before=round(float(w0[i]), 3), after=round(float(w1[i]), 3))
                   for i in order]
        reviewed = int(self.brain.s.get("review_positions", 0))
        if pair == 1:
            note = (f"I reviewed every position of this game ({reviewed} positions so far across all our games) "
                    "and refined my values. My experiment nudge is judged after the next game.")
        else:
            note = (f"I reviewed every position of this game ({reviewed} positions so far) and compared this "
                    "game with the previous one to keep the better nudge.")
        if updated:
            note += " My values changed:"
        if self.do_sync:
            ok = sync.safe(sync.push, f"Game {self.brain.s['games']} (reward {reward:+d})")
            self.sync_state = "SYNCED" if ok else "OFFLINE"
        say = f"{text} I scored {reward:+d} (captured {cap} − lost {lost}). " + \
              ("My values changed — see below." if changes else "My values stay the same.")
        self.set(koma="STANDBY", values=self.values(), **self.header(), say=say,
                 result=dict(title=text, outcome=result, reward=reward, captured=cap, lost=lost,
                             updated=bool(updated), changes=changes, note=note))
