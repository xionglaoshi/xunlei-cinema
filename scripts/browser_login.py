#!/usr/bin/env python3
"""Private, temporary browser UI for an independent xunlei-cli login."""

import asyncio
import html
import json
import os
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

from xunlei.auth import AuthManager, ReviewPanelException
from xunlei.config import Config

state = {"stage": "credentials", "username": "", "password": "", "review": None, "error": ""}
lock = threading.Lock()
nonce = secrets.token_urlsafe(24)


async def try_login(creditkey=""):
    config = Config()
    os.chmod(config.config_dir, 0o700)
    auth = AuthManager(config)
    try:
        await auth.login(state["username"], state["password"], creditkey)
        state.update(stage="success", username="", password="", review=None, error="")
    except ReviewPanelException as exc:
        state.update(stage="review", review=exc.review_data.to_dict(), error="")
    except Exception as exc:
        state.update(stage="error", error=type(exc).__name__, username="", password="", review=None)
    finally:
        await auth.close()
        for filename in ("config.json", "token.json"):
            path = Path(config.config_dir) / filename
            if path.exists():
                os.chmod(path, 0o600)


def page():
    stage = state["stage"]
    if stage == "credentials":
        body = '<form method="post" action="/{}/login"><label>迅雷账号 <input name="username" autocomplete="username" required></label><br><label>密码 <input name="password" type="password" autocomplete="current-password" required></label><br><button type="submit">登录</button></form>'.format(nonce)
    elif stage == "review":
        body = '<p>需要新设备短信验证。请等待 Agent 打开迅雷官方验证页，在那里完成验证。</p>'
    elif stage == "success":
        body = '<p>登录成功。凭据已写入本机 xunlei-cli 配置。</p>'
    else:
        body = '<p>登录失败：{}</p><p>请关闭此页后重新启动登录。</p>'.format(html.escape(state["error"]))
    return ('<!doctype html><html lang="zh"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>迅雷家庭影院登录</title><style>body{{font:18px system-ui;max-width:520px;margin:12vh auto;padding:24px}}label{{display:block;margin:16px 0}}input{{font:inherit;padding:8px;width:95%}}button{{font:inherit;padding:12px 28px;background:#2684ff;color:white;border:0;border-radius:8px}}</style><h1>迅雷家庭影院登录</h1><p>此页面由本机技能提供；账号和密码只通过本机服务提交给迅雷登录接口，不写入文件。</p>{}</html>').format(body).encode()


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body=b"", content_type="text/html; charset=utf-8", cors=False):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if cors:
            self.send_header("Access-Control-Allow-Origin", "https://i.xunlei.com")
            self.send_header("Access-Control-Allow-Private-Network", "true")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.client_address[0] != "127.0.0.1":
            self._send(403)
        elif self.path == f"/{nonce}":
            self._send(200, page())
        elif self.path == f"/{nonce}/review" and self.headers.get("Origin") == "https://i.xunlei.com" and state["stage"] == "review":
            self._send(200, json.dumps(state["review"]).encode(), "application/json", True)
        elif self.path == f"/{nonce}/status":
            self._send(200, json.dumps({"stage": state["stage"]}).encode(), "application/json")
        else:
            self._send(404)

    def do_OPTIONS(self):
        if self.path == f"/{nonce}/review" and self.headers.get("Origin") == "https://i.xunlei.com":
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "https://i.xunlei.com")
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.end_headers()
        else:
            self._send(403)

    def do_POST(self):
        if self.client_address[0] != "127.0.0.1" or self.path not in (f"/{nonce}/login", f"/{nonce}/verified"):
            self._send(403)
            return
        length = int(self.headers.get("Content-Length", "0"))
        if not 1 <= length <= 8192:
            self._send(400)
            return
        data = self.rfile.read(length)
        if not lock.acquire(False):
            self._send(409)
            return
        try:
            if self.path.endswith("/login") and state["stage"] == "credentials":
                values = parse_qs(data.decode("utf-8"))
                state.update(username=values.get("username", [""])[0], password=values.get("password", [""])[0])
                if not state["username"] or not state["password"]:
                    self._send(400)
                    return
                asyncio.run(try_login())
                self.send_response(303)
                self.send_header("Location", f"/{nonce}")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
            elif self.path.endswith("/verified") and state["stage"] == "review":
                key = json.loads(data)["creditkey"]
                if not isinstance(key, str) or not key.startswith("ck"):
                    self._send(400)
                    return
                asyncio.run(try_login(key))
                self._send(200, json.dumps({"stage": state["stage"]}).encode(), "application/json")
            else:
                self._send(409)
        finally:
            lock.release()

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    print(f"http://127.0.0.1:{server.server_port}/{nonce}", flush=True)
    server.serve_forever()
