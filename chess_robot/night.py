"""夜の練習：対局で失敗した場面を作り直して、手先の技能（skill.json）だけを CMA-ES で磨く

・出発点は今の skill.json。課題は「practice_cases.jsonl の場面（まだ解けていないもの）」と「ランダムな盤」を混ぜる。
  課題は一晩のあいだ固定するので、世代ごとの成功率をそのまま比べられる
・最後に、練習とは別のテスト課題（場面の駒を少しずらしたもの＋別のランダムな盤）で、今の技能と比べる。
  良くなったとき（成功率が上がった、または同じ成功率でコストが下がった）だけ skill.json を置き換える
・結果は night_log.jsonl に追記する
・頭脳（評価の重み）には触れない。頭脳が学ぶのは昼の対局だけ
"""
import datetime as dt
import json
import time
import warnings
from multiprocessing import Pool

warnings.filterwarnings("ignore", message="Could not import matplotlib")   # cma のグラフ機能は使わない
import cma  # noqa: E402
import numpy as np

from . import practice as P
from . import scene as B
from .paths import DATA, ensure_data_dir
from .skill import SKILL_FILE, Skill, TaskEnv

LOG_FILE = DATA / "night_log.jsonl"
PARAMS = [  # (名前, 画面の名前, 単位, 表示の倍率, 小数の桁)
    ("grasp_h", "Grip height", "mm", 1000, 1), ("open", "Finger opening", "rad", 1, 3),
    ("close", "Grip force", "rad", 1, 2), ("release", "Release opening", "rad", 1, 3),
    ("descend_t", "Descent time", "s", 1, 2), ("carry_t", "Carry time", "s", 1, 2),
    ("settle_t", "Settle time", "s", 1, 2), ("safe_dz", "Carry height", "mm", 1000, 0),
]

# ---------------------------------------------------------------- 課題を解く（別プロセスでも動く）
_env = None


def _init():
    global _env
    _env = TaskEnv()


def run_task(arg):
    """arg = (u, task)。task は ("case", 場面, ずらす量[m], 乱数の種) か ("random", 乱数の種, 駒の数)"""
    global _env
    if _env is None:
        _init()
    u, task = arg
    skill = Skill.from_unit(u)
    if task[0] == "case":
        _, case, jitter, seed = task
        rng = np.random.default_rng(seed) if jitter > 0 else None
        r = _env.run_case(skill, case, rng, jitter)
    else:
        _, seed, n = task
        r = _env.run(skill, np.random.default_rng(seed), n, parked=True)
    return {k: r[k] for k in ("success", "cost", "err_mm", "disturb_mm", "toppled", "time_s")}


def summarize(results):
    """課題の結果の一覧 → 成功率・置き位置のずれ・倒れた駒の数・1回の時間・コスト"""
    if not results:
        return None
    return dict(sr=round(float(np.mean([r["success"] for r in results])), 4),
                err_mm=round(float(np.mean([r["err_mm"] for r in results])), 2),
                falls=int(sum(len(r["toppled"]) for r in results)),
                time_s=round(float(np.mean([r["time_s"] for r in results])), 2),
                cost=round(float(np.mean([r["cost"] for r in results])), 4))


def better(a, b):
    """a は b より良いか（成功率が高い、または同じ成功率でコストが低い）"""
    return a["sr"] > b["sr"] + 1e-9 or (abs(a["sr"] - b["sr"]) < 1e-9 and a["cost"] < b["cost"] * 0.98)


# ---------------------------------------------------------------- 画面用の小道具
def mini_board(case):
    """場面の駒の配置を、だいたいのマスに直す（小さな盤の絵に使う）"""
    sq = {}
    for name, x, y, _ in case["layout"]:
        rank = int(round((x - B.X0) / B.S - 0.5))
        file = int(round(3.5 - y / B.S))
        if 0 <= rank < 8 and 0 <= file < 8:
            ch = name[1].upper() if name[0] == "w" else name[1]
            sq["abcdefgh"[file] + str(rank + 1)] = ch
    return dict(squares=sq, frm=case.get("from"), to=case.get("to"))


