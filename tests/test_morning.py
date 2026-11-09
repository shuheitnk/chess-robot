"""自動テスト：朝のレポート（/api/morning）

  uv run python tests/test_morning.py

1. 記録が何もないとき：数字は None（画面では "—"）になり、作り話をしない
2. 何日分かの対局と夜の練習の記録（テスト用に一時フォルダに作る）から、数字・ひとこと・いちばん学んだ対局・成長の記録が出る
本物の脳を汚さないよう、一時フォルダをデータの場所にして動かす。
"""
import datetime as dt
import json
import os
import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="koma-test-"))
os.environ["CHESS_ROBOT_DATA"] = str(TMP)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from chess_robot import reports  # noqa: E402
from chess_robot.morning import morning  # noqa: E402
from chess_robot.web.server import Hub, serve  # noqa: E402


def write_jsonl(name, rows):
    (TMP / name).write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def main():
    # 1. 記録なし
    d = morning()
    print("empty:", {k: d[k] for k in ("day", "games", "reward_last10", "reward_week_ago", "lesson")})
    assert d["games"] == 0 and d["reward_last10"] is None and d["reward_week_ago"] is None and d["lesson"] is None
    assert d["values"] and all(v["now"] == 0 for v in d["values"])
    assert d["timeline"][0]["text"].startswith("Born")

    # 2. 10日分の記録（テスト用）
    now = dt.datetime.now()
    born = (now - dt.timedelta(days=9)).date()
    w = [0.0] * 12
    hist = []
    for k in range(12):
        t = now - dt.timedelta(days=9 - k * 0.8)
        pair = 1 if k % 2 == 0 else 2
        if pair == 2:
            w = list(w)
            w[4] += 0.35          # クイーン
            w[11] -= 0.12 * (k == 7) * 3   # 7局目でただで取られる駒が大きく動く
        hist.append(dict(time=t.isoformat(timespec="seconds"), day=(t.date() - born).days + 1, game=k + 1,
                         reward=[-6, -4, -3, -5, -1, 0, 2, -7, 1, 3, 2, 4][k], updated=pair == 2, w=w,
                         result="0-1" if k == 7 else "1-0", end="resign" if k != 7 else "checkmate",
                         pair=pair, plies=40, moves="e2e4 e7e5 g1f3 b8c6"))
    write_jsonl("history.jsonl", hist)
    (TMP / "brain.json").write_text(json.dumps(dict(w=w, sigma=0.5, lr=0.35, games=12, pair_eps=None,
                                                     pair_sign=None, pair_r_plus=None, born=born.isoformat())))
    test = {"before": dict(sr=0.67, err_mm=1.9, falls=2, time_s=10.6, cost=3.0),
            "after": dict(sr=1.0, err_mm=0.4, falls=0, time_s=9.8, cost=0.4)}
    write_jsonl("night_log.jsonl", [dict(time=(now - dt.timedelta(hours=6)).isoformat(timespec="seconds"),
                                         day=9, phase="done", adopted=True, n_cases=6, test=test)])
    d = morning()
    print("headline:", d["headline"])
    print("body:", d["body"])
    print("kpis:", d["games"], d["games_yesterday"], d["reward_last10"], d["reward_week_ago"], d["skill"], d["speed"])
    print("lesson:", d["lesson"]["text"])
    for e in d["timeline"]:
        print("  day", e["day"], e["text"], "◆" if e.get("current") else "")
    assert d["games"] == 12
    assert abs(d["reward_last10"] - sum(h["reward"] for h in hist[-10:]) / 10) < 0.01
    assert d["skill"]["sr"] == 1.0 and d["speed"]["delta"] == -0.8
    assert "steadier" in d["headline"]
    assert d["lesson"]["game"] == 8 and d["lesson"]["feature"] == "Hanging pieces", d["lesson"]
    assert d["old_label"] == "A week ago"
    assert any("first game" in e["text"] for e in d["timeline"]) or any("Practiced at night" in e["text"] for e in d["timeline"])
    assert d["daily"] and d["daily"][-1]["day"] >= d["daily"][0]["day"]

    hub = Hub()
    _, url = serve(hub, reports.PAGES, reports.APIS, port=8797, open_browser=False)
    assert "morning.js" in urllib.request.urlopen(url + "/morning").read().decode()
    api = json.loads(urllib.request.urlopen(url + "/api/morning").read())
    assert api["games"] == 12
    print("OK")
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    main()
