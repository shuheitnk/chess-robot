"""夜の練習：今日の対局でうまく運べなかった場面を、シミュレーションの中で練習する

  uv run python scripts/night_practice.py                 # 15世代・候補8（1時間前後）。ブラウザに練習の画面が開く
  uv run python scripts/night_practice.py --gens 5        # 短めに
  uv run python scripts/night_practice.py --workers 4     # 並列に解く数（CPU のコア数に合わせて）

・出発点は今の skill.json。対局で記録した場面（practice_cases.jsonl）とランダムな盤を混ぜて CMA-ES で磨く
・練習とは別のテスト課題で今の技能と比べ、良くなったときだけ skill.json を置き換える
・結果は ~/.chess-robot/night_log.jsonl に追記し、最後に GitHub と同期する
・頭脳（評価の重み）は変えない。頭脳が学ぶのは昼の対局だけ
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main():
    from chess_robot import night as N
    from chess_robot import reports, sync
    from chess_robot.brain import Brain
    from chess_robot.web.server import Hub, serve

    ap = argparse.ArgumentParser()
    ap.add_argument("--gens", type=int, default=15)
    ap.add_argument("--popsize", type=int, default=8)
    ap.add_argument("--cases", type=int, default=6, help="一晩で練習する場面の数（残りは次の夜へ）")
    ap.add_argument("--random", type=int, default=4, help="練習に混ぜるランダムな盤の数")
    ap.add_argument("--test-random", type=int, default=6, help="テストに使うランダムな盤の数")
    ap.add_argument("--workers", type=int, default=max(1, min(4, (os.cpu_count() or 2) - 1)))
    ap.add_argument("--sigma", type=float, default=0.08, help="探索の広さ（[0,1] に正規化した空間で）")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--lan", action="store_true", help="同じネットワークのスマホからも画面を開けるようにする")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--no-sync", action="store_true")
    args = ap.parse_args()

    do_sync = sync.enabled() and not args.no_sync
    if do_sync:
        sync.safe(sync.pull)
    skill_info = N.Skill.load()[1]
    if skill_info is None:
        print("技能（skill.json）がありません。先に scripts/pretrain_skill.py を実行するか、sync.py init で取ってきてください")
        return

    latest = {"msg": None}
    hub = Hub(welcome=lambda: [latest["msg"]] if latest["msg"] else [])

    def on_state(st):
        latest["msg"] = st
        hub.broadcast(st)

    np_ = N.NightPractice(gens=args.gens, popsize=args.popsize, max_cases=args.cases, n_random=args.random,
                          test_random=args.test_random, workers=args.workers, sigma=args.sigma, seed=args.seed,
                          on_state=on_state, day=Brain().day())
    _, url = serve(hub, reports.PAGES, reports.APIS, host="0.0.0.0" if args.lan else "127.0.0.1",
                   port=args.port, open_path="/night", open_browser=not args.no_browser)
    print(f"夜の練習の画面: {url}/night")
    print(f"場面 {len(np_.cases)} 個（次の夜へ {len(np_.queued)} 個）＋ランダムな盤 {args.random} 個 · "
          f"{args.gens} 世代 · 候補 {args.popsize} · 並列 {args.workers}")
    rec = np_.run()
    b, a = rec["test"]["before"], rec["test"]["after"]
    print(f"テスト（練習に使っていない課題）: 成功率 {b['sr']:.0%} → {a['sr']:.0%} · ずれ {b['err_mm']} → {a['err_mm']} mm · "
          f"倒れ {b['falls']} → {a['falls']} · 1回 {b['time_s']} → {a['time_s']} s")
    print("新しい技能に置き換えました" if rec["adopted"] else "今の技能のままにしました（良くならなかったため）")
    if do_sync:
        sync.safe(sync.push, "夜の練習" + ("（技能を更新）" if rec["adopted"] else ""))


if __name__ == "__main__":
    main()
