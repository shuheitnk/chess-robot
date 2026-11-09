"""朝のレポート（画面3）のデータ

history.jsonl（対局）・night_log.jsonl（夜の練習）・brain.json（頭脳）・skill.json（技能）だけから作る。
記録がないところは None（画面では "—"）にして、作り話の数字は出さない。
"""
import datetime as dt
import json

import chess
import numpy as np

from . import brain as Bn
from .paths import DATA


def _jsonl(path):
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out


def _mean(xs):
    return round(float(np.mean(xs)), 2) if xs else None


def _when(rec):
    try:
        return dt.datetime.fromisoformat(rec["time"])
    except (KeyError, ValueError, TypeError):
        return None


RESULT_TEXT = {"0-1": "You won", "1-0": "I won", "1/2-1/2": "Draw", "*": "Move limit"}


def _result_text(rec):
    if rec.get("end") == "resign":
        return "You resigned"
    r = rec.get("result", "")
    return RESULT_TEXT.get(r, r or "—")


def _verb(name, before, after):
    """重みの変化を一人称の言葉に"""
    low = name.lower()
    if after < 0 and after < before:
        return f"to avoid {low}" if name in ("Hanging pieces",) else f"to value {low} less"
    if after > before:
        return f"to value {'my ' + low if name in ('Queen', 'Rook', 'Knight', 'Bishop', 'Pawn') else low} more"
    return f"to value {low} less"


def morning():
    now = dt.datetime.now()
    brain = Bn.Brain()
    hist = sorted(_jsonl(Bn.HISTORY_FILE), key=lambda r: r.get("time", ""))
    nights = sorted(_jsonl(DATA / "night_log.jsonl"), key=lambda r: r.get("time", ""))
    names = Bn.FEATURES_EN
    w_now = np.array(brain.s["w"], float)
    born = dt.date.fromisoformat(brain.s["born"])

    # ---- 数字4つ
    today = now.date()
    yesterday = today - dt.timedelta(days=1)
    games_yesterday = sum(1 for r in hist if _when(r) and _when(r).date() == yesterday)
    rewards = [r["reward"] for r in hist if "reward" in r]
    week_ago = now - dt.timedelta(days=7)
    old = [r for r in hist if _when(r) and _when(r) <= week_ago]
    reward_week = _mean([r["reward"] for r in old[-10:]]) if old else None

    night = nights[-1] if nights else None
    skill_info = _jsonl_one(DATA / "skill.json")
    if night and night.get("test"):
        cur = night["test"]["after"] if night.get("adopted") else night["test"]["before"]
        skill = dict(sr=cur["sr"], before=night["test"]["before"]["sr"], after=night["test"]["after"]["sr"],
                     adopted=night.get("adopted"), source="night")
        speed = dict(time_s=cur["time_s"],
                     delta=round(night["test"]["after"]["time_s"] - night["test"]["before"]["time_s"], 2)
                     if night.get("adopted") else None)
    else:
        tasks = (skill_info or {}).get("final_tasks") or []
        skill = dict(sr=(skill_info or {}).get("final_success"), before=None, after=None, adopted=None, source="pretrain")
        speed = dict(time_s=_mean([t["time_s"] for t in tasks if "time_s" in t]), delta=None)

    # ---- 日ごとの平均報酬
    by_day = {}
    for r in hist:
        if "day" in r and "reward" in r:
            by_day.setdefault(int(r["day"]), []).append(r["reward"])
    daily = [dict(day=d, avg=_mean(v), n=len(v)) for d, v in sorted(by_day.items())]

    # ---- 1週間前（なければ生まれたとき）と今の重み
    if old:
        w_old, old_label = np.array(old[-1]["w"], float), "A week ago"
    elif (today - born).days >= 7:
        w_old, old_label = np.zeros(len(names)), "A week ago"
    else:
        w_old, old_label = np.zeros(len(names)), "At birth"
    order = np.argsort(-np.maximum(np.abs(w_now), np.abs(w_old)))[:6]
    values = [dict(name=names[i], old=round(float(w_old[i]), 3), now=round(float(w_now[i]), 3)) for i in order]

    # ---- いちばん学んだ対局（重みがいちばん動いた局）
    lesson = None
    prev = np.zeros(len(names))
    best = 0.0
    for k, r in enumerate(hist):
        w = np.array(r.get("w", prev), float)
        d = w - prev
        if np.abs(d).max() > best + 1e-9:
            best = float(np.abs(d).max())
            i = int(np.argmax(np.abs(d)))
            partner = hist[k - 1] if k > 0 and hist[k - 1].get("pair") == 1 else None
            lesson = dict(game=r.get("game"), day=r.get("day"), result=_result_text(r), reward=r.get("reward"),
                          feature=names[i], before=round(float(prev[i]), 2), after=round(float(w[i]), 2),
                          partner_reward=partner.get("reward") if partner else None,
                          board=_final_board(r.get("moves", "")))
        prev = w
    if lesson:
        t = (f"Game {lesson['game']} ({lesson['result'].lower()}, reward {lesson['reward']:+d}) closed a learning pair "
             f"and moved my values the most: “{lesson['feature']}” went from {lesson['before']:+.2f} to {lesson['after']:+.2f}.")
        if lesson["partner_reward"] is not None:
            t += (f" Its partner game scored {lesson['partner_reward']:+d}, so I leaned toward whichever nudge scored better.")
        lesson["text"] = t

    # ---- 成長の記録（記録から自動で拾う）
    timeline = _timeline(brain, hist, nights, names, born)

    # ---- KOMA のひとこと
    head, body = _greeting(night, w_now, w_old, old_label, names, hist, now)

    return dict(now=now.isoformat(timespec="minutes"), day=brain.day(), games=brain.s["games"],
                games_yesterday=games_yesterday,
                reward_last10=_mean(rewards[-10:]), reward_week_ago=reward_week,
                skill=skill, speed=speed, daily=daily, values=values, old_label=old_label,
                lesson=lesson, timeline=timeline, headline=head, body=body,
                night=dict(time=night.get("time"), adopted=night.get("adopted"), n_cases=night.get("n_cases")) if night else None,
                synced=_synced())


