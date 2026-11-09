"""SO-101 の逆運動学と、駒をつかんで運ぶ動作"""
import mujoco
import numpy as np

ARM = ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll"]
JOINTS = ARM + ["gripper"]
GRIP_OPEN = 0.02      # 待機中の指の開き（動作中の値は skill.py の Skill が決める）
# 待機の姿勢：肩を横（+y 側）へ 90° 回してたたむ。盤の上を空けて、真上のカメラと人の手の邪魔をしない
PARK = np.array([-1.57, -1.74, 1.2, 1.6, 0.0])
PARK_LIFT = 0.10      # 待機へ向かう前に、盤からこの高さまで真上に上がる [m]


class Arm:
    def __init__(self, model, data):
        self.m, self.d = model, data
        self.sites = {"hold": model.site("grip_hold").id,   # 挟んだ駒の中心
                      "pick": model.site("grip_pick").id}   # つかみに行くときの狙い点
        self.site = self.sites["hold"]
        self.jids = [model.joint(n).id for n in ARM]
        self.qadr = [model.jnt_qposadr[j] for j in self.jids]
        self.dadr = [model.jnt_dofadr[j] for j in self.jids]
        self.lo = model.jnt_range[self.jids, 0]
        self.hi = model.jnt_range[self.jids, 1]
        self.kd = mujoco.MjData(model)          # IK 用の計算専用データ（本物の状態は動かさない）

    def ik(self, target, q0, iters=200, w_rot=0.3, site="hold", close=None):
        """指先を target に置く関節角。指の向きはできるだけ真下にし、
        届かないときはロボットから外向きに少しずつ傾けた向き（最大45°）を試す。
        close: 指を閉じる向き（固定側の指→動く側の指、水平の単位ベクトル）。None なら盤の斜め方向"""
        target = np.asarray(target, float)
        radial = np.array([target[0], target[1], 0.0]); radial /= np.linalg.norm(radial) + 1e-9
        starts = [np.array(q0, float), np.array([0, -1.0, 1.0, 1.0, 0]), np.array([0, 0.0, 0.3, 1.4, 0]),
                  np.array([0, -1.5, 1.5, 0.6, 0])]
        pan = np.arctan2(target[1], target[0])
        for s in starts[1:]:
            s[0] = pan
        if close is None:
            cands = [(np.array([1.0, -1.0, 0.0]) / np.sqrt(2), True), (np.array([1.0, 1.0, 0.0]) / np.sqrt(2), True)]
        else:
            cands = [(np.asarray(close, float), False)]
        best = None
        for tilt in np.radians([0, 10, 20, 30, 40, 45]):
            a_tgt = np.array([0, 0, -np.cos(tilt)]) + np.sin(tilt) * radial
            for c0, flip in cands:
                c_tgt = c0 - a_tgt * (c0 @ a_tgt); c_tgt /= np.linalg.norm(c_tgt)
                for s in starts:
                    q, ep, er = self._ik(target, a_tgt, s, iters, w_rot, self.sites[site], c_tgt, flip)
                    if best is None or ep + 0.01 * er < best[1] + 0.01 * best[2]:
                        best = (q, ep, er)
                    if ep < 5e-4 and er < 1e-2:
                        return q, ep, er
        return best

    def _ik(self, target, down, q0, iters, w_rot, sid, close_dir=None, flip=True):
        m, d = self.m, self.kd
        q = np.clip(np.array(q0, float), self.lo, self.hi)
        jp = np.zeros((3, m.nv)); jr = np.zeros((3, m.nv))
        for _ in range(iters):
            d.qpos[self.qadr] = q
            mujoco.mj_kinematics(m, d); mujoco.mj_comPos(m, d)
            p = d.site_xpos[sid]
            Rm = d.site_xmat[sid].reshape(3, 3)
            a = Rm[:, 0]
            ep = target - p
            er = np.cross(a, down)
            if close_dir is not None:
                c = Rm[:, 2]
                if flip and c @ close_dir < 0:  # 向きの指定がなければ表裏どちらでもよい
                    close_dir = -close_dir
                if c @ close_dir < -0.5:        # 逆向きのときは外積が小さくなるので、手首を回す方向を直接与える
                    er = er + 0.5 * a
                else:
                    er = er + 0.5 * np.cross(c, close_dir)
            if np.linalg.norm(ep) < 2e-4 and np.linalg.norm(er) < 2e-3:
                break
            mujoco.mj_jacSite(m, d, jp, jr, sid)
            J = np.vstack([jp[:, self.dadr], w_rot * jr[:, self.dadr]])
            e = np.concatenate([ep, w_rot * er])
            dq = J.T @ np.linalg.solve(J @ J.T + 1e-4 * np.eye(6), e)
            q = np.clip(q + 0.8 * dq, self.lo, self.hi)
        return q, float(np.linalg.norm(ep)), float(np.linalg.norm(er))

    def q(self):
        return self.d.qpos[self.qadr].copy()


