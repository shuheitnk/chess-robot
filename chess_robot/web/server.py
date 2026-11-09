"""KOMA の画面を出すローカルの Web サーバ（標準ライブラリだけ）

・HTML/CSS/JS（web/static）を配る。ビルドは不要
・/ws で WebSocket（RFC 6455 の必要な部分だけ：テキストのフレーム、ping/pong、close）
・/api/... で JSON（朝のレポート・夜の練習の記録など）
物理シミュレーションはメインスレッドで動き、このサーバは別スレッドで動く。
ブラウザから届いたメッセージは Hub.on_message へ、送るときは Hub.broadcast を使う（どのスレッドからでもよい）。
"""
import base64
import hashlib
import json
import socket
import struct
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

STATIC = Path(__file__).resolve().parent / "static"
GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".js": "text/javascript; charset=utf-8", ".svg": "image/svg+xml", ".json": "application/json",
         ".png": "image/png", ".ico": "image/x-icon"}


class Client:
    """つながっているブラウザ1つ"""

    def __init__(self, sock):
        self.sock = sock
        self.lock = threading.Lock()

    def send_text(self, text):
        data = text.encode("utf-8")
        n = len(data)
        if n < 126:
            head = struct.pack("!BB", 0x81, n)
        elif n < 65536:
            head = struct.pack("!BBH", 0x81, 126, n)
        else:
            head = struct.pack("!BBQ", 0x81, 127, n)
        with self.lock:
            self.sock.sendall(head + data)

    def send_control(self, opcode, payload=b""):
        with self.lock:
            self.sock.sendall(struct.pack("!BB", 0x80 | opcode, len(payload)) + payload)


def read_frame(rfile):
    """届いたフレームを1つ読む → (fin, opcode, payload)。切れたら None"""
    head = rfile.read(2)
    if len(head) < 2:
        return None
    fin, op = head[0] & 0x80, head[0] & 0x0F
    masked, n = head[1] & 0x80, head[1] & 0x7F
    if n == 126:
        n = struct.unpack("!H", rfile.read(2))[0]
    elif n == 127:
        n = struct.unpack("!Q", rfile.read(8))[0]
    if n > 1 << 20:                       # 画面から届くのは短い命令だけ
        return None
    mask = rfile.read(4) if masked else b"\0\0\0\0"
    data = rfile.read(n)
    if len(data) < n:
        return None
    if masked:
        data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    return bool(fin), op, data


class Hub:
    """WebSocket でつながっているブラウザたち"""

    def __init__(self, on_message=None, welcome=None):
        self.clients = []
        self.lock = threading.Lock()
        self.on_message = on_message or (lambda msg: None)
        self.welcome = welcome or (lambda: [])

    def has_clients(self):
        return bool(self.clients)

    def add(self, c):
        with self.lock:
            self.clients.append(c)
        for msg in self.welcome():
            c.send_text(json.dumps(msg))

    def remove(self, c):
        with self.lock:
            if c in self.clients:
                self.clients.remove(c)

    def broadcast(self, msg):
        text = json.dumps(msg)
        for c in list(self.clients):
            try:
                c.send_text(text)
            except OSError:
                self.remove(c)


def make_handler(hub, pages, apis):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):          # アクセスのたびにターミナルへ出さない
            pass

        def _send(self, code, ctype, body, extra=()):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in extra:
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/ws" and "websocket" in self.headers.get("Upgrade", "").lower():
                return self._websocket()
            if path in apis:
                try:
                    body = json.dumps(apis[path]()).encode("utf-8")
                except Exception as e:             # 記録が読めなくても画面は止めない
                    body = json.dumps({"error": str(e)}).encode("utf-8")
                return self._send(200, "application/json", body)
            if path in pages:
                target = pages[path]
                if target.startswith("redirect:"):
                    return self._send(302, "text/plain", b"", [("Location", target[9:])])
                path = "/" + target
            f = (STATIC / path.lstrip("/")).resolve()
            if STATIC not in f.parents or not f.is_file():
                return self._send(404, "text/plain; charset=utf-8", b"not found")
            self._send(200, TYPES.get(f.suffix, "application/octet-stream"), f.read_bytes())

        def _websocket(self):
            key = self.headers.get("Sec-WebSocket-Key", "")
            accept = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
            self.send_response(101, "Switching Protocols")
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", accept)
            self.end_headers()
            self.close_connection = True
            c = Client(self.connection)
            try:
                hub.add(c)
                parts = []
                while True:
                    fr = read_frame(self.rfile)
                    if fr is None:
                        break
                    fin, op, data = fr
                    if op == 8:                    # close
                        try:
                            c.send_control(8)
                        except OSError:
                            pass
                        break
                    if op == 9:                    # ping → pong
                        c.send_control(10, data[:125])
                        continue
                    if op in (1, 0):
                        parts.append(data)
                        if fin:
                            text = b"".join(parts).decode("utf-8", "replace")
                            parts = []
                            try:
                                hub.on_message(json.loads(text))
                            except ValueError:
                                pass
            except OSError:
                pass
            finally:
                hub.remove(c)

    return Handler


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False               # Windows で同じポートを二重に使わないように


def serve(hub, pages, apis=None, host="127.0.0.1", port=8765, open_path=None, open_browser=True):
    """サーバを別スレッドで動かし、(server, url) を返す。ポートが使われていたら次の番号を試す"""
    handler = make_handler(hub, pages, apis or {})
    last = None
    for p in range(port, port + 20):
        try:
            srv = Server((host, p), handler)
            break
        except OSError as e:
            last = e
    else:
        raise last
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    shown = "127.0.0.1" if host in ("127.0.0.1", "localhost") else lan_ip()
    url = f"http://{shown}:{srv.server_address[1]}"
    if open_browser and open_path is not None:
        webbrowser.open(url + open_path)
    return srv, url


def lan_ip():
    """同じネットワークのスマホから開くときのアドレス"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


# ---------------------------------------------------------------- テスト用の小さなクライアント
class WSClient:
    """自動テスト用の WebSocket クライアント（標準ライブラリだけ）"""

    def __init__(self, url):
        import os
        host, port = url.split("//", 1)[1].split("/", 1)[0].split(":")
        self.sock = socket.create_connection((host, int(port)))
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET /ws HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\n"
                           f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        self.rfile = self.sock.makefile("rb")
        line = self.rfile.readline()
        if b"101" not in line:
            raise ConnectionError(line)
        while self.rfile.readline() not in (b"\r\n", b""):
            pass

    def send(self, obj):
        import os
        data = json.dumps(obj).encode()
        mask = os.urandom(4)
        head = struct.pack("!BB", 0x81, 0x80 | len(data)) if len(data) < 126 else \
            struct.pack("!BBH", 0x81, 0x80 | 126, len(data))
        self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def recv(self, timeout=None):
        self.sock.settimeout(timeout)
        fr = read_frame(self.rfile)
        if fr is None:
            raise ConnectionError("closed")
        return json.loads(fr[2].decode())

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass
