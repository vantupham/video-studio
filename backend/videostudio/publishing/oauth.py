"""OAuth helpers: Google installed-app flow (PKCE + loopback redirect) and Facebook Login
for desktop (code returned to ``login_success.html``, captured by the app's webview)."""

from __future__ import annotations

import base64
import hashlib
import html
import secrets
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

from .base import PublishAuthError, PublishError, error_excerpt, http_client, request

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"
YOUTUBE_SCOPES = ("https://www.googleapis.com/auth/youtube.upload",
                  "https://www.googleapis.com/auth/youtube.force-ssl")
YOUTUBE_API = "https://www.googleapis.com/youtube/v3"

FB_VERSION = "v21.0"
FB_DIALOG_URL = f"https://www.facebook.com/{FB_VERSION}/dialog/oauth"
FB_GRAPH = f"https://graph.facebook.com/{FB_VERSION}"
FB_REDIRECT_URI = "https://www.facebook.com/connect/login_success.html"
FB_SCOPES = ("pages_show_list", "pages_read_engagement", "pages_manage_posts")


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge.rstrip(b"=").decode()


def expires_at(seconds: Any) -> str | None:
    try:
        n = int(seconds)
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    return (datetime.now(UTC) + timedelta(seconds=n)).isoformat()


# ------------------------------------------------------------------ loopback server

PAGE = """<!doctype html><html lang="vi"><meta charset="utf-8"><title>Video Studio</title>
<body style="font-family:system-ui,sans-serif;background:#0f172a;color:#e2e8f0;
display:grid;place-items:center;height:100vh;margin:0"><div style="text-align:center">
<h1 style="font-size:22px">{title}</h1><p>{body}</p></div></body></html>"""


class LoopbackServer:
    """Accepts exactly one OAuth redirect on ``http://127.0.0.1:<random port>/``."""

    def __init__(self, on_result: Callable[[dict[str, str]], tuple[bool, str]],
                 timeout_s: float = 600):
        self._on_result = on_result
        self._done = threading.Event()
        self._timeout = timeout_s
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 - http.server API
                parts = urlsplit(self.path)
                params = {k: v[0] for k, v in parse_qs(parts.query).items()}
                if not params.get("code") and not params.get("error"):
                    self.send_response(404)
                    self.end_headers()
                    return
                ok, message = outer._on_result(params)
                body = PAGE.format(
                    title="Đã kết nối" if ok else "Kết nối thất bại",
                    body=html.escape(message) + "<br>Bạn có thể đóng thẻ này và quay lại "
                                                "Video Studio.")
                data = body.encode("utf-8")
                self.send_response(200 if ok else 400)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                outer._done.set()

            def log_message(self, *args: Any) -> None:  # keep codes out of logs
                return

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._server.server_address[1]
        self.redirect_uri = f"http://127.0.0.1:{self.port}/"
        self._thread = threading.Thread(target=self._serve, name="oauth-loopback",
                                        daemon=True)

    def start(self) -> LoopbackServer:
        self._thread.start()
        return self

    def _serve(self) -> None:
        deadline = time.monotonic() + self._timeout
        self._server.timeout = 0.5
        try:
            while not self._done.is_set() and time.monotonic() < deadline:
                self._server.handle_request()
        finally:
            self._server.server_close()

    def stop(self) -> None:
        self._done.set()

    @property
    def finished(self) -> bool:
        return self._done.is_set()


# ------------------------------------------------------------------ Google


def google_auth_url(client_id: str, redirect_uri: str, state: str, challenge: str,
                    login_hint: str | None = None) -> str:
    params = {"client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code",
              "scope": " ".join(YOUTUBE_SCOPES), "access_type": "offline",
              "prompt": "consent", "include_granted_scopes": "true", "state": state,
              "code_challenge": challenge, "code_challenge_method": "S256"}
    if login_hint:
        params["login_hint"] = login_hint
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


