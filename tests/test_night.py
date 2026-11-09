"""自動テスト：夜の練習を短く回す（2世代・候補4）

  uv run python tests/test_night.py

・対局で駒を倒した場面を1つ作って記録し、それを教材に練習する
・night_log.jsonl に記録が残り、skill.json は「良くなったときだけ」置き換わる
・頭脳（brain.json）には触れない
・画面のサーバから /night と /api/night が読める
本物の脳を汚さないよう、一時フォルダをデータの場所にして動かす。
"""
import json
import os
import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path

if "KOMA_TEST_DATA" in os.environ:              # 並列処理の子プロセスは親と同じフォルダを使う
    TMP = Path(os.environ["KOMA_TEST_DATA"])
else:
    REAL = Path(os.environ.get("CHESS_ROBOT_DATA", Path.home() / ".chess-robot")).expanduser()
    TMP = Path(tempfile.mkdtemp(prefix="koma-test-"))
    if (REAL / "skill.json").exists():
        shutil.copy2(REAL / "skill.json", TMP / "skill.json")
    os.environ["KOMA_TEST_DATA"] = str(TMP)
os.environ["CHESS_ROBOT_DATA"] = str(TMP)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main():
    import chess
    import mujoco
    import numpy as np

    from chess_robot import night as N
    from chess_robot import practice as P
    from chess_robot import reports
    from chess_robot.sim_game import Game
    from chess_robot.web.server import Hub, serve

    # 教材：f2 のポーンを倒した状態でロボットが e2e4 を指す → 失敗した場面として記録される
    g = Game(log_cases=True)
    g.case_ctx = dict(day=1, game=1)
    a = g.qadr[g.at[chess.F2]]
    g.d.qpos[a + 3:a + 7] = [np.cos(np.radians(30)), np.sin(np.radians(30)), 0, 0]
    mujoco.mj_forward(g.m, g.d)
    g.play(chess.Move.from_uci("e2e4"), by_robot=True)
    assert len(P.load_cases()) == 1
    skill_before = json.loads((TMP / "skill.json").read_text())["params"]

    states = []
    night = N.NightPractice(gens=2, popsize=4, max_cases=6, n_random=1, test_random=1, workers=2, seed=1,
                            on_state=states.append, day=1)
    rec = night.run()
    phases = [s["phase"] for s in states]
    print("phases:", sorted(set(phases), key=phases.index))
    print("before:", rec["before"], "\nbest:  ", rec["current"])
    print("chart:", rec["chart"])
    print("test:", rec["test"], "adopted:", rec["adopted"])
    print("cases:", [(c["title"], c["status"], c["desc"]) for c in rec["cases"]])
    assert phases[-1] == "done" and len(rec["chart"]) == 2
    assert all(c["status"] in ("SOLVED", "PRACTICING", "QUEUED") for c in rec["cases"])
    assert len(N.load_log()) == 1
    skill_after = json.loads((TMP / "skill.json").read_text())["params"]
    assert (skill_after != skill_before) == rec["adopted"], "skill.json must change only when adopted"
    assert not (TMP / "brain.json").exists(), "night practice must not touch the brain"

    hub = Hub()
    _, url = serve(hub, reports.PAGES, reports.APIS, port=8795, open_browser=False)
    assert "night.js" in urllib.request.urlopen(url + "/night").read().decode()
    api = json.loads(urllib.request.urlopen(url + "/api/night").read())
    assert api["phase"] == "done" and api["adopted"] == rec["adopted"]
    print("OK")
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    main()