def gap(g):
    """指の角度 g [rad] のときの、2本の指の内側の間隔 [m]（シミュレーションで測った近似）"""
    return 0.0158 + 0.065 * g


def choose_close(target, others, ref_off, open_gap, piece_r=0.0065, pad_half_w=0.005, pad_t=0.004, feasible=None):
    """指を閉じる向きを選ぶ。周りの駒(others: xy の並び)から指が一番離れる向きを16方向から探す。
    ref_off: 狙い点から固定側の指の内側の面までの距離。open_gap: 指を開いたときの間隔"""
    best, best_c = -1e9, None
    tgt = np.asarray(target[:2], float)
    for ang in np.linspace(0, 2 * np.pi, 16, endpoint=False):
        c = np.array([np.cos(ang), np.sin(ang)])
        if feasible is not None and not feasible(c):
            continue
        p = np.array([-c[1], c[0]])
        segs = [(-ref_off - pad_t, -ref_off), (open_gap - ref_off, open_gap - ref_off + pad_t)]
        clear = 1e9
        for o in others:
            v = np.asarray(o[:2], float) - tgt
            u, w = v @ c, v @ p
            for lo, hi in segs:
                du = max(lo - u, 0, u - hi)
                dw = max(abs(w) - pad_half_w, 0)
                clear = min(clear, np.hypot(du, dw) - piece_r)
        # 同じくらい空いているなら、ロボットから見て斜め向き（手首を大きくひねらない向き）を好む
        score = min(clear, 0.02) - 0.0005 * abs(np.sin(2 * ang) - 1)
        if score > best:
            best, best_c = score, c
    return np.array([best_c[0], best_c[1], 0.0]), best


