"""物理シミュレーションの中のチェス盤とロボット

・ロボットの手：アーム（SO-101）が駒をつかんで動かす（技能は ~/.chess-robot/skill.json）。
  指し終えたら盤の横の待機姿勢に戻る（真上のカメラと人の手の邪魔をしない）
・ユーザの手：見えない手が駒を持ち上げて置く（盤の上をふわっと移動するアニメーション）
・取られた駒は盤の横のトレイへ、キャスリングではルークも動かす
・ロボットが1回運ぶたびに結果を判定し、失敗（倒した・ずらした）したら、その場面を夜の練習の教材として記録する
・ロボットが指すたびに、ずれた駒・倒れた駒を正しいマスへ戻す
"""
import io

import chess
import mujoco
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import practice as P
from . import robot as R
from . import scene as B
from .skill import Skill

TOP_CAM = dict(distance=0.27, azimuth=180.0, elevation=-90.0)   # 盤の真上。上が1段目（ロボット側）、左が h 筋
AUTO_FIX_MM = 2.0          # ロボットが指したあと、これ以上ずれた駒は直す


class Game:
    def __init__(self, record=None, size=(1280, 720), skill=None, fps=30, log_cases=False):
        """record: 動画の保存先（None なら録画しない）。フレームはその場でファイルに書き出す
        log_cases: ロボットが運ぶのに失敗した場面を practice_cases.jsonl に記録する"""
        self.m = mujoco.MjModel.from_xml_path(str(B.SCENE))
        self.d = mujoco.MjData(self.m)
        self.arm = R.Arm(self.m, self.d)
        self.skill = skill or Skill.load()[0]
        self.names = [self.m.body(i).name for i in range(self.m.nbody)
                      if self.m.body(i).name[:1] in "wb" and "_" in self.m.body(i).name]
        self.qadr = {n: self.m.jnt_qposadr[self.m.joint(n).id] for n in self.names}
        self.vadr = {n: self.m.jnt_dofadr[self.m.joint(n).id] for n in self.names}
        self.jadr = [self.m.jnt_qposadr[self.m.joint(n).id] for n in R.JOINTS]
        self.log_cases = log_cases
        self.case_ctx = {}           # 記録に添える情報（何日目・何局目）
        self.caption = ""
        self.sub = ""
        self.record = bool(record)
        self.writer = None
        if record:
            import imageio
            self.writer = imageio.get_writer(str(record), fps=fps, quality=8, macro_block_size=8)
        self.viewer_sync = None
        self.on_frame = None         # 物理を進めている間に呼ばれる（画面へ映像を送るのに使う）
        self.top_r = None
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
        self.reset()

    def reset(self):
        """駒を初期配置に戻し、アームを待機姿勢にする（次の対局）"""
        mujoco.mj_resetData(self.m, self.d)          # 駒は XML に書いた初期のマスへ戻る
        self.d.qpos[self.arm.qadr] = R.PARK
        self.d.ctrl[:5] = R.PARK
        self.d.ctrl[5] = R.GRIP_OPEN
        for _ in range(300):
            mujoco.mj_step(self.m, self.d)
        self.board = chess.Board()
        self.at = {chess.square(int(n[2]), int(n[4])): n for n in self.names}   # マス → 駒の body 名
        self.tray_used = {True: 0, False: 0}
        self.mv = R.Mover(self.m, self.d, self.arm, self._frame)
        self.mv.cmd = R.PARK.copy()
        self.last_report = None
        if self.viewer_sync:
            self.viewer_sync()

    # ------------------------------------------------------------ 表示
    def _frame(self):
        if self.viewer_sync:
            self.viewer_sync()
        if self.on_frame:
            self.on_frame()
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

    def top_frame(self, size=360, quality=78):
        """盤を真上から写した画像（JPEG）。実機ではカメラの映像に置き換える"""
        buf = io.BytesIO()
        Image.fromarray(self.top_image(size)).save(buf, "JPEG", quality=quality)
        return buf.getvalue()

    def changed_squares(self, before, after, ratio=0.45):
        """真上の画像2枚を比べて、見た目が変わったマスを探す（盤の状態は使わない。実機のカメラでも同じ方法）"""
        g = self.top_grid()
        a = np.asarray(before, float).mean(axis=2)
        b = np.asarray(after, float).mean(axis=2)
        h, w = a.shape
        diff = {}
        for col in range(8):                     # 左から h → a
            for row in range(8):                 # 上から 1 → 8
                u0 = g["left"] + (g["right"] - g["left"]) * (col + 0.2) / 8
                u1 = g["left"] + (g["right"] - g["left"]) * (col + 0.8) / 8
                v0 = g["top"] + (g["bottom"] - g["top"]) * (row + 0.2) / 8
                v1 = g["top"] + (g["bottom"] - g["top"]) * (row + 0.8) / 8
                ys, xs = slice(int(v0 * h), int(v1 * h)), slice(int(u0 * w), int(u1 * w))
                diff["hgfedcba"[col] + str(row + 1)] = float(np.abs(a[ys, xs] - b[ys, xs]).mean())
        top = max(diff.values())
        if top < 4.0:                            # ほとんど変わっていない
            return []
        return sorted([s for s, v in diff.items() if v >= ratio * top], key=lambda s: -diff[s])

    def top_image(self, size=360):
        if self.top_r is None:
            self.top_r = mujoco.Renderer(self.m, size, size)
            self.top_cam = mujoco.MjvCamera()
            self.top_cam.lookat[:] = [B.BOARD_CX, 0.0, B.BOARD_TOP]
            self.top_cam.distance = TOP_CAM["distance"]
            self.top_cam.azimuth = TOP_CAM["azimuth"]
            self.top_cam.elevation = TOP_CAM["elevation"]
        self.top_r.update_scene(self.d, self.top_cam)
        return self.top_r.render().copy()

    def top_grid(self):
        """真上の画像の中で盤が写っている範囲（0〜1 の割合）。左＝h 筋の端、上＝1段目の端"""
        k = 1.0 / (2 * TOP_CAM["distance"] * np.tan(np.radians(self.m.vis.global_.fovy) / 2))
        half = 4 * B.S
        return dict(left=0.5 - half * k, right=0.5 + half * k,
                    top=0.5 + (B.X0 - B.BOARD_CX) * k, bottom=0.5 + (B.X0 + 8 * B.S - B.BOARD_CX) * k)

    def actuators(self):
        """6つの関節の角度 [°] と負荷（力の上限に対する割合）"""
        out = []
        for i, name in enumerate(R.JOINTS):
            lim = float(max(abs(self.m.actuator_forcerange[i][0]), abs(self.m.actuator_forcerange[i][1]))) or 1.0
            out.append(dict(name=name.upper(), deg=round(float(np.degrees(self.d.qpos[self.jadr[i]])), 1),
                            load=round(min(abs(float(self.d.actuator_force[i])) / lim, 1.0), 3)))
        out[-1]["state"] = "OPEN" if self.mv.grip > 0 else "CLOSED"
        return out

    def pieces_on_board(self):
        """盤の範囲に立っている駒の数（シミュレーションの位置から数える）"""
        n = 0
        for name in self.names:
            p = self.d.body(name).xpos
            if B.X0 <= p[0] <= B.X0 + 8 * B.S and abs(p[1]) <= 4 * B.S and p[2] > B.BOARD_TOP - 0.005:
                n += 1
        return n

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

    def square_of(self):
        """body 名 → マス名（盤の上の駒だけ）"""
        return {n: chess.square_name(sq) for sq, n in self.at.items()}

    # ------------------------------------------------------------ 駒を動かす
    def robot_carry(self, name, dst, from_sq, to_sq, carry, move_info):
        """アームで1つ運び、結果を判定する。失敗したら場面を記録する"""
        others = [n for n in self.at.values() if n != name]
        before = {n: self.d.body(n).xpos.copy() for n in [name, *others]}
        snap = P.layout(self.d, [name, *others])
        where = {**self.square_of(), name: chess.square_name(from_sq)}
        src = self.pos(name)
        t0 = self.d.time
        if self.mv.is_parked():
            self.mv.unpark(src, B.BOARD_TOP)
        self.mv.pick_place(src, dst, self.skill, self.others(name), self.others(name))
        self.mv.hold(0.25)
        res = P.assess(self.d, name, dst, before, others, dst_on_board=to_sq is not None,
                       duration=self.d.time - t0)
        res["piece"] = name
        res["to"] = chess.square_name(to_sq) if to_sq is not None else "tray"
        res["toppled_sq"] = [where.get(n, "?") for n in res["toppled"]]
        res["moved_sq"] = {where.get(n, "?"): v for n, v in res["moved_mm"].items()}
        if not res["success"] and self.log_cases:
            P.append_case(dict(
                id=f"{self.case_ctx.get('game', 0)}-{len(self.board.move_stack) + 1}-{carry}-{name}",
                **self.case_ctx, **move_info, carry=carry, piece=name,
                **{"from": chess.square_name(from_sq), "to": chess.square_name(to_sq) if to_sq is not None else "tray"},
                src=[round(float(src[0]), 5), round(float(src[1]), 5)],
                dst=[round(float(dst[0]), 5), round(float(dst[1]), 5)], dst_on_board=to_sq is not None,
                layout=snap, squares=where, result=res,
                skill={k: round(float(v), 5) for k, v in vars(self.skill).items()}))
        return res

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
        """move を盤上で実行する。by_robot=True ならアーム、False なら見えない手で動かす。
        ロボットの手なら、運んだ結果と直した駒の報告を返す"""
        b = self.board
        info = dict(ply=len(b.move_stack) + 1, move_no=b.fullmove_number, san=b.san(move), uci=move.uci())
        results = []

        def carry(name, dst, from_sq, to_sq, kind):
            if by_robot:
                results.append(self.robot_carry(name, dst, from_sq, to_sq, kind, info))
            else:
                self.hand_carry(name, dst)

        frm, to = move.from_square, move.to_square
        cap_sq = None
        if b.is_en_passant(move):
            cap_sq = chess.square(chess.square_file(to), chess.square_rank(frm))
        elif b.piece_at(to):
            cap_sq = to
        if cap_sq is not None:                         # 取る手：先に取られる駒をトレイへ
            victim = self.at.pop(cap_sq)
            carry(victim, self.tray_slot(victim[0] == "w"), cap_sq, None, "capture")
        name = self.at.pop(frm)
        carry(name, self.xy(to), frm, to, "move")
        self.at[to] = name
        if b.is_castling(move):                        # キャスリング：ルークも動かす
            rank = chess.square_rank(frm)
            rf, rt = ((chess.square(7, rank), chess.square(5, rank)) if chess.square_file(to) == 6
                      else (chess.square(0, rank), chess.square(3, rank)))
            rook = self.at.pop(rf)
            carry(rook, self.xy(rt), rf, rt, "castle")
            self.at[rt] = rook
        # 成り：シミュレーションでは駒の形はそのまま（盤の状態だけクイーン等になる）
        b.push(move)
        if not by_robot:
            return None
        self.mv.park(B.BOARD_TOP)
        self.hold(0.2)
        err, _ = self.check()
        fallen = self.fallen()
        fixed = self.fix(tol_mm=AUTO_FIX_MM)            # 毎手、ずれた駒・倒れた駒を正しいマスへ
        if fixed:
            self.hold(0.1)
        self.last_report = dict(carries=results, fallen=fallen, fixed=fixed, max_err_mm=round(err, 2),
                                failed=[r for r in results if not r["success"]])
        return self.last_report

    # ------------------------------------------------------------ 確認と直し
    def check(self):
        """盤の状態と、シミュレーションの駒の位置のずれ [mm] の最大と、そのマス"""
        worst, who = 0.0, ""
        for sq, name in self.at.items():
            e = float(np.hypot(*(self.d.body(name).xpos[:2] - self.xy(sq)[:2]))) * 1000
            if e > worst:
                worst, who = e, chess.square_name(sq)
        return worst, who

    def fallen(self):
        """倒れている盤上の駒のマス名の一覧（傾き：クォータニオンから出る上向きの軸、高さ：盤の面からの浮き沈み）"""
        return [chess.square_name(sq) for sq, name in self.at.items() if P.fallen(self.d, name)]

    def fix(self, tol_mm=AUTO_FIX_MM, everything=False):
        """ずれた駒・倒れた駒を正しいマスへ立て直す（実機なら人が直す）。直した数を返す。
        everything=True なら、ずれが小さくても全部の駒をマスの中心にまっすぐ置き直す"""
        n = 0
        for sq, name in self.at.items():
            off = np.hypot(*(self.d.body(name).xpos[:2] - self.xy(sq)[:2])) * 1000
            bad = off > tol_mm or P.fallen(self.d, name)
            if bad or everything:
                a, v = self.qadr[name], self.vadr[name]
                x, y, _ = self.xy(sq)
                self.d.qpos[a:a + 7] = [x, y, B.BOARD_TOP, 1, 0, 0, 0]
                self.d.qvel[v:v + 6] = 0
                n += bool(bad or off > 0.5)
        mujoco.mj_forward(self.m, self.d)
        if self.viewer_sync:
            self.viewer_sync()
        return n

    def close(self):
        if self.writer:
            self.writer.close()
            self.writer = None
