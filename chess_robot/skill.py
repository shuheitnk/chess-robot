"""駒を動かす技能と、その事前学習

技能は「つかむ高さ」「指の開き」「つかむ力」「離すときの開き」「降りる速さ」「運ぶ速さ」「止まる時間」
「移動の高さ」の8つのパラメータで決まる。これを、駒をランダムに並べた盤で
「指定した駒を指定したマスへ動かす」課題を解かせて、CMA-ES で最適化する。

課題の評価（小さいほど良い。判定は practice.py の assess で、対局と同じ基準）
  ・置いた位置のずれ（3mm以内なら成功）
  ・周りの駒を動かしてしまった量
  ・駒が倒れたか（運んだ駒も、周りの駒も）
  ・かかった時間（少しだけ）
"""
import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path

import mujoco
import numpy as np

from . import practice as P
from . import robot as R
from . import scene as B
from .paths import DATA, ensure_data_dir

SKILL_FILE = DATA / "skill.json"

# 各パラメータの探索範囲（学習は [0,1] に正規化した空間で行う）
RANGES = {
    "grasp_h": (0.002, 0.010),     # 駒の根元から何mmの高さをつかむか
    "open": (-0.02, 0.10),         # つかみに行くときの指の開き [rad]
    "close": (-0.25, 0.0),         # 閉じる指令 [rad]（小さいほど強く握る）
    "release": (0.0, 0.12),        # 離すときの開き [rad]
    "descend_t": (0.2, 1.5),       # 最後に降りる時間 [s]
    "carry_t": (0.4, 2.0),         # 水平に運ぶ時間 [s]
    "settle_t": (0.0, 0.5),        # 握った・離したあとに待つ時間 [s]
    "safe_dz": (0.045, 0.075),     # 運ぶときの盤からの高さ [m]
}


@dataclass
class Skill:
    grasp_h: float = 0.006
    open: float = 0.03
    close: float = -0.12
    release: float = 0.06
    descend_t: float = 0.85
    carry_t: float = 1.2
    settle_t: float = 0.25
    safe_dz: float = 0.06

    @classmethod
    def from_unit(cls, u):
        u = np.clip(np.asarray(u, float), 0, 1)
        vals = {f.name: RANGES[f.name][0] + u[i] * (RANGES[f.name][1] - RANGES[f.name][0])
                for i, f in enumerate(fields(cls))}
        return cls(**vals)

    def to_unit(self):
        return np.array([(getattr(self, f.name) - RANGES[f.name][0]) / (RANGES[f.name][1] - RANGES[f.name][0])
                         for f in fields(self)])

    @classmethod
    def load(cls):
        if SKILL_FILE.exists():
            d = json.loads(SKILL_FILE.read_text())
            return cls(**d["params"]), d
        return cls(), None

    def save(self, info):
        ensure_data_dir()
        params = {k: float(v) for k, v in asdict(self).items()}
        SKILL_FILE.write_text(json.dumps({"params": params, **info}, indent=2, ensure_ascii=False, default=float))