def _google_error(resp, label: str) -> PublishError:
    try:
        data = resp.json()
    except ValueError:
        data = {}
    code = data.get("error") if isinstance(data, dict) else None
    if code in ("invalid_grant", "unauthorized_client", "invalid_client"):
        hint = {"invalid_grant": "phiên đăng nhập đã hết hạn hoặc bị thu hồi — hãy kết nối "
                                 "lại tài khoản",
                "invalid_client": "Client ID/secret không đúng",
                "unauthorized_client": "ứng dụng OAuth không được phép dùng luồng này"}[code]
        return PublishAuthError(f"{label}: {hint}", status=resp.status_code)
    retryable = resp.status_code >= 500 or resp.status_code == 429
    return PublishError(f"{label}: lỗi {resp.status_code}. {error_excerpt(resp)}",
                        retryable=retryable, status=resp.status_code)


async def google_exchange_code(client_id: str, client_secret: str, code: str,
                               verifier: str, redirect_uri: str) -> dict[str, Any]:
    async with http_client(30) as http:
        resp = await request(http, "POST", GOOGLE_TOKEN_URL, label="Google OAuth", data={
            "code": code, "client_id": client_id, "client_secret": client_secret,
            "redirect_uri": redirect_uri, "grant_type": "authorization_code",
            "code_verifier": verifier})
    if resp.status_code != 200:
        raise _google_error(resp, "Google OAuth")
    data = resp.json()
    if not data.get("refresh_token"):
        raise PublishAuthError("Google không trả refresh token. Hãy thu hồi quyền của ứng "
                               "dụng tại myaccount.google.com/permissions rồi kết nối lại.")
    return {"access_token": data["access_token"], "refresh_token": data["refresh_token"],
            "expires_at": expires_at(data.get("expires_in")),
            "scope": data.get("scope", "")}


async def google_refresh(client_id: str, client_secret: str, refresh_token: str
                         ) -> dict[str, Any]:
    async with http_client(30) as http:
        resp = await request(http, "POST", GOOGLE_TOKEN_URL, label="Google OAuth", data={
            "client_id": client_id, "client_secret": client_secret,
            "refresh_token": refresh_token, "grant_type": "refresh_token"})
    if resp.status_code != 200:
        raise _google_error(resp, "Làm mới token YouTube")
    data = resp.json()
    out = {"access_token": data["access_token"],
           "expires_at": expires_at(data.get("expires_in"))}
    if data.get("refresh_token"):
        out["refresh_token"] = data["refresh_token"]
    return out


async def google_revoke(token: str) -> bool:
    async with http_client(15) as http:
        try:
            resp = await request(http, "POST", GOOGLE_REVOKE_URL, label="Google",
                                 data={"token": token})
        except PublishError:
            return False
    return resp.status_code == 200


async def youtube_channel(access_token: str) -> dict[str, Any]:
    async with http_client(30) as http:
        resp = await request(http, "GET", f"{YOUTUBE_API}/channels", label="YouTube",
                             params={"part": "snippet,status", "mine": "true"},
                             headers={"Authorization": f"Bearer {access_token}"})
    if resp.status_code != 200:
        raise PublishError(f"YouTube: không đọc được kênh ({resp.status_code}). "
                           f"{error_excerpt(resp)}", status=resp.status_code)
    items = resp.json().get("items") or []
    if not items:
        raise PublishError("Tài khoản Google này chưa có kênh YouTube. Hãy tạo kênh trên "
                           "youtube.com rồi kết nối lại.")
    ch = items[0]
    snippet = ch.get("snippet", {})
    thumbs = snippet.get("thumbnails", {})
    avatar = (thumbs.get("default") or thumbs.get("medium") or {}).get("url")
    return {"id": ch["id"], "title": snippet.get("title") or ch["id"],
            "custom_url": snippet.get("customUrl"), "avatar_url": avatar,
            "long_uploads": (ch.get("status") or {}).get("longUploadsStatus")}


# ------------------------------------------------------------------ Facebook