class Mover:
    """関節目標を滑らかに補間しながら物理シミュレーションを進め、フレームを記録する"""

    def __init__(self, model, data, arm, frame_cb=None, fps=30):
        self.m, self.d, self.arm = model, data, arm
        self.grip = GRIP_OPEN
        self.frame_cb = frame_cb
        self.every = int(round(1.0 / fps / model.opt.timestep))
        self.t = 0
        self.cmd = arm.q()
        self.bias = np.zeros(3)
        self.close = None          # 今の指の向き（None なら盤の斜め方向）

    def step(self, n=1):
        for _ in range(n):
            self.d.ctrl[:5] = self.cmd
            self.d.ctrl[5] = self.grip
            mujoco.mj_step(self.m, self.d)
            self.t += 1
            if self.frame_cb and self.t % self.every == 0:
                self.frame_cb()

    def feasible(self, target, site):
        """その向きで指を真下に向けて届くか（手首のひねりの範囲を超える向きを除くため）"""
        def ok(c):
            q = self.cmd
            for dz in (0.006, 0.035, 0.06):          # つかむ高さ・途中・上空のすべてで、真下向きのまま届くこと
                q, ep, er = self.arm.ik([target[0], target[1], target[2] + dz], q, site=site,
                                        close=np.array([c[0], c[1], 0.0]))
                self.arm.kd.qpos[self.arm.qadr] = q
                mujoco.mj_kinematics(self.m, self.arm.kd)
                down = self.arm.kd.site_xmat[self.arm.sites[site]].reshape(3, 3)[2, 0]
                if ep > 1e-3 or er > 2e-2 or down > -0.995:
                    return False
            return True
        return ok

    def ik(self, xyz, q0, site):
        return self.arm.ik(xyz, q0, site=site, close=self.close)

    def go(self, q_target, secs):
        q0 = self.cmd.copy()
        n = max(1, int(secs / self.m.opt.timestep))
        for i in range(n):
            s = (i + 1) / n
            s = s * s * (3 - 2 * s)                 # なめらかに加減速
            self.cmd = q0 + (q_target - q0) * s
            self.step()

    def go_linear(self, q_target, secs):
        q0 = self.cmd.copy()
        n = max(1, int(secs / self.m.opt.timestep))
        for i in range(n):
            self.cmd = q0 + (q_target - q0) * (i + 1) / n
            self.step()

    def cmd_pos(self, site="hold"):
        """今の関節指令のときの指の位置（計算上）"""
        d = self.arm.kd
        d.qpos[self.arm.qadr] = self.cmd
        mujoco.mj_kinematics(self.m, d)
        return d.site_xpos[self.arm.sites[site]].copy()

    def to(self, xyz, secs, correct=0, site="hold", bias=None):
        """指の狙い点を xyz へ。correct>0 なら、到着後に実際の位置を測って
        重力によるたわみ（位置制御の定常偏差）の分だけ目標をずらし、やり直す"""
        xyz = np.asarray(xyz, float)
        aim = xyz.copy() if bias is None else xyz + bias
        sid = self.arm.sites[site]
        q, ep, er = self.ik(aim, self.cmd, site)
        self.go(q, secs)
        for _ in range(correct):
            self.hold(0.15)
            aim = aim + (xyz - self.d.site_xpos[sid])
            aim = xyz + np.clip(aim - xyz, -0.005, 0.005)   # 補正は最大5mmまで（何かに当たって届かないときに暴れないように）
            q, ep, er = self.ik(aim, self.cmd, site)
            self.go(q, 0.25)
        self.bias = aim - xyz        # たわみの補正量（次の動きでも使う）
        return float(np.linalg.norm(xyz - self.d.site_xpos[sid]))

    def line(self, xyz, secs, site="hold", k=12):
        """指を直線に沿って動かす（関節空間で補間すると、途中で低く下がって駒に当たることがあるため）"""
        p0 = self.cmd_pos(site)
        p1 = np.asarray(xyz, float)
        qs, q = [], self.cmd.copy()
        for i in range(1, k + 1):
            s = i / k
            s = s * s * (3 - 2 * s)
            q, _, _ = self.ik(p0 + (p1 - p0) * s, q, site)
            qs.append(q)
        for q in qs:
            self.go_linear(q, secs / k)

    def set_grip(self, g, secs=0.4):
        g0 = self.grip
        n = int(secs / self.m.opt.timestep)
        for i in range(n):
            self.grip = g0 + (g - g0) * (i + 1) / n
            self.step()

    def hold(self, secs):
        self.step(int(secs / self.m.opt.timestep))

    def park(self, board_top, secs=1.2):
        """真上に上がってから、盤の横の待機姿勢へ（関節空間で動かす。指先は盤から9cmより下がらない）"""
        cur = self.cmd_pos()
        if cur[2] < board_top + PARK_LIFT:
            self.line([cur[0], cur[1], board_top + PARK_LIFT], 0.3)
        self.set_grip(GRIP_OPEN, 0.1)
        self.go(PARK, secs)

    def unpark(self, xy, board_top, secs=1.0, dz=0.09):
        """待機姿勢から、つかむ駒の上空（盤から dz の高さ）へ"""
        q, _, _ = self.arm.ik([xy[0], xy[1], board_top + dz], self.cmd, site="pick")
        self.go(q, secs)

    def is_parked(self):
        return bool(np.allclose(self.cmd, PARK, atol=1e-3))

    def pick_place(self, src, dst, skill, others_src=(), others_dst=()):
        """src/dst: 盤面上の (x, y, 盤の高さ)。skill: 動作のパラメータ（skill.py の Skill。事前学習で決まる）。
        others_src/dst: つかむ場所・置く場所の周りにある駒の xy（指が当たらない向きを選ぶのに使う）"""
        k = skill
        sx, sy, sz = src
        dx, dy, dz = dst
        safe_z = sz + k.safe_dz
        cur = self.cmd_pos()
        if cur[2] < safe_z - 0.005:
            self.line([cur[0], cur[1], safe_z], 0.5)               # まず真上に逃げる
        # つかむとき：狙い点 grip_pick は固定側の指から8.55mm。開いた間隔は gap(k.open)
        self.close, _ = choose_close(src, others_src, 0.00855, gap(k.open), feasible=self.feasible(src, "pick"))
        self.line([sx, sy, safe_z], k.carry_t, site="pick")
        self.set_grip(k.open, 0.3)
        # 全部の駒より高い位置で、たわみを測って補正してから、真下に降りる
        self.to([sx, sy, safe_z], 0.2, correct=2, site="pick")
        mid = sz + 0.035
        self.line(np.array([sx, sy, mid]) + self.bias, k.descend_t * 0.6, site="pick")
        self.to([sx, sy, mid], 0.2, correct=2, site="pick", bias=self.bias)     # 途中でもう一度たわみを測る
        self.line(np.array([sx, sy, sz + k.grasp_h]) + self.bias, k.descend_t, site="pick")
        self.to([sx, sy, sz + k.grasp_h], 0.3, correct=1, site="pick", bias=self.bias)
        self.set_grip(k.close, 0.5)
        self.hold(k.settle_t)
        self.line([sx, sy, safe_z], 0.7)
        # 置くとき：基準は挟んだ駒の中心（固定側から6.5mm）、離すときの間隔は gap(k.release)
        self.close, _ = choose_close(dst, others_dst, 0.0065, gap(k.release), feasible=self.feasible(dst, "hold"))
        self.line([dx, dy, safe_z], k.carry_t)
        self.to([dx, dy, safe_z], 0.2, correct=2)
        mid = dz + 0.035
        self.line(np.array([dx, dy, mid]) + self.bias, k.descend_t * 0.6)
        self.to([dx, dy, mid], 0.2, correct=2, bias=self.bias)
        self.line(np.array([dx, dy, dz + k.grasp_h]) + self.bias, k.descend_t)
        self.to([dx, dy, dz + k.grasp_h], 0.3, correct=1, bias=self.bias)
        self.set_grip(k.release, 0.4)
        self.hold(k.settle_t)
        self.line(np.array([dx, dy, safe_z]) + self.bias, 0.8)
        self.set_grip(k.open, 0.2)
