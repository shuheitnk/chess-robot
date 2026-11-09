"""駒を1つ運んだ結果の判定と、失敗した場面（夜の練習の教材）の記録

対局でもランダムな盤の課題でも、同じ基準で判定する。
  ・置いた位置のずれ 3mm 未満（取った駒をトレイへ置くときは問わない）
  ・倒れた駒がない（運んだ駒も、周りの駒も）
  ・周りの駒を動かした量の合計が 3mm 未満
失敗したときは、その直前の駒の配置・指した手・ずれの量を ~/.chess-robot/practice_cases.jsonl に記録する。
夜の練習（scripts/night_practice.py）は、この配置をシミュレーションの中に作り直して、同じ動作を練習する。
"""
import datetime as dt
import json

import numpy as np

from . import scene as B
from .paths import DATA, ensure_data_dir

CASES_FILE = DATA / "practice_cases.jsonl"
MAX_TILT_DEG = 10.0       # これ以上傾いたら倒れたとみなす
MAX_DZ = 0.002            # 盤の面からこれ以上浮いた・沈んだら倒れたとみなす [m]
OK_ERR = 0.003            # 置き位置のずれ [m]
OK_DISTURB = 0.003        # 周りの駒を動かした量の合計 [m]

NAMES = {"p": "pawn", "n": "knight", "b": "bishop", "r": "rook", "q": "queen", "k": "king"}


def up_z(quat):
    """クォータニオン (w, x, y, z) から、駒の上向きの軸の z 成分（＝傾きの cos）"""
    w, x, y, z = quat
    return 1.0 - 2.0 * (x * x + y * y)


def fallen(d, name, board_top=B.BOARD_TOP):
    """駒が倒れている（傾いている、または盤の面から浮いて・沈んでいる）か"""
    if up_z(d.body(name).xquat) < np.cos(np.radians(MAX_TILT_DEG)):
        return True
    return abs(d.body(name).xpos[2] - board_top) > MAX_DZ


def kind_of(name):
    """駒の body 名（例 wp4_1）→ 駒の種類の文字（p）"""
    return name[1]


def assess(d, name, dst, before, others, dst_on_board=True, duration=0.0):
    """1回運んだ結果。before: 運ぶ前の各駒の位置 {name: xyz}、others: 周りの駒（盤の上）の名前"""
    p = d.body(name).xpos
    err = float(np.hypot(p[0] - dst[0], p[1] - dst[1]))
    moved = {n: float(np.linalg.norm(d.body(n).xpos - before[n])) for n in others}
    disturb = float(sum(moved.values()))
    toppled = [n for n in [name, *others] if fallen(d, n)]
    ok_err = err < OK_ERR or not dst_on_board
    success = ok_err and not toppled and disturb < OK_DISTURB
    cost = (min(err, 0.05) / OK_ERR if dst_on_board else 0.0) + 2.0 * len(toppled) \
        + min(disturb, 0.1) / OK_DISTURB + 0.02 * duration
    return dict(success=bool(success), cost=float(cost), err_mm=round(err * 1000, 2),
                disturb_mm=round(disturb * 1000, 2), upright=name not in toppled,
                toppled=toppled, moved_mm={n: round(v * 1000, 2) for n, v in moved.items() if v > 0.0005},
                time_s=round(float(duration), 2))


def layout(d, names):
    """駒の配置（名前・x・y・向き）。夜の練習で同じ配置を作り直すのに使う"""
    out = []
    for n in names:
        p, q = d.body(n).xpos, d.body(n).xquat
        yaw = float(np.arctan2(2 * (q[0] * q[3] + q[1] * q[2]), 1 - 2 * (q[2] ** 2 + q[3] ** 2)))
        out.append([n, round(float(p[0]), 5), round(float(p[1]), 5), round(yaw, 4)])
    return out


def append_case(case):
    ensure_data_dir()
    case = dict(case)
    case.setdefault("time", dt.datetime.now().isoformat(timespec="seconds"))
    with open(CASES_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(case, ensure_ascii=False) + "\n")
    return case


def load_cases():
    if not CASES_FILE.exists():
        return []
    out = []
    for line in CASES_FILE.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out


def describe(case):
    """場面の説明（英語、画面用）"""
    r = case.get("result", {})
    where = case.get("squares", {})          # body 名 → マス名
    carried = case.get("piece", "")
    parts = []
    top = [n for n in r.get("toppled", [])]
    if carried in top:
        parts.append("Piece toppled after release")
    others_top = [n for n in top if n != carried]
    if others_top:
        n = others_top[0]
        parts.append(f"Knocked over the {NAMES.get(kind_of(n), 'piece')} on {where.get(n, '?')}")
    moved = {n: v for n, v in r.get("moved_mm", {}).items() if n not in top}
    if moved and r.get("disturb_mm", 0) >= OK_DISTURB * 1000 * 0.5:
        n = max(moved, key=moved.get)
        parts.append(f"Nudged the {NAMES.get(kind_of(n), 'piece')} on {where.get(n, '?')} by {moved[n]:.1f} mm")
    if case.get("dst_on_board", True) and r.get("err_mm", 0) >= OK_ERR * 1000:
        parts.append(f"Placed {r['err_mm']:.1f} mm off the square")
    return "; ".join(parts) or "Missed the success criteria"
