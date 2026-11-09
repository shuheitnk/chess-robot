"""技能の事前学習：駒をランダムに並べた盤で「指定した駒を指定のマスへ」を繰り返し、CMA-ES で動作パラメータを最適化する

実行例:
  uv run python scripts/pretrain_skill.py --gens 15 --workers 2
結果は ~/.chess-robot/skill.json に保存され、対戦（scripts/play.py）でこの技能が使われる。
成功率が --target 以上になったら「安定した」とみなして終了する。
"""
import argparse
import json
import sys
import time
from multiprocessing import Pool
from pathlib import Path

import cma
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from chess_robot import skill as K  # noqa: E402


def job(args):
    u, seed, n_tasks, n_pieces = args
    cost, succ, _ = K.evaluate(u, seed, n_tasks, n_pieces)
    return cost, succ


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gens", type=int, default=15)
    ap.add_argument("--popsize", type=int, default=8)
    ap.add_argument("--tasks", type=int, default=3, help="1候補あたりの課題数")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--target", type=float, default=0.95, help="この成功率で安定とみなす")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    rng = np.random.default_rng(args.seed)
    u0 = rng.uniform(0.15, 0.85, len(K.RANGES))          # 何も知らない状態（ランダムな初期値）から始める
    es = cma.CMAEvolutionStrategy(u0, 0.25, {"popsize": args.popsize, "bounds": [0, 1],
                                             "seed": args.seed + 1, "verbose": -9})
    log = []
    print(f"初期値: {K.Skill.from_unit(u0)}")
    with Pool(args.workers) as pool:
        for gen in range(args.gens):
            t0 = time.time()
            X = es.ask()
            # 混み具合を世代ごとに変える（序盤〜中盤〜混んだ盤面）。同じ世代の候補は同じ課題で比べる
            n_pieces = int(rng.integers(10, 29))
            seed = int(rng.integers(1 << 30))
            out = pool.map(job, [(x, seed, args.tasks, n_pieces) for x in X])
            costs = [c for c, _ in out]
            es.tell(X, costs)
            mean_u = es.result.xfavorite
            m_cost, m_succ, _ = K.evaluate(mean_u, seed + 7, args.tasks, n_pieces)
            rec = dict(gen=gen, pieces=n_pieces, best_cost=min(costs), pop_success=float(np.mean([s for _, s in out])),
                       mean_success=m_succ, mean_cost=m_cost, sec=round(time.time() - t0, 1))
            log.append(rec)
            print(f"世代 {gen:2d}  駒{n_pieces:2d}個  候補の成功率 {rec['pop_success']:.2f}  "
                  f"平均パラメータの成功率 {m_succ:.2f}  コスト {m_cost:.2f}  ({rec['sec']}s)")
            K.Skill.from_unit(mean_u).save({"pretrain_log": log, "stable": False})

    # 最終確認：新しい課題で成功率を測る
    final = K.Skill.from_unit(es.result.xfavorite)
    _, succ, res = K.evaluate(es.result.xfavorite, 12345, 12, 24)
    stable = bool(succ >= args.target)
    final.save({"pretrain_log": log, "final_success": succ, "stable": stable,
                "final_tasks": [{k: v for k, v in r.items()} for r in res]})
    print(f"\n最終テスト（駒24個・12課題）の成功率 {succ:.2f} → {'安定' if stable else 'まだ不安定'}")
    print(json.dumps(final.__dict__, indent=2))


if __name__ == "__main__":
    main()
