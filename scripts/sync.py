"""育てた脳を GitHub で持ち運ぶ

最初に1回だけ（GitHub で空のリポジトリ、例えば chess-robot-brain を作ってから）:
  uv run python scripts/sync.py init https://github.com/<ユーザ名>/chess-robot-brain.git

あとは scripts/play.py が対局の前に GitHub の脳を取ってきて、対局の後に送るので、何もしなくてよい。
手動で同期したいとき:
  uv run python scripts/sync.py pull     # GitHub の脳を取ってくる
  uv run python scripts/sync.py push     # この PC の脳を GitHub に送る
  uv run python scripts/sync.py status   # つながっている先と、いまの脳の状態
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from chess_robot import sync  # noqa: E402
from chess_robot.paths import DATA  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init", help="データのフォルダを GitHub のリポジトリとつなぐ")
    p.add_argument("url")
    sub.add_parser("pull")
    sub.add_parser("push")
    sub.add_parser("status")
    args = ap.parse_args()

    try:
        if args.cmd == "init":
            for n in sync.init(args.url):
                print("[同期]", n)
            print(f"{DATA} を {args.url} とつなぎました。これからは対局のたびに自動で同期します")
        elif args.cmd == "pull":
            for n in sync.pull():
                print("[同期]", n)
            print("GitHub と同じ状態です")
        elif args.cmd == "push":
            sync.push("手動で保存")
            print("GitHub に送りました")
        else:
            print("データの場所:", DATA)
            print("同期先:", sync.remote_url() or "（まだつないでいません。sync.py init <URL> でつなげます）")
            from chess_robot.brain import Brain
            print(Brain().report())
    except sync.SyncError as e:
        print("同期できませんでした:", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