# ---------------------------------------------------------------- 課題：ランダム配置で指定の駒を指定マスへ
class TaskEnv:
    """駒をランダムに並べ、1つの駒を指定のマスへ動かす課題を作って、動作を評価する"""

    def __init__(self):
        self.m = mujoco.MjModel.from_xml_path(str(B.SCENE))
        self.d = mujoco.MjData(self.m)
        self.arm = R.Arm(self.m, self.d)
        self.names = [self.m.body(i).name for i in range(self.m.nbody)
                      if self.m.body(i).name[:1] in "wb" and "_" in self.m.body(i).name]
        self.qadr = {n: self.m.jnt_qposadr[self.m.joint(n).id] for n in self.names}
        self.home, _, _ = self.arm.ik([0.2, 0.0, 0.13], [0, -1, 1, 1, 0])

    def reset(self, rng, n_pieces, parked=False):
        mujoco.mj_resetData(self.m, self.d)
        self._arm_to(R.PARK if parked else self.home)
        squares = rng.permutation(64)
        used = list(rng.permutation(len(self.names))[:n_pieces])
        for k, name in enumerate(self.names):
            a = self.qadr[name]
            if k in used:
                sq = squares[used.index(k)]
                x, y = B.square_xy(sq % 8, sq // 8)
                # 置き位置に少しばらつき（人が置いた駒は中心からずれている）
                x += rng.uniform(-0.002, 0.002); y += rng.uniform(-0.002, 0.002)
                yaw = rng.uniform(-0.3, 0.3)
                self.d.qpos[a:a + 7] = [x, y, B.BOARD_TOP, np.cos(yaw / 2), 0, 0, np.sin(yaw / 2)]
            else:                                    # 使わない駒は遠くへ
                self.d.qpos[a:a + 7] = [-1.0 - 0.05 * k, 1.0, 0.0, 1, 0, 0, 0]
        mujoco.mj_forward(self.m, self.d)
        for _ in range(200):
            mujoco.mj_step(self.m, self.d)
        mover = squares[0]
        free = [s for s in squares[n_pieces:]]
        target = free[rng.integers(len(free))]
        name = self.names[used[0]]
        return name, B.square_xy(target % 8, target // 8)

    def run(self, skill, rng, n_pieces, parked=False):
        """ランダムな盤の課題を1つ解く。parked=True なら、対局と同じく待機姿勢から始める"""
        name, (tx, ty) = self.reset(rng, n_pieces, parked)
        return self._carry(skill, name, np.array([tx, ty, B.BOARD_TOP]), True, parked)

    def reset_case(self, case, rng=None, jitter=0.0):
        """対局で失敗した場面（practice_cases.jsonl の1件）の配置を作り直す。アームは待機姿勢から。
        jitter [m]: 駒の位置に足すばらつき（同じ場面の少し違う形で練習・テストするため）"""
        mujoco.mj_resetData(self.m, self.d)
        self._arm_to(R.PARK)
        placed = set()
        for k, (name, x, y, yaw) in enumerate(case["layout"]):
            if rng is not None and jitter > 0:
                x += rng.uniform(-jitter, jitter); y += rng.uniform(-jitter, jitter)
            self.d.qpos[self.qadr[name]:self.qadr[name] + 7] = [x, y, B.BOARD_TOP, np.cos(yaw / 2), 0, 0, np.sin(yaw / 2)]
            placed.add(name)
        for k, name in enumerate(self.names):
            if name not in placed:
                self.d.qpos[self.qadr[name]:self.qadr[name] + 7] = [-1.0 - 0.05 * k, 1.0, 0.0, 1, 0, 0, 0]
        mujoco.mj_forward(self.m, self.d)
        for _ in range(200):
            mujoco.mj_step(self.m, self.d)

    def run_case(self, skill, case, rng=None, jitter=0.0):
        """失敗した場面を作り直して、同じ動作をもう一度やってみる"""
        self.reset_case(case, rng, jitter)
        dst = np.array([case["dst"][0], case["dst"][1], B.BOARD_TOP])
        return self._carry(skill, case["piece"], dst, case.get("dst_on_board", True), True)

    def _arm_to(self, q):
        self.d.qpos[self.arm.qadr] = q
        self.d.ctrl[:5] = q
        self.d.ctrl[5] = R.GRIP_OPEN

    def _carry(self, skill, name, dst, dst_on_board, parked):
        on_board = [n for n in self.names if self.d.body(n).xpos[0] > 0]
        before = {n: self.d.body(n).xpos.copy() for n in on_board}
        others = [n for n in on_board if n != name]
        others_xy = [before[n][:2] for n in others]
        src = np.array([before[name][0], before[name][1], B.BOARD_TOP])
        mv = R.Mover(self.m, self.d, self.arm, None)
        t0 = self.d.time
        if parked:
            mv.cmd = R.PARK.copy()
            mv.unpark(src, B.BOARD_TOP)
        mv.pick_place(src, dst, skill, others_xy, others_xy)
        mv.hold(0.3)
        return P.assess(self.d, name, dst, before, others, dst_on_board=dst_on_board, duration=self.d.time - t0)


def evaluate(u, seed, n_tasks, n_pieces):
    """1つの候補パラメータを、n_tasks 個のランダム課題で評価する（並列処理用に関数にしてある）"""
    env = TaskEnv()
    skill = Skill.from_unit(u)
    rng = np.random.default_rng(seed)
    res = [env.run(skill, rng, n_pieces) for _ in range(n_tasks)]
    return float(np.mean([r["cost"] for r in res])), float(np.mean([r["success"] for r in res])), res
