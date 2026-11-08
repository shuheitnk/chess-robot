"""SO-101 とチェス盤のシーン（MJCF）を作る

公開されている SO-101 のモデル（TheRobotStudio/SO-ARM100 の Simulation/SO101、Apache-2.0）に、
・指先の把持用パッド、重力補償、高さ3cmの台
・スタントン型の駒32個（光沢のある象牙色と黒檀色。質量・摩擦あり）
・つやのある木目の盤、取った駒のトレイ、照明
を足した so101_chess.xml を、モデルと同じフォルダに書き出す。
"""
import re
from pathlib import Path

from . import pieces as P

ROOT = Path(__file__).resolve().parent.parent
MODEL_DIR = ROOT / "third_party" / "SO-ARM100" / "Simulation" / "SO101"
SCENE = MODEL_DIR / "so101_chess.xml"

S = 0.022                 # マスの一辺 [m]
BOARD_CX = 0.198          # 盤の中心（ロボットの正面方向 x）
BOARD_TOP = 0.012         # 盤面の高さ
PEDESTAL = 0.03           # アームの台の高さ
X0 = BOARD_CX - 4 * S     # 1段目（ロボット側）の手前の端
PIECE_R = P.BASE_R        # 駒の台座の半径（6.5mm）
MASS = 0.008
TRAY_Y = 0.15             # 取った駒のトレイ（白の駒は -y 側、黒の駒は +y 側）

START = ["rnbqkbnr", "pppppppp"]


def square_xy(file, rank):
    """file 0..7 (a..h), rank 0..7 (1..8) → 盤面上の座標。ロボットが白番で、a筋はロボットから見て左"""
    return X0 + (rank + 0.5) * S, (3.5 - file) * S


MATERIALS = """
    <texture name="sky" type="skybox" builtin="gradient" rgb1="0.10 0.11 0.13" rgb2="0.01 0.01 0.015" width="512" height="512"/>
    <texture name="floor" type="2d" builtin="flat" rgb1="0.09 0.09 0.10" width="64" height="64"/>
    <material name="floor" texture="floor" reflectance="0.12" shininess="0.6" specular="0.3"/>
    <material name="ivory" rgba="0.94 0.90 0.81 1" specular="0.75" shininess="0.92" reflectance="0.04"/>
    <material name="ebony" rgba="0.075 0.068 0.072 1" specular="1.0" shininess="0.98" reflectance="0.08"/>
    <material name="maple" rgba="0.80 0.68 0.50 1" specular="0.5" shininess="0.85" reflectance="0.10"/>
    <material name="walnut" rgba="0.33 0.21 0.13 1" specular="0.5" shininess="0.85" reflectance="0.10"/>
    <material name="frame" rgba="0.16 0.10 0.07 1" specular="0.6" shininess="0.9" reflectance="0.06"/>
    <material name="brass" rgba="0.78 0.62 0.32 1" specular="1.0" shininess="0.95" reflectance="0.2"/>
    <material name="felt" rgba="0.12 0.13 0.14 1" specular="0.05" shininess="0.1"/>
    <material name="arm_white" rgba="0.93 0.93 0.91 1" specular="0.35" shininess="0.6"/>
    <material name="arm_dark" rgba="0.11 0.11 0.12 1" specular="0.5" shininess="0.7"/>
    <material name="rubber" rgba="0.08 0.08 0.08 1" specular="0.1" shininess="0.1"/>
"""


def piece_body(name, kind, white, x, y):
    mat = "ivory" if white else "ebony"
    h = P.HEIGHT[kind]
    col = ('contype="2" conaffinity="2" condim="4" friction="1.2 0.01 0.001" '
           'solref="0.004 1" solimp="0.95 0.99 0.001" group="3" rgba="0 0 0 0"')
    geoms = [
        # 衝突形状：つかむ台座（半径6.5mm）と、上の細い胴体
        f'<geom type="cylinder" size="{PIECE_R} {P.BASE_H / 2:.4f}" pos="0 0 {P.BASE_H / 2:.4f}" mass="{MASS * 0.7:.4f}" {col}/>',
        f'<geom type="cylinder" size="0.0045 {(h - P.BASE_H) / 2:.4f}" pos="0 0 {(P.BASE_H + h) / 2:.4f}" mass="{MASS * 0.3:.4f}" {col}/>',
        # 見た目：回転体のメッシュ
        f'<geom type="mesh" mesh="chess_{kind}" material="{mat}" contype="0" conaffinity="0" mass="0"/>',
    ]
    if kind == "n":
        yaw = 0 if white else 180                      # 馬の顔を相手側に向ける
        geoms.append(f'<geom type="mesh" mesh="chess_n_head" material="{mat}" euler="0 0 {yaw}" '
                     f'contype="0" conaffinity="0" mass="0"/>')
    if kind == "k":
        top = 0.0344
        geoms.append(f'<geom type="box" size="0.0009 0.0009 0.0032" pos="0 0 {top + 0.0028:.4f}" material="{mat}" contype="0" conaffinity="0" mass="0"/>')
        geoms.append(f'<geom type="box" size="0.0024 0.0009 0.0009" pos="0 0 {top + 0.0036:.4f}" material="{mat}" contype="0" conaffinity="0" mass="0"/>')
    return (f'<body name="{name}" pos="{x:.4f} {y:.4f} {BOARD_TOP:.4f}">'
            f'<freejoint name="{name}"/>{"".join(geoms)}</body>')


