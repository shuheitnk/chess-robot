"""育てたデータ（~/.chess-robot）を GitHub のリポジトリと同期する

データのフォルダそのものを git リポジトリにして、
対局の前に取ってくる（pull）・対局の後に送る（push）だけの、簡単な仕組み。
どの PC で育てても、GitHub の脳がいちばん新しい状態になる。

2台の PC で同期せずに別々に育ててしまったとき（履歴が分かれたとき）は、
  ・brain.json  … 対局数の多いほうを残す（同じなら GitHub 側）
  ・history.jsonl / practice_cases.jsonl / night_log.jsonl … 両方の記録をまとめる
  ・skill.json  … GitHub 側を残す
で自動的にまとめる。
"""
import json
import subprocess

from .paths import DATA, ensure_data_dir

BRANCH = "main"
# 1行に1件の記録。2台で別々に増えたときは、両方の行をまとめる
JSONL = {"history.jsonl": "対局の記録", "practice_cases.jsonl": "夜の練習の教材（失敗した場面）",
         "night_log.jsonl": "夜の練習の記録"}


def _time_of(line):
    try:
        return json.loads(line).get("time", "")
    except ValueError:
        return ""


class SyncError(RuntimeError):
    pass


def _git(*args, check=True):
    r = subprocess.run(["git", "-C", str(DATA), *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        raise SyncError(f"git {' '.join(args)}: {r.stderr.strip() or r.stdout.strip()}")
    return r


def enabled():
    """データのフォルダが GitHub のリポジトリとつながっているか"""
    if not (DATA / ".git").exists():
        return False
    return _git("remote", "get-url", "origin", check=False).returncode == 0


def remote_url():
    return _git("remote", "get-url", "origin").stdout.strip() if enabled() else None


def _remote_has_branch():
    return _git("ls-remote", "--heads", "origin", BRANCH).stdout.strip() != ""


def _show(ref, name):
    r = _git("show", f"{ref}:{name}", check=False)
    return r.stdout if r.returncode == 0 else None


def _games(text):
    try:
        return int(json.loads(text).get("games", 0))
    except (ValueError, TypeError, AttributeError):
        return -1


def _merge_files(ours_ref, theirs_ref):
    """履歴が分かれたときに、2つの版の中身をまとめる。まとめた結果をフォルダに書き込む"""
    notes = []
    b_o, b_t = _show(ours_ref, "brain.json"), _show(theirs_ref, "brain.json")
    if b_o is not None or b_t is not None:
        if b_o is not None and (b_t is None or _games(b_o) > _games(b_t)):
            keep, who = b_o, "この PC"
        else:
            keep, who = b_t, "GitHub"
        (DATA / "brain.json").write_text(keep, encoding="utf-8")
        if b_o is None or b_t is None:
            notes.append(f"{who}の頭脳を使います")
        else:
            notes.append(f"頭脳は対局数の多い{who}の版を残しました（この PC {_games(b_o)} 局 / GitHub {_games(b_t)} 局）")

    for name, what in JSONL.items():
        h_o, h_t = _show(ours_ref, name) or "", _show(theirs_ref, name) or ""
        if h_o or h_t:
            seen, lines = set(), []
            for line in (h_t + "\n" + h_o).splitlines():
                if line.strip() and line not in seen:
                    seen.add(line)
                    lines.append(line)
            lines.sort(key=_time_of)
            (DATA / name).write_text("\n".join(lines) + "\n", encoding="utf-8")
            notes.append(f"{what}は両方をまとめました（{len(lines)} 件）")

    s_t = _show(theirs_ref, "skill.json")
    s_o = _show(ours_ref, "skill.json")
    if s_t is not None or s_o is not None:
        (DATA / "skill.json").write_text(s_t if s_t is not None else s_o, encoding="utf-8")
    return notes


def init(url):
    """データのフォルダを GitHub のリポジトリ（空でも、別の PC で育てた脳が入っていてもよい）とつなぐ"""
    ensure_data_dir()
    if not (DATA / ".git").exists():
        _git("init", "-q", "-b", BRANCH)
    if _git("remote", "get-url", "origin", check=False).returncode == 0:
        _git("remote", "set-url", "origin", url)
    else:
        _git("remote", "add", "origin", url)
    for key, default in (("user.name", "chess-robot"), ("user.email", "chess-robot@localhost")):
        if not _git("config", key, check=False).stdout.strip():      # git の名前が未設定の PC でもコミットできるように
            _git("config", key, default)

    notes = []
    if _remote_has_branch():
        _git("fetch", "-q", "origin", BRANCH)
        has_local_commit = _git("rev-parse", "--verify", "HEAD", check=False).returncode == 0
        local_files = [f.name for f in DATA.iterdir() if f.is_file()]
        if not has_local_commit and local_files:
            # この PC にもデータがある → いったん記録してから GitHub の脳とまとめる
            _git("add", "-A")
            _git("commit", "-q", "-m", "この PC のデータ")
            has_local_commit = True
        if has_local_commit:
            notes += pull()
        else:
            _git("checkout", "-q", "-B", BRANCH, f"origin/{BRANCH}")
            notes.append("GitHub の脳を取ってきました")
    notes += push("同期を開始")
    return notes


def pull():
    """GitHub の脳を取ってくる。この PC の変更と分かれていたら、まとめる"""
    if not enabled():
        return []
    if not _remote_has_branch():
        return []
    _git("fetch", "-q", "origin", BRANCH)
    theirs = f"origin/{BRANCH}"
    if _git("rev-parse", "--verify", "HEAD", check=False).returncode != 0:
        _git("checkout", "-q", "-B", BRANCH, theirs)
        return ["GitHub の脳を取ってきました"]
    commit_local("この PC の変更")
    head, remote = _git("rev-parse", "HEAD").stdout.strip(), _git("rev-parse", theirs).stdout.strip()
    if head == remote:
        return []
    if _git("merge-base", "--is-ancestor", "HEAD", theirs, check=False).returncode == 0:
        _git("merge", "-q", "--ff-only", theirs)          # GitHub のほうが新しい → そのまま進める
        return ["GitHub の新しい脳を取ってきました"]
    if _git("merge-base", "--is-ancestor", theirs, "HEAD", check=False).returncode == 0:
        return []                                          # この PC のほうが新しい（あとで push する）
    # 両方で別々に育った → 中身をまとめる
    _git("merge", "-q", "--no-commit", "-s", "ours", theirs, "--allow-unrelated-histories")
    notes = _merge_files("HEAD", theirs)
    _git("add", "-A")
    _git("commit", "-q", "-m", "2台の PC で育てた脳をまとめる")
    return notes


def commit_local(msg):
    _git("add", "-A")
    if _git("diff", "--cached", "--quiet", check=False).returncode != 0:
        _git("commit", "-q", "-m", msg)
        return True
    return False


def push(msg="対局の結果を保存"):
    """この PC の脳を GitHub に送る。先に誰かが更新していたら、取ってきてまとめてから送る"""
    if not enabled():
        return []
    commit_local(msg)
    if _git("rev-parse", "--verify", "HEAD", check=False).returncode != 0:
        return []
    for _ in range(3):
        r = _git("push", "-q", "-u", "origin", BRANCH, check=False)
        if r.returncode == 0:
            return []
        notes = pull()
        if notes:
            print("\n".join("[同期] " + n for n in notes))
    raise SyncError(r.stderr.strip())


def safe(fn, *args):
    """同期に失敗しても対局は止めない（オフラインのときなど）"""
    try:
        for n in fn(*args) or []:
            print("[同期]", n)
        return True
    except (SyncError, OSError) as e:
        print(f"[同期] GitHub と同期できませんでした（{e}）。この PC には保存されています")
        return False