def _jsonl_one(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _synced():
    from . import sync
    return bool(sync.enabled())


def _final_board(moves):
    b = chess.Board()
    for u in moves.split():
        try:
            b.push_uci(u)
        except ValueError:
            break
    return {chess.square_name(s): p.symbol() for s, p in b.piece_map().items()}


def _greeting(night, w_now, w_old, old_label, names, hist, now):
    head = "Good morning."
    parts = []
    if night and night.get("test"):
        t0, t1 = night["test"]["before"], night["test"]["after"]
        when = _when(night)
        ago = "Last night" if when and (now.date() - when.date()).days <= 1 else f"On {when:%b %d}" if when else "Last time"
        n = night.get("n_cases") or 0
        what = f"the {n} moment{'s' if n != 1 else ''} that went wrong in our games" if n else "on random boards"
        if night.get("adopted"):
            better = []
            if t1["falls"] < t0["falls"]:
                better.append("I topple fewer pieces now")
            if t1["err_mm"] < t0["err_mm"] - 0.05:
                better.append("I place pieces more precisely")
            if t1["time_s"] < t0["time_s"] - 0.05:
                better.append("each move is a bit faster")
            if t1["sr"] > t0["sr"]:
                better.insert(0, f"my success rate on new tasks went from {t0['sr']:.0%} to {t1['sr']:.0%}")
            steadier = t1["sr"] > t0["sr"] or t1["falls"] < t0["falls"] or t1["err_mm"] < t0["err_mm"] - 0.05
            head2 = "Overnight, my hands got a little steadier." if steadier else "Overnight, my hands got a little quicker."
            s = ", ".join(better[:-1]) + (" and " if len(better) > 1 else "") + better[-1] if better else ""
            parts.append(f"{ago} I practiced {what}. " + (s[0].upper() + s[1:] + "." if s else ""))
        else:
            head2 = "I practiced overnight, but kept yesterday’s hands."
            parts.append(f"{ago} I practiced {what}, but the new skill wasn’t better on fresh test tasks, so I kept my old one.")
    else:
        head2 = "Ready for today’s games."
        parts.append("I haven’t practiced at night yet.")
    d = w_now - w_old
    if hist and np.abs(d).max() > 1e-9:
        i = int(np.argmax(np.abs(d)))
        since = "this week" if old_label == "A week ago" else "since I was born"
        parts.append(f"In chess, I’ve learned {_verb(names[i], w_old[i], w_now[i])} {since}.")
    elif hist:
        parts.append("In chess, my values haven’t moved yet — they change after each pair of games.")
    else:
        parts.append("In chess, I still know nothing — every game we play teaches me.")
    return head + " " + head2, " ".join(p for p in parts if p)


def _timeline(brain, hist, nights, names, born):
    ev = [dict(day=1, time=born.isoformat(), text="Born. Knew nothing — not even piece values — and played random legal moves.")]
    first = {}

    def once(key, rec, text):
        if key not in first:
            first[key] = True
            ev.append(dict(day=rec.get("day"), time=rec.get("time", ""), text=text))

    top = None
    by_day = {}
    for r in hist:
        if "reward" not in r:
            continue
        once("game", r, "Played our first game.")
        if r.get("updated"):
            once("update", r, "My values changed for the first time.")
        if r["reward"] > 0:
            once("reward", r, f"First game where I captured more than I lost (reward {r['reward']:+d}).")
        if r.get("result") == "1-0" and r.get("end") == "checkmate":
            once("win", r, "Checkmated you for the first time.")
        by_day.setdefault(r.get("day"), []).append(r)
        w = np.array(r.get("w", []), float)
        if w.size and np.abs(w).max() > 1e-9:
            t = int(np.argmax(np.abs(w)))
            if t != top:
                top = t
                sign = "value" if w[t] > 0 else "avoid"
                ev.append(dict(day=r.get("day"), time=r.get("time", ""),
                               text=f"Started to {sign} “{names[t].lower()}” most."))
    for d, rs in sorted(by_day.items(), key=lambda kv: kv[0] or 0):
        if np.mean([r["reward"] for r in rs]) > 0:
            once("posday", rs[-1], "My average reward for a day was positive for the first time.")
            break
    for n in nights:
        once("night", n, "Practiced at night for the first time.")
        if n.get("adopted"):
            t = n.get("test", {})
            once("adopt", n, f"My hands improved overnight for the first time "
                             f"({t.get('before', {}).get('sr', 0):.0%} → {t.get('after', {}).get('sr', 0):.0%} on new tasks).")
    ev.sort(key=lambda e: e.get("time") or "")
    ev = ev[-7:]
    if ev:
        ev[-1]["current"] = True
    return ev
