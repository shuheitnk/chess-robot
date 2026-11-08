"""デモ動画：ロボットが白黒両方を指して、決まった棋譜を再生する

  uv run python scripts/demo_video.py --out demo.mp4
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from chess_robot.sim_game import Game  # noqa: E402

# ナイトの両取りから駒の取り合いになる「フライド・リバー・アタック」
OPENING = ["e4", "e5", "Nf3", "Nc6", "Bc4", "Nf6", "Ng5", "d5", "exd5", "Nxd5", "Nxf7", "Kxf7", "Qf3+", "Ke6"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="demo.mp4")
    ap.add_argument("--plies", type=int, default=len(OPENING))
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=720)
    args = ap.parse_args()

    g = Game(record=args.out, size=(args.width, args.height))
    for i, san in enumerate(OPENING[:args.plies]):
        mv = g.board.parse_san(san)
        n = i // 2 + 1
        g.caption = f"{n}. {san}" if i % 2 == 0 else f"{n}... {san}"
        g.sub = "SO-101  ·  physics simulation"
        g.play(mv, by_robot=True)
        g.hold(0.3)
        err, sq = g.check()
        print(f"{g.caption:10s} 最大のずれ {err:.1f} mm ({sq})")
    g.hold(1.5)
    g.close()
    print("saved", args.out)


if __name__ == "__main__":
    main()
