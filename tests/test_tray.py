"""自動テスト：取った駒のトレイ

  uv run python tests/test_tray.py

1. トレイの置き場15か所が、どれもトレイの上にあり、重ならず、アームが真下を向いて届く（白・黒の両側）
2. どの向きでも届かない場所でも、指の向きを選ぶところで止まらない
3. ロボットが黒の駒15個を、1つずつトレイへ運ぶ（以前は10個目で止まっていた）
本物の脳を汚さないよう、一時フォルダをデータの場所にして動かす。
"""
import itertools
import os
import shutil
import sys
import tempfile
from pathlib import Path

REAL = Path(os.environ.get("CHESS_ROBOT_DATA", Path.home() / ".chess-robot")).expanduser()
TMP = Path(tempfile.mkdtemp(prefix="koma-test-"))
if (REAL / "skill.json").exists():
    shutil.copy2(REAL / "skill.json", TMP / "skill.json")
os.environ["CHESS_ROBOT_DATA"] = str(TMP)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import chess  # noqa: E402
import numpy as np  # noqa: E402

from chess_robot import practice as P  # noqa: E402
from chess_robot import scene as B  # noqa: E402
from chess_robot.robot import choose_close  # noqa: E402
from chess_robot.sim_game import TRAY_SLOTS, Game  # noqa: E402


def main():
    g = Game()

    # 1. 置き場
    assert len(TRAY_SLOTS) == 15, len(TRAY_SLOTS)
    near = min(np.hypot(a[0] - b[0], a[1] - b[1]) for a, b in itertools.combinations(TRAY_SLOTS, 2))
    assert near > 0.019, f"slots too close: {near * 1000:.1f} mm"
    for white in (True, False):
        tray = g.m.geom("tray_w" if white else "tray_b")
        lo, hi = tray.pos[:2] - tray.size[:2], tray.pos[:2] + tray.size[:2]
        for i in range(15):
            g.tray_used[white] = i
            dst = g.tray_slot(white)
            assert np.all(dst[:2] - B.PIECE_R >= lo) and np.all(dst[:2] + B.PIECE_R <= hi), (white, i, dst)
            ok = g.mv.feasible(dst, "hold")
            n = sum(ok(np.array([np.cos(a), np.sin(a)])) for a in np.linspace(0, 2 * np.pi, 16, endpoint=False))
            assert n >= 8, f"{'white' if white else 'black'} slot {i} {dst[:2]}: reachable in {n}/16 directions"
    g.tray_used = {True: 0, False: 0}
    print(f"tray: 15 slots per side, all on the tray and reachable, at least {near * 1000:.0f} mm apart")

    # 2. 届かない場所でも止まらない
    c, _ = choose_close([0.30, 0.20, B.BOARD_TOP], [], 0.0065, 0.016, feasible=lambda c: False)
    assert c.shape == (3,), c
    print("unreachable target: falls back to", np.round(c, 2))

    # 3. ロボットが15個運ぶ
    black = sorted((sq for sq, n in g.at.items() if n[0] == "b" and g.board.piece_type_at(sq) != chess.KING),
                   key=lambda sq: (chess.square_rank(sq), sq))       # ポーンから
    placed = []
    for k, sq in enumerate(black):
        name = g.at.pop(sq)
        dst = g.tray_slot(False)
        r = g.robot_carry(name, dst, sq, None, "capture", dict(ply=k + 1))
        g.mv.park(B.BOARD_TOP)
        placed.append((name, dst))
        off = np.hypot(*(g.d.body(name).xpos[:2] - dst[:2])) * 1000
        print(f"  {k + 1:2d}. {chess.square_name(sq)} -> tray ({off:.1f} mm from the slot, "
              f"board pieces disturbed {r['disturb_mm']:.1f} mm)")
    g.hold(0.5)
    down = [n for n, _ in placed if P.fallen(g.d, n)]
    offs = [np.hypot(*(g.d.body(n).xpos[:2] - d[:2])) * 1000 for n, d in placed]
    print(f"tray after 15 captures: {len(placed) - len(down)} upright, worst {max(offs):.1f} mm from its slot")
    assert not down, f"knocked over in the tray: {down}"
    assert max(offs) < 6.0, offs
    assert not g.fallen(), g.fallen()
    print("OK")
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    main()
