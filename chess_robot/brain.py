"""対戦だけで育つチェスの頭脳（棋譜の振り返り＋進化戦略による生涯学習）

・盤面の評価は「特徴量 × 重み」の足し算。重みは最初すべて 0（＝何も知らない。合法手をランダムに指すのと同じ）
・2手先まで読む（自分の手 → 相手の返し）アルファベータ探索で、評価が一番高い手を選ぶ
・報酬は「ゲーム終了時に取った駒の点数 − 取られた駒の点数」（P=1, N=3, B=3, R=5, Q=9）
・重み w = w_review（振り返りで決まる土台）＋ delta（進化戦略で探す上乗せ）
  1. 棋譜の振り返り：これまでの全対局の全局面について「この局面の特徴量から、その局の最終的な報酬を当てる」
     ように w_review を当てはめ直す（リッジ回帰）。1局から数十の局面を学べるので、少ない対局で育つ
  2. 進化戦略（antithetic ES）：1局目は w + σε、2局目は w − σε で指し、
     報酬が良かった向きへ delta を動かす（delta ← delta + lr · sign(R⁺ − R⁻) · ε）。
     振り返りだけでは見つからない「当てはまりは悪いが勝てる」指し方を探す
・学習の材料はユーザとの対戦だけ。状態は ~/.chess-robot/brain.json、
  振り返り用の局面は ~/.chess-robot/cache/review.npz（対局の記録 history.jsonl からいつでも作り直せる）
"""
import datetime as dt
import json
import random

import chess
import numpy as np

from .paths import DATA, ensure_data_dir

BRAIN_FILE = DATA / "brain.json"
HISTORY_FILE = DATA / "history.jsonl"
REVIEW_FILE = DATA / "cache" / "review.npz"
RIDGE = 0.01            # 振り返りの回帰の正則化（局面数あたり）

VALUE = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 0}
FEATURES = ["ポーン", "ナイト", "ビショップ", "ルーク", "クイーン",
            "動ける手の数", "中央の支配", "相手キング周りへの圧力", "ポーンの前進",
            "王手", "駒の展開", "ただで取られる駒"]
FEATURES_EN = ["Pawn", "Knight", "Bishop", "Rook", "Queen",
               "Mobility", "Center control", "Pressure on enemy king", "Pawn advance",
               "Giving check", "Development", "Hanging pieces"]
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


# ---------------------------------------------------------------- 棋譜の振り返り
def game_positions(moves, me=chess.WHITE):
    """棋譜（uci をスペース区切り）から、各手を指したあとの局面の特徴量を並べる"""
    b = chess.Board()
    X = []
    for u in moves.split():
        b.push(chess.Move.from_uci(u))
        X.append(features(b, me))
    return np.array(X).reshape(-1, len(FEATURES))


def _history():
    if not HISTORY_FILE.exists():
        return []
    out = []
    for line in HISTORY_FILE.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out


class Review:
    """振り返り用の局面の集まり（X: 特徴量、y: その局の最終報酬）。history.jsonl と食い違えば作り直す"""

    def __init__(self):
        self.X = np.zeros((0, len(FEATURES)))
        self.y = np.zeros(0)
        self.keys = []                       # どの対局の局面を入れたか（time と game 番号）
        if REVIEW_FILE.exists():
            try:
                z = np.load(REVIEW_FILE, allow_pickle=False)
                self.X, self.y, self.keys = z["X"], z["y"], list(z["keys"])
            except (OSError, KeyError, ValueError):
                pass
        self.sync_with_history()

    @staticmethod
    def key(rec):
        return f"{rec.get('time', '')}#{rec.get('game', '')}"

    def sync_with_history(self):
        hist = [h for h in _history() if h.get("moves")]
        want = [self.key(h) for h in hist]
        if want[:len(self.keys)] != self.keys:     # 2台の PC の記録をまとめた等で順番が変わった → 作り直す
            self.X, self.y, self.keys = np.zeros((0, len(FEATURES))), np.zeros(0), []
        for h in hist[len(self.keys):]:
            self.add(h["moves"], h["reward"], self.key(h), save=False)
        if hist:
            self.save()

    def add(self, moves, reward, key, save=True):
        X = game_positions(moves)
        if len(X):
            self.X = np.vstack([self.X, X])
            self.y = np.concatenate([self.y, np.full(len(X), float(reward))])
        self.keys.append(key)
        if save:
            self.save()

    def save(self):
        REVIEW_FILE.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(REVIEW_FILE, X=self.X, y=self.y, keys=np.array(self.keys, dtype=str))

    def fit(self):
        """局面の特徴量 → 最終報酬 のリッジ回帰。局面がなければ 0"""
        n = len(self.y)
        if n == 0:
            return np.zeros(len(FEATURES))
        A = self.X
        return np.linalg.solve(A.T @ A + RIDGE * n * np.eye(A.shape[1]), A.T @ self.y)


