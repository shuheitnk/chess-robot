"""SO-101 の公開シミュレーションモデルを取ってきて、チェスのシーンを作る

TheRobotStudio/SO-ARM100（Apache-2.0）の Simulation フォルダだけを third_party/ に取得し、
chess_robot/scene.py で駒・盤・照明を足した third_party/SO-ARM100/Simulation/SO101/so101_chess.xml を作る。
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DEST = ROOT / "third_party" / "SO-ARM100"


def main():
    if not (DEST / "Simulation" / "SO101" / "so101_new_calib.xml").exists():
        DEST.parent.mkdir(exist_ok=True)
        subprocess.run(["git", "clone", "--depth", "1", "--filter=blob:none", "--sparse",
                        "https://github.com/TheRobotStudio/SO-ARM100.git", str(DEST)], check=True)
        subprocess.run(["git", "-C", str(DEST), "sparse-checkout", "set", "Simulation"], check=True)
    from chess_robot import scene
    print("シーンを作りました:", scene.build())


if __name__ == "__main__":
    main()
