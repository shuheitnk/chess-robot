"""駒を動かす技能と、その事前学習

技能は「つかむ高さ」「指の開き」「つかむ力」「離すときの開き」「降りる速さ」「運ぶ速さ」「止まる時間」
「移動の高さ」の8つのパラメータで決まる。これを、駒をランダムに並べた盤で
「指定した駒を指定したマスへ動かす」課題を解かせて、CMA-ES で最適化する。

課題の評価（小さいほど良い）
  ・置いた位置のずれ（3mm以内なら成功）
  ・周りの駒を動かしてしまった量
  ・駒が倒れたか
  ・かかった時間（少しだけ）
"""
import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path

import mujoco
import numpy as np

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

    def reset(self, rng, n_pieces):
        mujoco.mj_resetData(self.m, self.d)
        self.d.qpos[self.arm.qadr] = self.home
        self.d.ctrl[:5] = self.home
        self.d.ctrl[5] = R.GRIP_OPEN
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

    def run(self, skill, rng, n_pieces):
        name, (tx, ty) = self.reset(rng, n_pieces)
        on_board = [n for n in self.names if self.d.body(n).xpos[0] > 0]
        before = {n: self.d.body(n).xpos.copy() for n in on_board}
        others = [before[n][:2] for n in on_board if n != name]
        src = np.array([before[name][0], before[name][1], B.BOARD_TOP])
        dst = np.array([tx, ty, B.BOARD_TOP])
        mv = R.Mover(self.m, self.d, self.arm, None)
        t0 = self.d.time
        mv.pick_place(src, dst, skill, others, others)
        mv.hold(0.3)
        p = self.d.body(name).xpos
        err = float(np.hypot(p[0] - tx, p[1] - ty))
        upright = abs(p[2] - B.BOARD_TOP) < 0.002
        disturb = sum(float(np.linalg.norm(self.d.body(n).xpos - before[n])) for n in on_board if n != name)
        dur = self.d.time - t0
        ok = err < 0.003 and upright and disturb < 0.003
        cost = min(err, 0.05) / 0.003 + 2.0 * (not upright) + min(disturb, 0.1) / 0.003 + 0.02 * dur
        return dict(cost=float(cost), success=bool(ok), err_mm=round(err * 1000, 2),
                    disturb_mm=round(disturb * 1000, 2), upright=bool(upright), time_s=round(float(dur), 2))


def evaluate(u, seed, n_tasks, n_pieces):
    """1つの候補パラメータを、n_tasks 個のランダム課題で評価する（並列処理用に関数にしてある）"""
    env = TaskEnv()
    skill = Skill.from_unit(u)
    rng = np.random.default_rng(seed)
    res = [env.run(skill, rng, n_pieces) for _ in range(n_tasks)]
    return float(np.mean([r["cost"] for r in res])), float(np.mean([r["success"] for r in res])), res
