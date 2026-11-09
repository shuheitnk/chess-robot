"""KOMA の画面のページと、記録から作るデータ（JSON）"""


def last_night():
    """いちばん新しい夜の練習の記録（なければ none）"""
    from . import night
    log = night.load_log()
    return log[-1] if log else {"none": True}


PAGES = {
    "/": "redirect:/play",
    "/play": "play.html",
    "/night": "night.html",
}

APIS = {
    "/api/night": last_night,
}
