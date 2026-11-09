"""対戦だけで育つチェスの頭脳（進化戦略による生涯学習）

・盤面の評価は「特徴量 × 重み」の足し算。重みは最初すべて 0（＝何も知らない。合法手をランダムに指すのと同じ）
・2手先まで読む（自分の手 → 相手の返し）アルファベータ探索で、評価が一番高い手を選ぶ
・学習は対称サンプリングの進化戦略（antithetic ES）：
    1局目は 重み + σε、2局目は 重み − σε で指し、
    報酬が良かった向きへ重みを動かす（w ← w + lr · sign(R⁺ − R⁻) · ε）
・報酬は「ゲーム終了時に取った駒の点数 − 取られた駒の点数」（P=1, N=3, B=3, R=5, Q=9）
・学習の材料はユーザとの対戦だけ。状態は ~/.chess-robot/brain.json に保存し、日をまたいで育ち続ける
"""
import datetime as dt
import json
import random

import chess
import numpy as np

from .paths import DATA, ensure_data_dir

BRAIN_FILE = DATA / "brain.json"
HISTORY_FILE = DATA / "history.jsonl"

VALUE = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 0}
FEATURES = ["ポーン", "ナイト", "ビショップ", "ルーク", "クイーン",
            "動ける手の数", "中央の支配", "相手キング周りへの圧力", "ポーンの前進",
            "王手", "駒の展開", "ただで取られる駒"]
CENTER = [chess.D4, chess.E4, chess.D5, chess.E5]


def features(board, me):
    """me（ロボットの色）から見た特徴量。どれも「自分 − 相手」の差"""
    you = not me
    f = np.zeros(len(FEATURES))
    for i, pt in enumerate([chess.PAWN, chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN]):
        f[i] = len(board.pieces(pt, me)) - len(board.pieces(pt, you))

    def mobility(color):
        b = board.copy(stack=False)
        b.turn = color
        return b.legal_moves.count()
    f[5] = (mobility(me) - mobility(you)) / 20

    def center(color):
        return sum(1 for s in CENTER if board.piece_at(s) and board.piece_at(s).color == color) + \
               sum(len(board.attackers(color, s)) for s in CENTER) / 2
    f[6] = (center(me) - center(you)) / 4

    def pressure(attacker):
        k = board.king(not attacker)
        if k is None:
            return 0
        ring = chess.SquareSet(chess.BB_KING_ATTACKS[k]) | chess.SquareSet.from_square(k)
        return sum(len(board.attackers(attacker, s)) for s in ring)
    f[7] = (pressure(me) - pressure(you)) / 6

    def advance(color):
        return sum((chess.square_rank(s) if color == chess.WHITE else 7 - chess.square_rank(s))
                   for s in board.pieces(chess.PAWN, color))
    f[8] = (advance(me) - advance(you)) / 8

    if board.is_check():
        f[9] = 1.0 if board.turn == you else -1.0

    def developed(color):
        back = 0 if color == chess.WHITE else 7
        return sum(1 for pt in (chess.KNIGHT, chess.BISHOP) for s in board.pieces(pt, color)
                   if chess.square_rank(s) != back)
    f[10] = (developed(me) - developed(you)) / 4

    def hanging(color):
        return sum(1 for s in chess.SquareSet(board.occupied_co[color])
                   if board.piece_type_at(s) != chess.KING and board.is_attacked_by(not color, s)
                   and not board.is_attacked_by(color, s))
    f[11] = (hanging(me) - hanging(you)) / 2
    return f


def evaluate(board, me, w):
    if board.is_checkmate():                      # ルールとしての勝ち負け（戦略の知識ではない）
        return -1e4 if board.turn == me else 1e4
    if board.is_game_over():
        return 0.0
    return float(w @ features(board, me))


def search(board, me, w, depth, alpha, beta):
    if depth == 0 or board.is_game_over():
        return evaluate(board, me, w)
    if board.turn == me:
        best = -1e9
        for mv in board.legal_moves:
            board.push(mv)
            best = max(best, search(board, me, w, depth - 1, alpha, beta))
            board.pop()
            alpha = max(alpha, best)
            if alpha >= beta:
                break
        return best
    best = 1e9
    for mv in board.legal_moves:
        board.push(mv)
        best = min(best, search(board, me, w, depth - 1, alpha, beta))
        board.pop()
        beta = min(beta, best)
        if alpha >= beta:
            break
    return best