def case_view(case, status):
    return dict(id=case["id"], title=f"Move {case.get('move_no', '—')} · {case.get('san', '—')}",
                desc=P.describe(case), status=status, board=mini_board(case), time=case.get("time"))


def params_view(skill, base):
    out = []
    for key, label, unit, k, nd in PARAMS:
        v, v0 = getattr(skill, key) * k, getattr(base, key) * k
        out.append(dict(key=key, label=label, unit=unit, value=round(v, nd + 1), delta=round(v - v0, nd + 1), digits=nd))
    return out


def load_log():
    if not LOG_FILE.exists():
        return []
    out = []
    for line in LOG_FILE.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out


def pending_cases(max_cases):
    """まだ解けていない場面（新しい順）を、今夜の分と次の夜に回す分に分ける"""
    solved = {c["id"] for n in load_log() for c in n.get("cases", []) if c.get("status") == "SOLVED"}
    todo = [c for c in P.load_cases() if c["id"] not in solved]
    todo.sort(key=lambda c: c.get("time", ""), reverse=True)
    return todo[:max_cases], todo[max_cases:]


# ---------------------------------------------------------------- 夜の練習
class NightPractice:
    def __init__(self, gens=15, popsize=8, max_cases=6, n_random=4, test_random=6, workers=2, sigma=0.08,
                 seed=None, on_state=None, day=None):
        self.gens, self.popsize, self.workers, self.sigma = gens, popsize, workers, sigma
        self.rng = np.random.default_rng(seed)
        self.on_state = on_state or (lambda st: None)
        self.base, info = Skill.load()
        self.info = info or {}
        self.u0 = self.base.to_unit()
        self.cases, self.queued = pending_cases(max_cases)
        sizes = [12, 20, 28, 24, 16, 26]
        self.train = [("case", c, 0.0, 0) for c in self.cases] + \
                     [("random", int(self.rng.integers(1 << 30)), sizes[i % len(sizes)]) for i in range(n_random)]
        # テスト：場面の駒を ±1mm ずらしたもの＋別のランダムな盤（練習には使わない）
        self.test = [("case", c, 0.001, int(self.rng.integers(1 << 30))) for c in self.cases] + \
                    [("random", int(self.rng.integers(1 << 30)), sizes[(i + 2) % len(sizes)]) for i in range(test_random)]
        self.started = dt.datetime.now()
        self.st = dict(type="night", phase="starting", started=self.started.isoformat(timespec="seconds"),
                       day=day, gen=0, gens=gens, popsize=popsize, n_cases=len(self.cases), n_random=n_random,
                       n_queued=len(self.queued), progress=dict(done=0, total=0), before=None, current=None,
                       chart=[], cases=[case_view(c, "QUEUED") for c in self.cases + self.queued],
                       params=params_view(self.base, self.base), test=None, adopted=None, message="")

    def set(self, **kw):
        self.st.update(kw)
        self.st["now"] = dt.datetime.now().isoformat(timespec="seconds")
        self.on_state(dict(self.st))

    def _evaluate(self, pool, us, tasks, phase):
        """候補たち × 課題たち を解いて、候補ごとの結果の一覧を返す（進み具合を画面へ送る）"""
        jobs = [(i, j) for i in range(len(us)) for j in range(len(tasks))]
        out = [[None] * len(tasks) for _ in us]
        self.set(phase=phase, progress=dict(done=0, total=len(jobs)))
        args = [(np.asarray(us[i], float), tasks[j]) for i, j in jobs]
        it = pool.imap(run_task, args) if pool else map(run_task, args)
        last = 0.0
        for k, r in enumerate(it):
            i, j = jobs[k]
            out[i][j] = r
            if time.monotonic() - last > 1.0 or k + 1 == len(jobs):
                last = time.monotonic()
                self.set(progress=dict(done=k + 1, total=len(jobs)))
        return out

    def _case_status(self, results):
        st = []
        for k, c in enumerate(self.cases):
            st.append(case_view(c, "SOLVED" if results[k]["success"] else "PRACTICING"))
        return st + [case_view(c, "QUEUED") for c in self.queued]

    def run(self):
        pool = Pool(self.workers, initializer=_init) if self.workers > 1 else None
        try:
            return self._run(pool)
        finally:
            if pool:
                pool.close()
                pool.join()

    def _run(self, pool):
        # 練習前の今の技能
        base_res = self._evaluate(pool, [self.u0], self.train, "baseline")[0]
        before = summarize(base_res)
        best_u, best_res, best = self.u0.copy(), base_res, before
        self.set(before=before, current=before, cases=self._case_status(base_res), chart=[])

        es = cma.CMAEvolutionStrategy(self.u0, self.sigma, {"popsize": self.popsize, "bounds": [0, 1],
                                                            "seed": int(self.rng.integers(1 << 30)), "verbose": -9})
        chart = []
        for gen in range(self.gens):
            self.set(gen=gen + 1, message=f"Generation {gen + 1}: trying {self.popsize} candidates")
            X = es.ask()
            mean = es.mean.copy()
            res = self._evaluate(pool, list(X) + [mean], self.train, "practicing")
            es.tell(X, [summarize(r)["cost"] for r in res[:-1]])
            # 世代の代表＝この世代の平均（CMA-ES の中心）と、候補のうち一番良かったもの
            cands = [(summarize(r), X[i] if i < len(X) else mean, r) for i, r in enumerate(res)]
            g_sum, g_u, g_res = max(cands, key=lambda t: (t[0]["sr"], -t[0]["cost"]))
            chart.append(dict(gen=gen + 1, sr=g_sum["sr"], cost=g_sum["cost"]))
            if better(g_sum, best):
                best_u, best_res, best = np.asarray(g_u, float).copy(), g_res, g_sum
            self.set(chart=chart, current=best, cases=self._case_status(best_res),
                     params=params_view(Skill.from_unit(best_u), self.base))

        # 練習に使っていない課題で、前の技能と比べる
        t = self._evaluate(pool, [self.u0, best_u], self.test, "testing")
        t0, t1 = summarize(t[0]), summarize(t[1])
        adopted = bool(not np.allclose(best_u, self.u0) and better(t1, t0))
        new = Skill.from_unit(best_u)
        if adopted:
            data = json.loads(SKILL_FILE.read_text()) if SKILL_FILE.exists() else {}
            data.pop("params", None)
            data["night"] = dict(time=dt.datetime.now().isoformat(timespec="seconds"), test_before=t0, test_after=t1)
            new.save(data)
            msg = (f"On new test tasks my success went from {t0['sr']:.0%} to {t1['sr']:.0%}, "
                   "so tomorrow I'll use the new skill.")
        else:
            msg = (f"On new test tasks the new skill wasn't better ({t0['sr']:.0%} → {t1['sr']:.0%}), "
                   "so I'll keep my current one.")
        # 置き換えなかったときは、場面の「解けた」は今の技能のまま（次の夜にまた練習する）
        self.set(phase="done", test=dict(before=t0, after=t1), adopted=adopted, message=msg,
                 params=params_view(new, self.base), cases=self._case_status(best_res if adopted else base_res))
        rec = dict(self.st, time=dt.datetime.now().isoformat(timespec="seconds"),
                   minutes=round((dt.datetime.now() - self.started).total_seconds() / 60, 1),
                   params_before={k: float(v) for k, v in vars(self.base).items()},
                   params_after={k: float(v) for k, v in vars(new).items()})
        rec.pop("progress", None)
        ensure_data_dir()
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec
