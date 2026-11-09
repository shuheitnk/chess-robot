"""自動テスト：KOMA の画面のサーバを立ち上げ、WebSocket で数手指す

KOMA が先手の対局 → 投了して学習 → 次の対局で「あなたが先手」に切り替え（まだ指していないので、すぐに指し直す）
→ あなたが白で指す → 対局の途中で切り替え（次の対局から）→ 投了 → KOMA が先手に戻る → 中断。
取った駒の一覧（en passant を含む）も確かめる。

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
from chess_robot.session import Session, points, replay  # noqa: E402
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


def _board(sans):
    b = chess.Board()
    for san in sans:
        b.push_san(san)
    return b


def check_replay():
    """取った駒：白は d5 のポーンと en passant で e5 のポーン、黒は d5 のポーン"""
    b = chess.Board()
    for u in "e2e4 d7d5 e4d5 d8d5 b1c3 d5a5 d2d4 c7c5 d4d5 e7e5 d5e6".split():
        b.push_uci(u)
    sans, took = replay(b)
    assert sans[-1] == "dxe6" and took[chess.WHITE] == ["p", "p"] and took[chess.BLACK] == ["P"], (sans, took)
    assert points(took[chess.WHITE]) == 2 and points(["q", "n", "p"]) == 13
    print("captured pieces:", took)


def main():
    check_replay()
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
    assert st["koma_color"] == "white" and st["first"] == "koma" and st["last"]["by"] == "koma"
    assert st["taken"] == dict(koma=[], you=[], koma_pts=0, you_pts=0), st["taken"]
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

    # あなたが先手に切り替え：まだ指していないので、すぐに指し直す（学習しない）
    cl.send({"type": "side", "first": "you"})
    st = wait_for(cl, lambda m: m.get("koma_color") == "black" and m.get("my_turn"), "switched to you first")
    assert st["moves"] == [] and st["first"] == "you" and st["say"].startswith("Okay, switching sides"), st
    assert st["pair"] == 2 and st["games"] == 1, "switching must not learn"
    assert (TMP / "cache" / "play_prefs.json").exists()
    assert all(u[1] in "12" for u in st["legal"]), "you move the white pieces"
    if physics:
        assert st["err_mm"] is not None and st["err_mm"] < 1.0, st["err_mm"]
    print("switched:", st["say"])

    # あなたが白で指す → KOMA（黒）が返す。取った駒も合っているか
    for uci in ("e2e4", "d2d4", "b1c3"):
        if uci not in st["legal"]:
            uci = st["legal"][0]
        n = len(st["moves"])
        cl.send({"type": "move", "uci": uci})
        st = wait_for(cl, lambda m: m.get("my_turn") and len(m["moves"]) == n + 2, f"black reply to {uci}")
        _, took = replay(_board(st["moves"]))
        assert st["taken"]["koma"] == took[chess.BLACK] and st["taken"]["you"] == took[chess.WHITE], st["taken"]
        assert st["last"]["by"] == "koma"
        print(f"you (white): {uci}  → KOMA (black): {st['moves'][-1]} | taken {st['taken']}")
        if physics:
            print(f"  err {st['err_mm']} mm · vision {st['vision']}")
            assert st["err_mm"] < 4.0

    # 対局の途中で切り替え → 次の対局から
    cl.send({"type": "side", "first": "koma"})
    st = wait_for(cl, lambda m: m.get("next_first") == "koma", "switch applies next game")
    assert st["koma_color"] == "black" and st["first"] == "you" and "Next game" in st["say"], st["say"]
    print("mid-game switch:", st["say"])

    # 投了（KOMA は黒なので 0-1）→ 記録に KOMA の色が残る
    cl.send({"type": "resign"})
    st = wait_for(cl, lambda m: m.get("phase") == "over" and m.get("koma") == "STANDBY" and m.get("result"), "result 2")
    assert st["result"]["outcome"] == "0-1", st["result"]
    hist = (TMP / "history.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(hist) == 2 and '"koma": "black"' in hist[-1], hist[-1][:200]
    print("result as black:", st["result"]["title"], st["result"]["outcome"], "reward", st["result"]["reward"])

    # 次は KOMA が先手に戻る
    cl.send({"type": "new"})
    st = wait_for(cl, lambda m: m.get("phase") == "play" and m.get("my_turn"), "KOMA first again")
    assert st["koma_color"] == "white" and len(st["moves"]) == 1, st["moves"]
    if physics:
        assert st["err_mm"] < 4.0, st["err_mm"]

    cl.send({"type": "quit"})
    st = wait_for(cl, lambda m: m.get("phase") == "over", "quit")
    assert st["result"]["reward"] is None
    hist = (TMP / "history.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(hist) == 2, "quit must not learn"
    s.stop.set()
    th.join(20)
    cl.close()
    print("OK", "(physics)" if physics else "(text)")
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    main()
