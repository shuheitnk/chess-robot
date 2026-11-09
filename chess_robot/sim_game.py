"""物理シミュレーションの中のチェス盤とロボット

・ロボットの手：アーム（SO-101）が駒をつかんで動かす（技能は ~/.chess-robot/skill.json）
・ユーザの手：見えない手が駒を持ち上げて置く（盤の上をふわっと移動するアニメーション）
・取られた駒は盤の横のトレイへ、キャスリングではルークも動かす
"""
import chess
import mujoco
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import robot as R
from . import scene as B
from .skill import Skill


class Game:
    def __init__(self, record=None, size=(1280, 720), skill=None, fps=30):
        """record: 動画の保存先（None なら録画しない）。フレームはその場でファイルに書き出す"""
        self.m = mujoco.MjModel.from_xml_path(str(B.SCENE))
        self.d = mujoco.MjData(self.m)
        self.arm = R.Arm(self.m, self.d)
        self.skill = skill or Skill.load()[0]
        q, _, _ = self.arm.ik([0.2, 0.0, 0.13], [0, -1, 1, 1, 0])
        self.d.qpos[self.arm.qadr] = q
        self.d.ctrl[:5] = q
        self.d.ctrl[5] = R.GRIP_OPEN
        for _ in range(300):
            mujoco.mj_step(self.m, self.d)

        self.board = chess.Board()
        self.names = [self.m.body(i).name for i in range(self.m.nbody)
                      if self.m.body(i).name[:1] in "wb" and "_" in self.m.body(i).name]
        self.at = {chess.square(int(n[2]), int(n[4])): n for n in self.names}   # マス → 駒の body 名
        self.qadr = {n: self.m.jnt_qposadr[self.m.joint(n).id] for n in self.names}
        self.vadr = {n: self.m.jnt_dofadr[self.m.joint(n).id] for n in self.names}
        self.tray_used = {True: 0, False: 0}
        self.caption = ""
        self.sub = ""
        self.record = bool(record)
        self.writer = None
        if record:
            import imageio
            self.writer = imageio.get_writer(str(record), fps=fps, quality=8, macro_block_size=8)
        self.viewer_sync = None
        if record:
            self.r = mujoco.Renderer(self.m, size[1], size[0])
            self.cam = mujoco.MjvCamera()
            self.cam.lookat[:] = [0.19, 0.0, 0.03]
            self.cam.distance = 0.58
            self.cam.azimuth = 212
            self.cam.elevation = -30
            try:
                self.font = ImageFont.truetype("DejaVuSans-Bold.ttf", 30)
                self.font_s = ImageFont.truetype("DejaVuSans.ttf", 20)
            except OSError:
                self.font = self.font_s = ImageFont.load_default()
        self.mv = R.Mover(self.m, self.d, self.arm, self._frame)

    # ------------------------------------------------------------ 表示
    def _frame(self):
        if self.viewer_sync:
            self.viewer_sync()
        if not self.record:
            return
        self.cam.azimuth += 0.03                       # ゆっくり回り込むカメラ
        self.r.update_scene(self.d, self.cam)
        img = Image.fromarray(self.r.render())
        if self.caption:
            dr = ImageDraw.Draw(img)
            dr.text((40, 34), self.caption, fill=(240, 236, 228), font=self.font)
            if self.sub:
                dr.text((42, 74), self.sub, fill=(170, 170, 175), font=self.font_s)
        self.writer.append_data(np.asarray(img))

    def hold(self, secs):
        self.mv.hold(secs)

    # ------------------------------------------------------------ 位置
    def xy(self, sq):
        x, y = B.square_xy(chess.square_file(sq), chess.square_rank(sq))
        return np.array([x, y, B.BOARD_TOP])

    def pos(self, name):
        p = self.d.body(name).xpos
        return np.array([p[0], p[1], B.BOARD_TOP])

    def others(self, exclude):
        """今の駒の実際の位置（実機ならカメラで見る情報）"""
        return [self.d.body(n).xpos[:2].copy() for n in self.names if n != exclude]

    def tray_slot(self, white_piece):
        i = self.tray_used[white_piece]
        self.tray_used[white_piece] += 1
        row, col = divmod(i, 5)
        x = B.BOARD_CX - 0.050 + col * 0.024
        y = (-1 if white_piece else 1) * (B.TRAY_Y + row * 0.022)
        return np.array([x, y, B.BOARD_TOP])

    # ------------------------------------------------------------ 駒を動かす
    def robot_carry(self, name, dst):
        self.mv.pick_place(self.pos(name), dst, self.skill, self.others(name), self.others(name))

    def hand_carry(self, name, dst, secs=1.2):
        """ユーザの手：駒を持ち上げて弧を描いて運ぶ（物理ではなく位置を直接動かす）"""
        a, v = self.qadr[name], self.vadr[name]
        p0 = self.d.qpos[a:a + 3].copy()
        quat = self.d.qpos[a + 3:a + 7].copy()
        p1 = np.array([dst[0], dst[1], B.BOARD_TOP])
        n = int(secs / self.m.opt.timestep)
        for i in range(n):
            s = (i + 1) / n
            s = s * s * (3 - 2 * s)
            p = p0 + (p1 - p0) * s
            p[2] = p0[2] + (p1[2] - p0[2]) * s + 0.05 * np.sin(np.pi * s)
            self.d.qpos[a:a + 3] = p
            self.d.qpos[a + 3:a + 7] = quat
            self.d.qvel[v:v + 6] = 0
            self.mv.step()

    def play(self, move, by_robot):
        """move を盤上で実行する。by_robot=True ならアーム、False なら見えない手で動かす"""
        carry = self.robot_carry if by_robot else self.hand_carry
        b = self.board
        frm, to = move.from_square, move.to_square
        cap_sq = None
        if b.is_en_passant(move):
            cap_sq = chess.square(chess.square_file(to), chess.square_rank(frm))
        elif b.piece_at(to):
            cap_sq = to
        if cap_sq is not None:                         # 取る手：先に取られる駒をトレイへ
            victim = self.at.pop(cap_sq)
            carry(victim, self.tray_slot(victim[0] == "w"))
        name = self.at.pop(frm)
        carry(name, self.xy(to))
        self.at[to] = name
        if b.is_castling(move):                        # キャスリング：ルークも動かす
            rank = chess.square_rank(frm)
            rf, rt = ((chess.square(7, rank), chess.square(5, rank)) if chess.square_file(to) == 6
                      else (chess.square(0, rank), chess.square(3, rank)))
            rook = self.at.pop(rf)
            carry(rook, self.xy(rt))
            self.at[rt] = rook
        # 成り：シミュレーションでは駒の形はそのまま（盤の状態だけクイーン等になる）
        b.push(move)

    def check(self):
        """盤の状態と、シミュレーションの駒の位置のずれ [mm] の最大と、そのマス"""
        worst, who = 0.0, ""
        for sq, name in self.at.items():
            e = float(np.hypot(*(self.d.body(name).xpos[:2] - self.xy(sq)[:2]))) * 1000
            if e > worst:
                worst, who = e, chess.square_name(sq)
        return worst, who

    def fallen(self, max_tilt_deg=10.0):
        """倒れている（傾いている）盤上の駒のマス名の一覧"""
        c = np.cos(np.radians(max_tilt_deg))
        return [chess.square_name(sq) for sq, name in self.at.items()
                if self.d.body(name).xmat[8] < c]        # 駒の上向きの軸と真上のなす角

    def fix(self, tol_mm=4.0, max_tilt_deg=10.0):
        """ずれた駒・倒れた駒を正しいマスへ立て直す（実機なら人が直す）。直した数を返す"""
        c = np.cos(np.radians(max_tilt_deg))
        n = 0
        for sq, name in self.at.items():
            if np.hypot(*(self.d.body(name).xpos[:2] - self.xy(sq)[:2])) * 1000 > tol_mm \
                    or abs(self.d.body(name).xpos[2] - B.BOARD_TOP) > 0.002 \
                    or self.d.body(name).xmat[8] < c:
                a, v = self.qadr[name], self.vadr[name]
                x, y, _ = self.xy(sq)
                self.d.qpos[a:a + 7] = [x, y, B.BOARD_TOP, 1, 0, 0, 0]
                self.d.qvel[v:v + 6] = 0
                n += 1
        if n:
            mujoco.mj_forward(self.m, self.d)
        return n

    def close(self):
        if self.writer:
            self.writer.close()
            self.writer = None