def board_xml():
    g = []
    half = 4 * S
    # 盤の枠（黒っぽい木）と、マスの周りの真鍮の細い線
    g.append(f'<geom name="board" type="box" size="{half + 0.014:.4f} {half + 0.014:.4f} {BOARD_TOP / 2:.4f}" '
             f'pos="{BOARD_CX:.4f} 0 {BOARD_TOP / 2:.4f}" material="frame" '
             f'contype="2" conaffinity="2" condim="4" friction="1.0 0.01 0.001"/>')
    g.append(f'<geom type="box" size="{half + 0.0022:.4f} {half + 0.0022:.4f} 0.0002" '
             f'pos="{BOARD_CX:.4f} 0 {BOARD_TOP + 0.00005:.5f}" material="brass" contype="0" conaffinity="0"/>')
    for f in range(8):
        for r in range(8):
            x, y = square_xy(f, r)
            mat = "maple" if (f + r) % 2 == 1 else "walnut"
            g.append(f'<geom type="box" size="{S / 2:.4f} {S / 2:.4f} 0.0002" '
                     f'pos="{x:.4f} {y:.4f} {BOARD_TOP + 0.0001:.4f}" material="{mat}" contype="0" conaffinity="0"/>')
    # 取った駒を置くトレイ（フェルト張り）
    for side, yy in (("w", -TRAY_Y), ("b", TRAY_Y)):
        g.append(f'<geom name="tray_{side}" type="box" size="0.062 0.026 {BOARD_TOP / 2:.4f}" '
                 f'pos="{BOARD_CX - 0.005:.4f} {yy + (-0.011 if side == "w" else 0.011):.4f} {BOARD_TOP / 2:.4f}" material="frame" '
                 f'contype="2" conaffinity="2" condim="4"/>')
    return "".join(g)


def pieces_xml():
    out = []
    for rank, row in ((0, START[0]), (1, START[1])):         # 白（ロボット側）
        for f, k in enumerate(row):
            x, y = square_xy(f, rank)
            out.append(piece_body(f"w{k}{f}_{rank}", k, True, x, y))
    for rank, row in ((7, START[0]), (6, START[1])):         # 黒
        for f, k in enumerate(row):
            x, y = square_xy(f, rank)
            out.append(piece_body(f"b{k}{f}_{rank}", k, False, x, y))
    return "".join(out)


