"""自動テスト：ロボットが指したあとの「倒れ・ずれ」の判定と直し、失敗した場面の記録と再現

  uv run python tests/test_practice_cases.py

・駒をわざと倒して（傾き）・浮かせて（高さ）、倒れの判定に引っかかるか
・その状態でロボットが指すと、場面が practice_cases.jsonl に記録され、指したあとに直されるか
・記録した場面を、夜の練習用の課題（TaskEnv.run_case）として作り直せるか
本物の脳を汚さないよう、一時フォルダをデータの場所にして動かす。
"""
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
import mujoco  # noqa: E402
import numpy as np  # noqa: E402

from chess_robot import practice as P  # noqa: E402
from chess_robot.sim_game import Game  # noqa: E402
from chess_robot.skill import Skill, TaskEnv  # noqa: E402


def main():
    g = Game(log_cases=True)
    g.case_ctx = dict(day=1, game=1)
    # f2 のポーンを 60° 傾け、d2 のポーンを 5mm 浮かせる
    f2, d2 = g.at[chess.F2], g.at[chess.D2]
    a = g.qadr[f2]
    g.d.qpos[a + 3:a + 7] = [np.cos(np.radians(30)), np.sin(np.radians(30)), 0, 0]
    mujoco.mj_forward(g.m, g.d)
    assert "f2" in g.fallen(), g.fallen()
    a = g.qadr[d2]
    g.d.qpos[a + 2] += 0.005
    mujoco.mj_forward(g.m, g.d)
    assert "d2" in g.fallen(), g.fallen()
    print("detected fallen:", g.fallen())

    rep = g.play(chess.Move.from_uci("e2e4"), by_robot=True)
    print("report:", {k: v for k, v in rep.items() if k != "carries"})
    assert rep["failed"], "the move should count as a failure (pieces were down)"
    assert not g.fallen(), f"still fallen after the automatic fix: {g.fallen()}"
    err, sq = g.check()
    assert err < 2.0, (err, sq)
    print(f"after fix: max error {err:.2f} mm, fallen {g.fallen()}")

    cases = P.load_cases()
    assert len(cases) == 1, cases
    c = cases[0]
    assert c["san"] == "e4" and c["from"] == "e2" and c["to"] == "e4" and c["piece"] == g.at[chess.E4]
    assert any(n == f2 for n in c["result"]["toppled"]), c["result"]["toppled"]
    print("case:", c["id"], "|", P.describe(c))

    env = TaskEnv()
    r = env.run_case(Skill.load()[0], c)
    print("replayed:", {k: r[k] for k in ("success", "err_mm", "disturb_mm", "toppled")})
    assert "success" in r

    n = g.fix(everything=True)
    print("fix(everything) →", n)
    print("OK")
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    main()