def facebook_auth_url(app_id: str, state: str) -> str:
    params = {"client_id": app_id, "redirect_uri": FB_REDIRECT_URI, "state": state,
              "response_type": "code", "scope": ",".join(FB_SCOPES)}
    return f"{FB_DIALOG_URL}?{urlencode(params)}"


def parse_redirect(url: str) -> dict[str, str]:
    """Params from the login_success.html URL (query or fragment)."""
    parts = urlsplit(url.strip())
    params = {k: v[0] for k, v in parse_qs(parts.query).items()}
    params.update({k: v[0] for k, v in parse_qs(parts.fragment).items()})
    return params


def facebook_error(resp, label: str) -> PublishError:
    try:
        err = resp.json().get("error", {})
    except (ValueError, AttributeError):
        err = {}
    code = err.get("code")
    message = str(err.get("message") or resp.text[:200])
    if code == 190 or resp.status_code == 401:
        return PublishAuthError(f"{label}: token Facebook hết hạn hoặc bị thu hồi — hãy kết "
                                f"nối lại. ({message})", status=resp.status_code)
    if code in (10, 200, 230) or (isinstance(code, int) and 200 <= code < 300):
        return PublishError(f"{label}: thiếu quyền ({message}). Kiểm tra quyền "
                            "pages_manage_posts và vai trò quản trị Trang.", code="permission",
                            status=resp.status_code)
    if code in (4, 17, 32, 613) or resp.status_code == 429:
        return PublishError(f"{label}: vượt giới hạn gọi API, sẽ thử lại. ({message})",
                            retryable=True, code="quota", status=resp.status_code)
    retryable = bool(err.get("is_transient")) or code in (1, 2) or resp.status_code >= 500
    return PublishError(f"{label}: {message}", retryable=retryable, status=resp.status_code)


async def _fb_get(path: str, params: dict[str, Any], label: str) -> dict[str, Any]:
    async with http_client(30) as http:
        resp = await request(http, "GET", f"{FB_GRAPH}/{path.lstrip('/')}", label=label,
                             params=params)
    if resp.status_code != 200:
        raise facebook_error(resp, label)
    return resp.json()


async def facebook_exchange_code(app_id: str, app_secret: str, code: str) -> dict[str, Any]:
    short = await _fb_get("oauth/access_token", {
        "client_id": app_id, "client_secret": app_secret, "redirect_uri": FB_REDIRECT_URI,
        "code": code}, "Facebook đăng nhập")
    long = await _fb_get("oauth/access_token", {
        "grant_type": "fb_exchange_token", "client_id": app_id, "client_secret": app_secret,
        "fb_exchange_token": short["access_token"]}, "Facebook đổi token dài hạn")
    return {"access_token": long["access_token"],
            "expires_at": expires_at(long.get("expires_in"))}


async def facebook_pages(user_token: str) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    params: dict[str, Any] = {"fields": "id,name,access_token,tasks,picture{url},category",
                              "limit": 100, "access_token": user_token}
    data = await _fb_get("me/accounts", params, "Facebook Trang")
    pages.extend(data.get("data", []))
    return [{"id": p["id"], "name": p.get("name", p["id"]), "access_token": p["access_token"],
             "tasks": p.get("tasks", []), "category": p.get("category"),
             "picture": ((p.get("picture") or {}).get("data") or {}).get("url")}
            for p in pages if p.get("access_token")]


async def facebook_debug_token(token: str, app_id: str, app_secret: str) -> dict[str, Any]:
    data = await _fb_get("debug_token", {"input_token": token,
                                         "access_token": f"{app_id}|{app_secret}"},
                         "Facebook kiểm tra token")
    return data.get("data", {})


async def facebook_revoke(user_token: str) -> bool:
    async with http_client(15) as http:
        try:
            resp = await request(http, "DELETE", f"{FB_GRAPH}/me/permissions",
                                 label="Facebook", params={"access_token": user_token})
        except PublishError:
            return False
    return resp.status_code == 200
