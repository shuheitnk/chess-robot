"""自動テスト：倒れ・ずれの判定と直し、失敗した場面の記録と再現

  uv run python tests/test_practice_cases.py

1. 見えない手（あなたの手）だけでランダムな対局を60手進め、1手ごとに隣の駒を倒していないか・ずらしていないか
2. 倒れの判定（傾き・高さ）と、ロボットが指す前の盤の整え
3. ロボットが運んでいる最中に隣の駒が倒れたら、その場面が practice_cases.jsonl に記録され、指したあとに直される。
   運ぶ前から倒れていた駒はロボットのせいにしない
4. 記録した場面を、夜の練習用の課題（TaskEnv.run_case）として作り直せる
本物の脳を汚さないよう、一時フォルダをデータの場所にして動かす。
"""
import os
import random
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
import mujoco  # noqa: E402
import numpy as np  # noqa: E402

from chess_robot import practice as P  # noqa: E402
from chess_robot.sim_game import Game  # noqa: E402
from chess_robot.skill import Skill, TaskEnv  # noqa: E402


def tip(g, sq, deg=60):
    """駒を傾ける（盤にめり込まないよう、少し浮かせてから）"""
    a = g.qadr[g.at[sq]]
    g.d.qpos[a + 2] += 0.009
    g.d.qpos[a + 3:a + 7] = [np.cos(np.radians(deg / 2)), np.sin(np.radians(deg / 2)), 0, 0]
    g.d.qvel[g.vadr[g.at[sq]]:g.vadr[g.at[sq]] + 6] = 0
    mujoco.mj_forward(g.m, g.d)


def main():
    # 1. 見えない手は隣の駒を倒さない
    g = Game(log_cases=True)
    rng = random.Random(7)
    worst = 0.0
    for ply in range(60):
        if g.board.is_game_over():
            break
        mv = rng.choice(list(g.board.legal_moves))
        g.play(mv, by_robot=False)
        err, sq = g.check()
        worst = max(worst, err)
        assert not g.fallen(), f"ply {ply + 1} {mv}: fallen {g.fallen()}"
        assert err < 1.0, f"ply {ply + 1} {mv}: {sq} off by {err:.2f} mm"
    print(f"hand moves: {ply + 1} plies, no piece knocked over, worst offset {worst:.2f} mm")

    # 2. 倒れの判定と、指す前の整え
    g.reset()
    g.case_ctx = dict(day=1, game=1)
    tip(g, chess.F2)
    a = g.qadr[g.at[chess.D2]]
    g.d.qpos[a + 2] += 0.005
    mujoco.mj_forward(g.m, g.d)
    assert set(g.fallen()) >= {"f2", "d2"}, g.fallen()
    print("detected fallen (tilt, height):", g.fallen())

    # 3. 運んでいる最中に隣（f2）が倒れる → 記録される
    state = {"n": 0}

    def knock():
        state["n"] += 1
        if state["n"] == 150:              # 運んでいる途中で
            tip(g, chess.F2, deg=90)
    g.on_frame = knock
    rep = g.play(chess.Move.from_uci("e2e4"), by_robot=True)
    g.on_frame = None
    print("report:", {k: v for k, v in rep.items() if k != "carries"})
    f = rep["failed"][0] if rep["failed"] else {}
    assert f and ("f2" in f["toppled_sq"] or "f2" in f["moved_sq"]), rep
    assert "d2" not in f["toppled_sq"], "d2 was fixed before the move"
    assert not g.fallen(), f"still fallen after the automatic fix: {g.fallen()}"
    err, sq = g.check()
    assert err < 2.0, (err, sq)
    cases = P.load_cases()
    assert len(cases) == 1, cases
    c = cases[0]
    assert c["san"] == "e4" and c["from"] == "e2" and c["to"] == "e4"
    print("case:", c["id"], "|", P.describe(c))

    # 運ぶ前から倒れていた駒はロボットのせいにしない
    tip(g, chess.B8)
    g.fix = lambda *a, **k: 0               # 指す前の整えを止めて、倒れたまま運ばせる
    rep = g.play(chess.Move.from_uci("d2d4"), by_robot=True)
    del g.fix
    assert all("b8" not in r["toppled_sq"] for r in rep["carries"]), rep["carries"]
    print("pre-fallen piece not blamed:", [r["success"] for r in rep["carries"]])

    # 4. 場面の再現
    r = TaskEnv().run_case(Skill.load()[0], c)
    print("replayed:", {k: r[k] for k in ("success", "err_mm", "disturb_mm", "toppled")})
    assert "success" in r
    print("OK")
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    main()