# ---------------------------------------------------------------- 頭脳
class Brain:
    def __init__(self, sigma=0.3, lr=0.15, seed=None):
        self.rng = np.random.default_rng(seed)
        if BRAIN_FILE.exists():
            self.s = json.loads(BRAIN_FILE.read_text())
        else:
            self.s = dict(w=[0.0] * len(FEATURES), sigma=sigma, lr=lr, games=0,
                          pair_eps=None, pair_sign=None, pair_r_plus=None,
                          born=dt.date.today().isoformat())
        if "delta" not in self.s:                   # 振り返り学習の前の頭脳：これまでの棋譜を振り返って土台を作る
            self.s.update(version=2, sigma=sigma, lr=lr, delta=[0.0] * len(FEATURES))
            self._review = Review()
            if len(self._review.y):
                self.s["w_review"] = self._review.fit().tolist()
            else:                                   # 棋譜がなければ、今の重みをそのまま上乗せ分として引き継ぐ
                self.s["w_review"] = [0.0] * len(FEATURES)
                self.s["delta"] = list(self.s["w"])
            self.s["review_positions"] = int(len(self._review.y))
            self._sum()

    def _sum(self):
        self.s["w"] = (np.array(self.s["w_review"]) + np.array(self.s["delta"])).tolist()

    @property
    def w(self):
        return np.array(self.s["w"])

    def day(self):
        born = dt.date.fromisoformat(self.s["born"])
        return (dt.date.today() - born).days + 1

    def pair_game(self):
        """今の対局が、学習の対（2局で1組）の何局目か（candidate() を呼んだあとに使う）"""
        return 2 if self.s.get("pair_sign") == -1 else 1

    def candidate(self):
        """今回の対局で使う重み（今の重み ± σε）"""
        if self.s["pair_eps"] is None:
            self.s["pair_eps"] = self.rng.standard_normal(len(FEATURES)).tolist()
            self.s["pair_sign"] = 1
        eps = np.array(self.s["pair_eps"])
        return self.w + self.s["sigma"] * self.s["pair_sign"] * eps

    def learn(self, reward, info):
        """1局の結果で学習する。毎局：棋譜を振り返って土台を当てはめ直す。対の2局目のあと：進化戦略で上乗せ分を更新"""
        self.s["games"] += 1
        w_before = self.w.copy()
        if self.s["pair_sign"] == 1:
            self.s["pair_r_plus"] = reward
            self.s["pair_sign"] = -1
        elif self.s["pair_sign"] == -1:
            r_plus, r_minus = self.s["pair_r_plus"], reward
            eps = np.array(self.s["pair_eps"])
            step = np.sign(r_plus - r_minus)
            self.s["delta"] = (np.array(self.s["delta"]) + self.s["lr"] * step * eps).tolist()
            self.s["pair_eps"] = None
            self.s["pair_sign"] = None
            self.s["pair_r_plus"] = None
        ensure_data_dir()
        rec = dict(time=dt.datetime.now().isoformat(timespec="seconds"), day=self.day(),
                   game=self.s["games"], reward=reward, **info)
        if info.get("moves"):                       # 棋譜の振り返り
            review = getattr(self, "_review", None) or Review()
            review.add(info["moves"], reward, Review.key(rec))
            self.s["w_review"] = review.fit().tolist()
            self.s["review_positions"] = int(len(review.y))
            self._review = review
        self._sum()
        updated = not np.allclose(self.w, w_before)
        BRAIN_FILE.write_text(json.dumps(self.s, indent=2, ensure_ascii=False))
        rec.update(updated=bool(updated), w=self.s["w"])
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
