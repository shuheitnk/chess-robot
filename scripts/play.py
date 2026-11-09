"""ロボットと対戦する（ロボット＝白、あなた＝黒）

対局が終わるたびに、報酬（取った点数 − 取られた点数）でロボットの頭脳が学習し、~/.chess-robot/ に保存される（リポジトリを消しても残る）。
同じロボットと日をまたいで対局を続けるほど、少しずつ強くなっていく。

実行例:
  uv run python scripts/play.py              # MuJoCo のウィンドウでロボットが駒を動かす
  uv run python scripts/play.py --text       # 物理なし（盤面の文字表示だけ）。たくさん対局して育てたいとき
  uv run python scripts/play.py --record game.mp4   # 対局の動画も保存する

scripts/sync.py init で GitHub のリポジトリとつないでおくと、対局の前に GitHub の脳を取ってきて、
対局の後に送る（別の PC で育てた脳で続きを指せる）。

手の入力: e5 や Nf6 のような棋譜表記、または e7e5 のような形式。
          resign = 投了 / quit = 学習せずに中断 / board = 盤面を表示 / help = 合法手の一覧
"""
import argparse
import os
import random
import sys
from pathlib import Path

import chess

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from chess_robot import brain as Bn  # noqa: E402
from chess_robot import sync  # noqa: E402


ASCII = os.name == "nt"      # Windows のターミナルは ♟ を絵文字（幅2）で表示して列がずれるので、文字で表示する


PIECE_JA = {chess.PAWN: "ポーン", chess.KNIGHT: "ナイト", chess.BISHOP: "ビショップ",
            chess.ROOK: "ルーク", chess.QUEEN: "クイーン", chess.KING: "キング"}


def show(board):
    print()
    if ASCII:
        # 大文字＝白（ロボット）、小文字＝黒（あなた）。K=キング Q=クイーン R=ルーク B=ビショップ N=ナイト P=ポーン
        files = range(7, -1, -1)                      # あなた（黒）から見た向き：h が左
        print("    " + " ".join(chess.FILE_NAMES[f] for f in files))
        for r in range(8):
            row = [(board.piece_at(chess.square(f, r)) or ".").__str__() for f in files]
            print(f" {r + 1}  " + " ".join(row) + f"  {r + 1}")
        print("    " + " ".join(chess.FILE_NAMES[f] for f in files))
        print("    （大文字＝ロボット、小文字＝あなた）")
    else:
        print(board.unicode(borders=True, empty_square="·", orientation=chess.BLACK))
    print()


