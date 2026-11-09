"""学習したデータ（技能・頭脳・対局の記録）の保存先

リポジトリを消したりクローンし直したりしても育てた頭脳が消えないように、
リポジトリの外（ホームフォルダの ~/.chess-robot）に保存する。
環境変数 CHESS_ROBOT_DATA を設定すれば、別の場所（Google Drive の同期フォルダなど）も使える。
"""
import os
import shutil
from pathlib import Path

from .scene import ROOT

DATA = Path(os.environ.get("CHESS_ROBOT_DATA", Path.home() / ".chess-robot")).expanduser()
_OLD = ROOT / "data"          # 以前の保存先（リポジトリの中）


def ensure_data_dir():
    """保存先を作る。以前リポジトリの中（data/）に保存していたファイルがあれば、初回だけ移す"""
    DATA.mkdir(parents=True, exist_ok=True)
    if _OLD.is_dir() and _OLD.resolve() != DATA.resolve():
        for f in _OLD.iterdir():
            if f.is_file() and not (DATA / f.name).exists():
                shutil.copy2(f, DATA / f.name)
                print(f"[chess-robot] {f.name} を {DATA} に移しました")
    return DATA


ensure_data_dir()