def build():
    if not (MODEL_DIR / "so101_new_calib.xml").exists():
        raise SystemExit("SO-101 のモデルがありません。先に `uv run python scripts/setup_model.py` を実行してください。")
    files = P.build_meshes(MODEL_DIR / "assets")
    arm = (MODEL_DIR / "so101_new_calib.xml").read_text()

    # 指先のパッド（把持はこれで行う。駒とだけ衝突する contype=2）
    pad = ('type="box" size="0.010 0.005 0.002" material="rubber" contype="2" conaffinity="2" condim="4" '
           'friction="1.6 0.02 0.002" solref="0.004 1" solimp="0.95 0.99 0.001" group="1"')
    fixed_pad = f'<geom name="pad_fixed" {pad} pos="-0.0099 -0.00022 -0.08813" quat="0.70711 0 0.70711 0"/>'
    moving_pad = f'<geom name="pad_moving" {pad} pos="-0.0103 -0.06473 0.01902" quat="-0.5 0.5 -0.5 0.5"/>'
    # grip_hold: 挟んだ駒の中心（固定側の指から6.5mm＝駒の半径）。grip_pick: つかみに行くときの狙い点（開いた指の真ん中）
    sites = ('<site name="grip_hold" pos="-0.0014 -0.00022 -0.09413" quat="0.707107 0 0.707107 0" size="0.002" group="3"/>\n'
             '<site name="grip_pick" pos="0.00065 -0.00022 -0.09413" quat="0.707107 0 0.707107 0" size="0.002" group="3"/>')
    i = arm.index('<site group="3" name="gripperframe"')
    arm = arm[:i] + fixed_pad + "\n" + sites + "\n" + arm[i:]
    j = arm.index('<joint axis="0 0 1" name="gripper"')
    j = arm.index("/>", j) + 2
    arm = arm[:j] + "\n" + moving_pad + arm[j:]

    # 指の力を弱める（実機でもトルク上限レジスタで設定できる）
    arm = arm.replace('name="gripper" joint="gripper" forcerange="-3.35 3.35"',
                      'name="gripper" joint="gripper" forcerange="-0.8 0.8"')
    arm = arm.replace('<mujoco model="so101_new_calib">', '<mujoco model="so101_chess">')
    # 重力補償（位置制御が重力でたわむのを防ぐ）と、高さ3cmの台
    arm = re.sub(r'<body name="([^"]+)"', r'<body name="\1" gravcomp="1"', arm)
    arm = arm.replace('<body name="base" gravcomp="1" pos="0 0 0"', f'<body name="base" gravcomp="1" pos="0 0 {PEDESTAL}"')
    # アームの配色：白い外装と黒いサーボ
    arm = re.sub(r'(<material name="sts3215[^"]*_material") rgba="[^"]*"', r'\1 rgba="0.11 0.11 0.12 1" specular="0.5" shininess="0.7"', arm)
    arm = re.sub(r'(<material name="(?!sts3215)[^"]*_material") rgba="[^"]*"', r'\1 rgba="0.93 0.93 0.91 1" specular="0.35" shininess="0.6"', arm)

    meshes = "".join(f'<mesh name="chess_{k}" file="{fn}"/>' for k, fn in files.items()
                     if k != "n_head")
    meshes += f'<mesh name="chess_n_head" file="{files["n_head"]}"/>'
    extra = f"""
  <option timestep="0.002" cone="elliptic" impratio="10"/>
  <visual>
    <headlight diffuse="0.25 0.25 0.25" ambient="0.22 0.22 0.24" specular="0.1 0.1 0.1"/>
    <global offwidth="1920" offheight="1080"/>
    <quality shadowsize="8192"/>
    <map znear="0.005"/>
  </visual>
  <asset>{MATERIALS}{meshes}</asset>
  <contact>
    <!-- 凸包で近似した衝突形状は実物より太いので、折りたたんだときの見かけ上の自己衝突は除外する -->
    <exclude body1="shoulder" body2="gripper"/>
    <exclude body1="shoulder" body2="wrist"/>
    <exclude body1="shoulder" body2="moving_jaw_so101_v1"/>
    <exclude body1="upper_arm" body2="gripper"/>
    <exclude body1="upper_arm" body2="wrist"/>
    <exclude body1="upper_arm" body2="moving_jaw_so101_v1"/>
    <exclude body1="base" body2="gripper"/>
    <exclude body1="base" body2="moving_jaw_so101_v1"/>
  </contact>
  <worldbody>
    <light name="key" pos="0.45 -0.35 0.75" dir="-0.45 0.35 -0.75" diffuse="0.85 0.82 0.78" specular="0.6 0.6 0.6" castshadow="true" cutoff="45" exponent="2"/>
    <light name="rim" pos="0.1 0.5 0.5" dir="0.1 -0.6 -0.5" diffuse="0.35 0.38 0.45" specular="0.4 0.4 0.5" castshadow="false"/>
    <light name="fill" pos="-0.3 -0.2 0.6" dir="0.4 0.2 -0.6" diffuse="0.18 0.18 0.2" specular="0 0 0" castshadow="false"/>
    <geom name="table" type="plane" size="2 2 0.01" material="floor" contype="3" conaffinity="3"/>
    <geom name="pedestal" type="cylinder" size="0.055 {PEDESTAL / 2}" pos="0 0 {PEDESTAL / 2}" material="arm_dark" contype="0" conaffinity="0"/>
    {board_xml()}
    {pieces_xml()}
  </worldbody>
"""
    k = arm.rindex("</mujoco>")
    arm = arm[:k] + extra + arm[k:]
    SCENE.write_text(arm)
    return SCENE


if __name__ == "__main__":
    print(build())