def ask(board):
    while True:
        s = input("あなたの手 > ").strip()
        if s in ("resign", "quit"):
            return s
        if s == "board":
            show(board); continue
        if s == "help":
            print(" ".join(board.san(m) for m in board.legal_moves)); continue
        try:
            return board.parse_san(s)
        except chess.AmbiguousMoveError:
            # 同じマスへ行ける駒が複数ある（例：2つのナイトが e6 へ行ける）→ どの駒かを聞く
            to = chess.parse_square(s.rstrip("+#")[-2:])
            kind = chess.PIECE_SYMBOLS.index(s[0].lower())
            cands = [m for m in board.legal_moves if m.to_square == to and board.piece_type_at(m.from_square) == kind]
            print(f"  {s} に行ける駒が{len(cands)}つあります。どちらか指定してください: "
                  + " / ".join(f"{board.san(m)}（{chess.square_name(m.from_square)} の駒）" for m in cands))
            continue
        except ValueError:
            pass
        try:
            return board.parse_uci(s)
        except ValueError:
            pass
        if s in chess.SQUARE_NAMES:
            # マスだけ書いた（＝ポーンの手）が、ポーンでは行けない → そのマスへ行ける手を教える
            to = chess.parse_square(s)
            cands = [m for m in board.legal_moves if m.to_square == to]
            if cands:
                print(f"  {s} はポーンの手になりますが、ポーンでは行けません。{s} へ行ける手: "
                      + " / ".join(f"{board.san(m)}（{chess.square_name(m.from_square)} の"
                                   f"{PIECE_JA[board.piece_type_at(m.from_square)]}）" for m in cands))
                continue
        print("  その手は指せません（help で合法手の一覧）")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", action="store_true", help="物理シミュレーションなしで対局する")
    ap.add_argument("--record", default=None, help="対局の動画を保存するファイル名（例: game.mp4）")
    ap.add_argument("--depth", type=int, default=2, help="読みの深さ（手数）")
    ap.add_argument("--max-plies", type=int, default=120, help="この手数で打ち切って報酬を計算")
    ap.add_argument("--force", action="store_true", help="技能の事前学習が終わっていなくても対局する")
    ap.add_argument("--no-sync", action="store_true", help="GitHub と同期しない")
    ap.add_argument("--ascii", action="store_true", default=None, help="盤面を文字（K Q R B N P）で表示する")
    ap.add_argument("--unicode", dest="ascii", action="store_false", help="盤面を駒の記号（♔♟）で表示する")
    args = ap.parse_args()
    global ASCII
    if args.ascii is not None:
        ASCII = args.ascii

    do_sync = sync.enabled() and not args.no_sync
    if do_sync:
        sync.safe(sync.pull)
    brain = Bn.Brain()
    print("=" * 50)
    print(brain.report())
    print("=" * 50)

    g = None
    if not args.text:
        from chess_robot.skill import Skill
        skill, info = Skill.load()
        if (info is None or not info.get("stable")) and not args.force:
            print("技能の事前学習がまだ終わっていません。先に scripts/pretrain_skill.py を実行してください。")
            print("（--force で、仮の技能のまま対局することもできます）")
            return
        from chess_robot.sim_game import Game
        g = Game(record=args.record, skill=skill)
        if not args.record:
            import mujoco.viewer
            viewer = mujoco.viewer.launch_passive(g.m, g.d)
            g.viewer_sync = viewer.sync
    board = g.board if g else chess.Board()

    w = brain.candidate()
    rng = random.Random()
    result = None
    show(board)
    while not board.is_game_over() and len(board.move_stack) < args.max_plies:
        if board.turn == chess.WHITE:
            mv, score = Bn.choose_move(board, w, depth=args.depth, rng=rng)
            san = board.san(mv)
            print(f"ロボット: {san}")
            if g:
                g.caption = f"{board.fullmove_number}. {san}"
                g.sub = f"Day {brain.day()}  game {brain.s['games'] + 1}"
                g.play(mv, by_robot=True)
                g.hold(0.3)
                err, sq = g.check()
                if err > 4:
                    print(f"  （駒がずれました：{sq} で {err:.1f}mm。位置を直します）")
                    g.fix()
            else:
                board.push(mv)
        else:
            show(board)
            mv = ask(board)
            if mv in ("resign", "quit"):
                result = mv
                break
            if g:
                g.caption = f"{board.fullmove_number}... {board.san(mv)}"
                g.play(mv, by_robot=False)
            else:
                board.push(mv)

    show(board)
    if result == "quit":
        print("中断しました（この対局は学習に使いません）")
        return
    reward = Bn.material_reward(board, chess.WHITE)
    outcome = "あなたの投了" if result == "resign" else (board.result() if board.is_game_over() else "手数で打ち切り")
    print(f"結果: {outcome} / ロボットの報酬（取った点数 − 取られた点数）: {reward:+d}")
    updated = brain.learn(reward, dict(result=outcome, plies=len(board.move_stack),
                                       moves=" ".join(m.uci() for m in board.move_stack)))
    print("頭脳を更新しました" if updated else "（この局の結果は、次の対局と比べてから学習に使います）")
    print(brain.report())
    if do_sync and sync.safe(sync.push, f"対局 {brain.s['games']}（報酬 {reward:+d}）"):
        print("[同期] GitHub の脳を更新しました")
    if g and args.record:
        g.hold(1.0)
        g.close()
        print("動画を保存しました:", args.record)


if __name__ == "__main__":
    main()