def choose_move(board, w, depth=2, rng=random):
    me = board.turn
    scored = []
    for mv in board.legal_moves:
        board.push(mv)
        scored.append((search(board, me, w, depth - 1, -1e9, 1e9), mv))
        board.pop()
    best = max(s for s, _ in scored)
    top = [mv for s, mv in scored if s >= best - 1e-6]
    return rng.choice(top), best                   # 同点の手からはランダムに選ぶ


def material_reward(board, me):
    """ゲーム終了時の「取った点数 − 取られた点数」"""
    start = {chess.PAWN: 8, chess.KNIGHT: 2, chess.BISHOP: 2, chess.ROOK: 2, chess.QUEEN: 1}

    def lost(color):
        return sum(VALUE[pt] * max(n - len(board.pieces(pt, color)), 0) for pt, n in start.items())
    return lost(not me) - lost(me)


class Brain:
    def __init__(self, sigma=0.5, lr=0.35, seed=None):
        self.rng = np.random.default_rng(seed)
        if BRAIN_FILE.exists():
            self.s = json.loads(BRAIN_FILE.read_text())
        else:
            self.s = dict(w=[0.0] * len(FEATURES), sigma=sigma, lr=lr, games=0,
                          pair_eps=None, pair_sign=None, pair_r_plus=None,
                          born=dt.date.today().isoformat())

    @property
    def w(self):
        return np.array(self.s["w"])

    def day(self):
        born = dt.date.fromisoformat(self.s["born"])
        return (dt.date.today() - born).days + 1

    def candidate(self):
        """今回の対局で使う重み（今の重み ± σε）"""
        if self.s["pair_eps"] is None:
            self.s["pair_eps"] = self.rng.standard_normal(len(FEATURES)).tolist()
            self.s["pair_sign"] = 1
        eps = np.array(self.s["pair_eps"])
        return self.w + self.s["sigma"] * self.s["pair_sign"] * eps

    def learn(self, reward, info):
        """1局の報酬で学習する。対の2局目が終わったときに重みを更新"""
        self.s["games"] += 1
        updated = False
        if self.s["pair_sign"] == 1:
            self.s["pair_r_plus"] = reward
            self.s["pair_sign"] = -1
        else:
            r_plus, r_minus = self.s["pair_r_plus"], reward
            eps = np.array(self.s["pair_eps"])
            step = np.sign(r_plus - r_minus)
            self.s["w"] = (self.w + self.s["lr"] * step * eps).tolist()
            self.s["pair_eps"] = None
            self.s["pair_sign"] = None
            self.s["pair_r_plus"] = None
            updated = step != 0
        ensure_data_dir()
        BRAIN_FILE.write_text(json.dumps(self.s, indent=2, ensure_ascii=False))
        rec = dict(time=dt.datetime.now().isoformat(timespec="seconds"), day=self.day(),
                   game=self.s["games"], reward=reward, updated=bool(updated), w=self.s["w"], **info)
        with open(HISTORY_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return updated

    def report(self):
        w = self.w
        lines = [f"Day {self.day()} ・ これまでの対局 {self.s['games']} 局"]
        hist = []
        if HISTORY_FILE.exists():
            hist = [json.loads(l) for l in HISTORY_FILE.read_text(encoding="utf-8").splitlines() if l.strip()]
        if hist:
            last = [h["reward"] for h in hist[-10:]]
            lines.append(f"直近{len(last)}局の平均報酬 {np.mean(last):+.1f}")
        order = np.argsort(-np.abs(w))
        lines.append("いま大事だと思っていること：")
        for i in order[:5]:
            if abs(w[i]) > 1e-6:
                lines.append(f"  {FEATURES[i]:<14s} {w[i]:+.2f}")
        if np.allclose(w, 0):
            lines.append("  （まだ何も知りません。対局するほど覚えていきます）")
        return "\n".join(lines)
