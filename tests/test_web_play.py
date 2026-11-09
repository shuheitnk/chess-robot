"""自動テスト：KOMA の画面のサーバを立ち上げ、WebSocket で数手指す

  uv run python tests/test_web_play.py            # 物理あり（数分かかる）
  uv run python tests/test_web_play.py --text     # 物理なし（速い）

本物の脳（~/.chess-robot）を汚さないよう、一時フォルダをデータの場所にして動かす（技能 skill.json だけ写す）。
"""
import argparse
import os
import shutil
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

REAL = Path(os.environ.get("CHESS_ROBOT_DATA", Path.home() / ".chess-robot")).expanduser()
TMP = Path(tempfile.mkdtemp(prefix="koma-test-"))
if (REAL / "skill.json").exists():
    shutil.copy2(REAL / "skill.json", TMP / "skill.json")
os.environ["CHESS_ROBOT_DATA"] = str(TMP)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import chess  # noqa: E402

from chess_robot import reports  # noqa: E402
from chess_robot.session import Session  # noqa: E402
from chess_robot.web.server import Hub, WSClient, serve  # noqa: E402


def wait_for(cl, pred, what, timeout=240):
    """条件に合う state が届くまで読む（frame は数えておく）"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        m = cl.recv(timeout=timeout)
        if m["type"] == "frame":
            wait_for.frames += 1
            continue
        if pred(m):
            return m
    raise AssertionError(f"timeout: {what}")


wait_for.frames = 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", action="store_true")
    args = ap.parse_args()
    physics = not args.text

    s = Session(physics=physics, depth=1, max_plies=60, do_sync=False, viewer=False)
    hub = Hub(on_message=s.submit, welcome=s.welcome)
    s.hub = hub
    _, url = serve(hub, reports.PAGES, reports.APIS, port=8790, open_browser=False)
    th = threading.Thread(target=s.run, daemon=True)
    th.start()

    html = urllib.request.urlopen(url + "/play").read().decode()
    assert "KOMA" in html and "play.js" in html, "page"
    for f in ("/koma.css", "/koma.js", "/play.css", "/play.js"):
        assert urllib.request.urlopen(url + f).status == 200, f

    cl = WSClient(url)
    st = wait_for(cl, lambda m: m.get("my_turn"), "KOMA's first move")
    print("KOMA:", st["moves"], "|", st["say"])
    assert len(st["moves"]) == 1 and st["say"].startswith("I "), st["say"]
    assert st["legal"] and all(len(u) >= 4 for u in st["legal"])
    if physics:
        assert st["err_mm"] is not None and st["err_mm"] < 4.0, st["err_mm"]
        assert st["vision"] and st["vision"]["changed"], st["vision"]
        print("  vision:", st["vision"])

    # 2手指す（e7e5 → 合法手の先頭）
    for k in range(2):
        uci = "e7e5" if "e7e5" in st["legal"] else st["legal"][0]
        cl.send({"type": "move", "uci": uci})
        n = len(st["moves"])
        st = wait_for(cl, lambda m: m.get("my_turn") and len(m["moves"]) == n + 2, f"reply to {uci}")
        print(f"you: {uci}  → KOMA: {st['moves'][-1]} | {st['say']}")
        if physics:
            print(f"  err {st['err_mm']} mm · vision {st['vision']}")
            assert st["err_mm"] < 4.0

    # 合法でない手は弾かれる
    cl.send({"type": "move", "uci": "a8a1"})
    st = wait_for(cl, lambda m: "isn't legal" in (m.get("say") or ""), "illegal move rejected")

    # 駒を直す
    cl.send({"type": "fix"})
    st = wait_for(cl, lambda m: m.get("my_turn") and ("square" in m["say"] or "nothing to fix" in m["say"]), "fix")
    print("fix:", st["say"])

    # 投了 → 学習の結果
    cl.send({"type": "resign"})
    st = wait_for(cl, lambda m: m.get("phase") == "over" and m.get("koma") == "STANDBY" and m.get("result"), "result")
    r = st["result"]
    print("result:", r["title"], "reward", r["reward"], "|", r["note"])
    assert r["reward"] is not None and r["outcome"] == "1-0"
    hist = (TMP / "history.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(hist) == 1, hist
    assert (TMP / "brain.json").exists()

    # 次の対局
    cl.send({"type": "new"})
    st = wait_for(cl, lambda m: m.get("phase") == "play" and m.get("my_turn"), "new game")
    assert len(st["moves"]) == 1 and st["pair"] == 2 and st["games"] == 1, (st["moves"], st["pair"], st["games"])
    print("new game:", st["moves"], "pair", st["pair"])
    if physics:
        assert wait_for.frames > 0, "no camera frames"
        print("camera frames received:", wait_for.frames)

    cl.send({"type": "quit"})
    st = wait_for(cl, lambda m: m.get("phase") == "over", "quit")
    assert st["result"]["reward"] is None
    hist = (TMP / "history.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(hist) == 1, "quit must not learn"
    s.stop.set()
    th.join(20)
    cl.close()
    print("OK", "(physics)" if physics else "(text)")
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    main()
