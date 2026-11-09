"""クリックで指す 2D の盤面ウィンドウ（tkinter）

tkinter はメインスレッドで動かす決まりなので、対局の進行（ロボットの物理シミュレーション）は
別のスレッドで動かし、2つのあいだはキューでやりとりする。
  対局 → 画面：BoardWindow.show(...)   （どのスレッドから呼んでもよい）
  画面 → 対局：BoardWindow.wait()      （あなたの操作を待つ。("move", mv) / ("fix",) / ("resign",) / ("quit",)）
"""
import queue
import tkinter as tk

import chess

SQ = 72                       # 1マスの大きさ [px]
PAD = 26                      # 盤のまわりの座標表示の幅
LIGHT, DARK = "#eed9b5", "#b58863"
SEL, LAST, DOT = "#f6e05e", "#cdd16a", "#3b6e3b"
GLYPH = {chess.KING: "♚", chess.QUEEN: "♛", chess.ROOK: "♜",
         chess.BISHOP: "♝", chess.KNIGHT: "♞", chess.PAWN: "♟"}
OUTLINE = {chess.KING: "♔", chess.QUEEN: "♕", chess.ROOK: "♖",
           chess.BISHOP: "♗", chess.KNIGHT: "♘", chess.PAWN: "♙"}


class BoardWindow:
    def __init__(self, me=chess.BLACK, title="chess-robot", can_fix=True):
        """can_fix: 「駒を直す」ボタンを出すか（物理シミュレーションがないときは不要）"""
        self.me = me
        self.root = tk.Tk()
        self.root.title(title)
        self.root.resizable(False, False)
        self.font = ("Segoe UI Symbol", int(SQ * 0.62))
        self.inbox = queue.Queue()      # 対局 → 画面
        self.outbox = queue.Queue()     # 画面 → 対局
        self.board = chess.Board()
        self.my_turn = False
        self.busy = False               # ロボットが動いている間は操作できない
        self.sel = None

        size = SQ * 8 + PAD * 2
        self.cv = tk.Canvas(self.root, width=size, height=size, bg="#2b2b2b", highlightthickness=0)
        self.cv.pack()
        self.cv.bind("<Button-1>", self._click)
        self.status = tk.Label(self.root, text="準備中…", font=("Yu Gothic UI", 13), anchor="w")
        self.status.pack(fill="x", padx=10, pady=(6, 2))
        bar = tk.Frame(self.root)
        bar.pack(fill="x", padx=10, pady=(2, 10))
        self.btn_fix = tk.Button(bar, text="駒を直す", command=lambda: self._send("fix"))
        self.btn_resign = tk.Button(bar, text="投了", command=lambda: self._send("resign"))
        self.btn_quit = tk.Button(bar, text="中断（学習しない）", command=self._quit)
        for b in ((self.btn_fix,) if can_fix else ()) + (self.btn_resign, self.btn_quit):
            b.pack(side="left", padx=(0, 8))
        self.root.protocol("WM_DELETE_WINDOW", self._quit)
        self._draw()
        self.root.after(50, self._poll)

    # ------------------------------------------------------------ 対局スレッドから呼ぶ
    def show(self, board, status, my_turn=False, busy=False):
        self.inbox.put((board.copy(), status, my_turn, busy))

    def wait(self):
        return self.outbox.get()

    def run(self):
        self.root.mainloop()

    def close_later(self):
        self.inbox.put(None)

    # ------------------------------------------------------------ 画面側
    def _poll(self):
        try:
            while True:
                item = self.inbox.get_nowait()
                if item is None:
                    self.root.destroy()
                    return
                self.board, text, self.my_turn, self.busy = item
                self.sel = None
                self.status.config(text=text)
                state = "disabled" if self.busy else "normal"
                self.btn_fix.config(state=state)
                self.btn_resign.config(state="normal" if self.my_turn else "disabled")
                self._draw()
        except queue.Empty:
            pass
        self.root.after(50, self._poll)

    def _send(self, *cmd):
        if not self.busy:
            self.outbox.put(cmd)

    def _quit(self):
        self.outbox.put(("quit",))
        self.root.destroy()

    def _xy(self, sq):
        f, r = chess.square_file(sq), chess.square_rank(sq)
        if self.me == chess.BLACK:          # 自分の駒を手前に
            col, row = 7 - f, r
        else:
            col, row = f, 7 - r
        return PAD + col * SQ, PAD + row * SQ

    def _square_at(self, x, y):
        col, row = (x - PAD) // SQ, (y - PAD) // SQ
        if not (0 <= col < 8 and 0 <= row < 8):
            return None
        if self.me == chess.BLACK:
            return chess.square(7 - col, row)
        return chess.square(col, 7 - row)

    def _draw(self):
        cv, b = self.cv, self.board
        cv.delete("all")
        last = b.peek() if b.move_stack else None
        targets = {m.to_square for m in b.legal_moves if m.from_square == self.sel} if self.sel is not None else set()
        for sq in chess.SQUARES:
            x, y = self._xy(sq)
            light = (chess.square_file(sq) + chess.square_rank(sq)) % 2 == 1
            color = LIGHT if light else DARK
            if last and sq in (last.from_square, last.to_square):
                color = LAST
            if sq == self.sel:
                color = SEL
            cv.create_rectangle(x, y, x + SQ, y + SQ, fill=color, outline="")
            p = b.piece_at(sq)
            if p:
                cx, cy = x + SQ / 2, y + SQ / 2 + 2
                if p.color == chess.WHITE:
                    cv.create_text(cx, cy, text=GLYPH[p.piece_type], font=self.font, fill="#fbfbf8")
                    cv.create_text(cx, cy, text=OUTLINE[p.piece_type], font=self.font, fill="#1a1a1a")
                else:
                    cv.create_text(cx, cy, text=GLYPH[p.piece_type], font=self.font, fill="#1a1a1a")
            if sq in targets:                                  # 行ける先の印
                if p:
                    cv.create_oval(x + 3, y + 3, x + SQ - 3, y + SQ - 3, outline=DOT, width=4)
                else:
                    r = SQ * 0.14
                    cv.create_oval(x + SQ / 2 - r, y + SQ / 2 - r, x + SQ / 2 + r, y + SQ / 2 + r,
                                   fill=DOT, outline="")
        if b.is_check():                                       # 王手されている王を赤く囲む
            k = b.king(b.turn)
            x, y = self._xy(k)
            cv.create_rectangle(x + 2, y + 2, x + SQ - 2, y + SQ - 2, outline="#d33", width=4)
        for i in range(8):                                     # 座標（a〜h, 1〜8）
            f_sq = chess.square(i, 0)
            x, _ = self._xy(f_sq)
            for yy in (PAD / 2, PAD + SQ * 8 + PAD / 2):
                cv.create_text(x + SQ / 2, yy, text="abcdefgh"[i], fill="#ddd", font=("Consolas", 12))
            r_sq = chess.square(0, i)
            _, y = self._xy(r_sq)
            for xx in (PAD / 2, PAD + SQ * 8 + PAD / 2):
                cv.create_text(xx, y + SQ / 2, text=str(i + 1), fill="#ddd", font=("Consolas", 12))

    def _click(self, ev):
        if not self.my_turn or self.busy:
            return
        sq = self._square_at(ev.x, ev.y)
        if sq is None:
            return
        b = self.board
        p = b.piece_at(sq)
        if self.sel is not None and sq != self.sel:
            cands = [m for m in b.legal_moves if m.from_square == self.sel and m.to_square == sq]
            if cands:
                mv = cands[0] if len(cands) == 1 else self._ask_promotion(cands)
                if mv is not None:
                    self.my_turn = False
                    self.sel = None
                    self._draw()
                    self.outbox.put(("move", mv))
                return
        if p and p.color == self.me and any(m.from_square == sq for m in b.legal_moves):
            self.sel = None if sq == self.sel else sq          # もう一度押すと選択をやめる
        else:
            self.sel = None
        self._draw()

    def _ask_promotion(self, cands):
        """成る駒を選ぶ小さな窓"""
        choice = {"mv": None}
        top = tk.Toplevel(self.root)
        top.title("成る駒")
        top.transient(self.root)
        top.grab_set()
        tk.Label(top, text="何に成りますか？", font=("Yu Gothic UI", 12)).pack(padx=12, pady=(10, 4))
        row = tk.Frame(top)
        row.pack(padx=12, pady=(0, 12))
        for pt in (chess.QUEEN, chess.ROOK, chess.BISHOP, chess.KNIGHT):
            mv = next(m for m in cands if m.promotion == pt)

            def pick(mv=mv):
                choice["mv"] = mv
                top.destroy()
            tk.Button(row, text=GLYPH[pt], font=("Segoe UI Symbol", 26), width=2, command=pick).pack(side="left", padx=3)
        self.root.wait_window(top)
        return choice["mv"]
