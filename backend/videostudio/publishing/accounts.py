"""Connected social accounts and the user's own developer-app credentials.

Secrets (app client secrets, OAuth access/refresh tokens, page tokens) live only in the
SecretStore:

* ``socialapp:<platform>:client_secret``
* ``social:<account id>:token`` – JSON ``{access_token, refresh_token?, expires_at?}``
* ``social:<account id>:user_token`` – Facebook long-lived user token (to revoke)

SQLite only has ids, names and expiry dates. Nothing secret is ever returned by the API.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets as pysecrets
import threading
import time
import webbrowser
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from videostudio.db import AppSetting, SocialAccount
from videostudio.providers.service import mask_secret
from videostudio.secretstore import SecretStore

from . import oauth
from .base import AccountRef, PublishAuthError, PublishError

log = logging.getLogger(__name__)

APP_PLATFORMS = ("youtube", "facebook")
REFRESH_MARGIN = timedelta(minutes=2)
EXPIRY_WARNING = timedelta(days=7)
# Google: refresh tokens of OAuth apps in "Testing" publishing status expire after 7 days.
GOOGLE_TESTING_REFRESH_TTL = timedelta(days=7)
FLOW_TTL_S = 900


class SocialError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass
class OAuthFlow:
    id: str
    platform: str
    state: str
    created: float = field(default_factory=time.monotonic)
    status: str = "pending"  # pending | exchanging | select_pages | done | error
    error: str | None = None
    account_ids: list[str] = field(default_factory=list)
    auth_url: str = ""
    verifier: str = ""
    redirect_uri: str = ""
    server: oauth.LoopbackServer | None = None
    # Facebook: kept server-side only, never returned
    user_token: dict[str, Any] | None = None
    pages: list[dict[str, Any]] = field(default_factory=list)

    def public(self) -> dict[str, Any]:
        return {"id": self.id, "platform": self.platform, "status": self.status,
                "error": self.error, "account_ids": self.account_ids,
                "auth_url": self.auth_url, "redirect_uri": self.redirect_uri,
                "pages": [{k: v for k, v in p.items() if k != "access_token"}
                          for p in self.pages]}


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


class SocialService:
    def __init__(self, session_factory: sessionmaker[Session], secrets: SecretStore,
                 notify=None):
        self.sf = session_factory
        self.secrets = secrets
        self._flows: dict[str, OAuthFlow] = {}
        self._lock = threading.Lock()
        self._refresh_locks: dict[str, asyncio.Lock] = {}
        self.notify = notify or (lambda **kw: None)
        self.open_browser = webbrowser.open

    # ------------------------------------------------------------------ app credentials

    def _app_key(self, platform: str) -> str:
        return f"socialapp:{platform}"

    def app_config(self, platform: str) -> dict[str, Any]:
        if platform not in APP_PLATFORMS:
            raise SocialError("Nền tảng không hỗ trợ", 404)
        with self.sf() as s:
            row = s.get(AppSetting, self._app_key(platform))
            value = dict(row.value) if row else {}
        secret = self.secrets.get(f"{self._app_key(platform)}:client_secret")
        out = {"platform": platform, "client_id": value.get("client_id", ""),
               "client_secret": mask_secret(secret), "has_secret": bool(secret),
               "configured": bool(value.get("client_id") and secret)}
        if platform == "youtube":
            out["testing_mode"] = bool(value.get("testing_mode", True))
            out["redirect"] = "http://127.0.0.1:<cổng ngẫu nhiên>/ (loopback, tự động)"
        else:
            out["redirect"] = oauth.FB_REDIRECT_URI
        return out

    def set_app_config(self, platform: str, client_id: str | None,
                       client_secret: str | None, testing_mode: bool | None = None
                       ) -> dict[str, Any]:
        if platform not in APP_PLATFORMS:
            raise SocialError("Nền tảng không hỗ trợ", 404)
        with self.sf.begin() as s:
            row = s.get(AppSetting, self._app_key(platform))
            value = dict(row.value) if row else {}
            if client_id is not None:
                value["client_id"] = client_id.strip()
            if testing_mode is not None:
                value["testing_mode"] = bool(testing_mode)
            if row is None:
                s.add(AppSetting(key=self._app_key(platform), value=value))
            else:
                row.value = value
        # masked values sent back by the UI mean "unchanged"
        if client_secret and not client_secret.startswith("••••"):
            self.secrets.set(f"{self._app_key(platform)}:client_secret", client_secret.strip())
        return self.app_config(platform)

    def clear_app_secret(self, platform: str) -> dict[str, Any]:
        self.secrets.delete(f"{self._app_key(platform)}:client_secret")
        return self.app_config(platform)

    def _app_credentials(self, platform: str) -> tuple[str, str]:
        cfg = self.app_config(platform)
        secret = self.secrets.get(f"{self._app_key(platform)}:client_secret")
        if not cfg["client_id"] or not secret:
            name = "Google (YouTube)" if platform == "youtube" else "Meta (Facebook)"
            raise SocialError(f"Chưa nhập thông tin ứng dụng {name}. Vào Cài đặt → Tài khoản "
                              "mạng xã hội và làm theo hướng dẫn.", 422)
        return cfg["client_id"], secret

    # ------------------------------------------------------------------ accounts

    def _token_key(self, account_id: str, kind: str = "token") -> str:
        return f"social:{account_id}:{kind}"

    def _save_tokens(self, account_id: str, tokens: dict[str, Any]) -> None:
        self.secrets.set(self._token_key(account_id), json.dumps(tokens))

    def _load_tokens(self, account_id: str) -> dict[str, Any]:
        raw = self.secrets.get(self._token_key(account_id))
        return json.loads(raw) if raw else {}

    def account_out(self, a: SocialAccount) -> dict[str, Any]:
        now = datetime.now(UTC)
        warning = None
        expires = a.token_expires_at
        if expires and expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        if a.status == "expired":
            warning = a.last_error or "Phiên đăng nhập đã hết hạn — hãy kết nối lại."
        elif expires and expires - now < EXPIRY_WARNING:
            days = max(0, (expires - now).days)
            warning = (f"Quyền truy cập sẽ hết hạn sau {days} ngày — hãy kết nối lại trước "
                       "ngày đó." if expires > now else "Quyền truy cập đã hết hạn.")
        return {"id": a.id, "platform": a.platform, "publisher_id": a.publisher_id,
                "name": a.name, "external_id": a.external_id, "kind": a.kind,
                "avatar_url": a.avatar_url, "status": a.status, "last_error": a.last_error,
                "token_expires_at": expires, "warning": warning,
                "mock": a.publisher_id.startswith("mock_"), "meta": a.meta,
                "created_at": a.created_at}

    def list_accounts(self) -> list[dict[str, Any]]:
        with self.sf() as s:
            rows = s.scalars(select(SocialAccount).order_by(SocialAccount.platform,
                                                            SocialAccount.created_at))
            return [self.account_out(a) for a in rows]

    def account_ref(self, account_id: str) -> tuple[AccountRef, str]:
        with self.sf() as s:
            a = s.get(SocialAccount, account_id)
            if a is None:
                raise SocialError("Tài khoản đã bị ngắt kết nối", 404)
            return (AccountRef(id=a.id, platform=a.platform, external_id=a.external_id,
                               name=a.name, kind=a.kind, meta=dict(a.meta or {})),
                    a.publisher_id)

    def _upsert(self, s: Session, *, platform: str, publisher_id: str, external_id: str,
                name: str, kind: str, avatar_url: str | None, meta: dict[str, Any],
                expires: datetime | None) -> SocialAccount:
        row = s.scalars(select(SocialAccount).where(
            SocialAccount.platform == platform, SocialAccount.publisher_id == publisher_id,
            SocialAccount.external_id == external_id)).first()
        if row is None:
            row = SocialAccount(platform=platform, publisher_id=publisher_id,
                                external_id=external_id, name=name, kind=kind)
            s.add(row)
        row.name, row.kind, row.avatar_url, row.meta = name, kind, avatar_url, meta
        row.status, row.last_error, row.token_expires_at = "connected", None, expires
        s.flush()
        return row

    def connect_mock(self, platform: str, name: str | None = None) -> dict[str, Any]:
        if platform not in APP_PLATFORMS:
            raise SocialError("Nền tảng không hỗ trợ", 404)
        publisher_id = f"mock_{platform}"
        default = "Kênh thử nghiệm (giả lập)" if platform == "youtube" else \
            "Trang thử nghiệm (giả lập)"
        with self.sf.begin() as s:
            row = self._upsert(s, platform=platform, publisher_id=publisher_id,
                               external_id=f"mock-{platform}-1", name=name or default,
                               kind="channel" if platform == "youtube" else "page",
                               avatar_url=None, meta={"mock": True}, expires=None)
            account_id = row.id
            out = self.account_out(row)
        self._save_tokens(account_id, {"access_token": f"mock-{pysecrets.token_hex(8)}"})
        return out

    async def disconnect(self, account_id: str, revoke: bool = True) -> None:
        with self.sf() as s:
            a = s.get(SocialAccount, account_id)
            if a is None:
                raise SocialError("Không tìm thấy tài khoản", 404)
            platform, publisher_id = a.platform, a.publisher_id
        tokens = self._load_tokens(account_id)
        if revoke and not publisher_id.startswith("mock_"):
            try:
                if platform == "youtube" and tokens.get("refresh_token"):
                    await oauth.google_revoke(tokens["refresh_token"])
                elif platform == "facebook":
                    user = self.secrets.get(self._token_key(account_id, "user_token"))
                    others = self._accounts_sharing_user_token(account_id, user)
                    if user and not others:
                        await oauth.facebook_revoke(user)
            except Exception:  # noqa: BLE001 - disconnect must succeed locally anyway
                log.warning("Revoking %s token failed", platform)
        self.secrets.delete(self._token_key(account_id))
        self.secrets.delete(self._token_key(account_id, "user_token"))
        with self.sf.begin() as s:
            a = s.get(SocialAccount, account_id)
            if a is not None:
                s.delete(a)

    def _accounts_sharing_user_token(self, account_id: str, user_token: str | None) -> bool:
        if not user_token:
            return False
        with self.sf() as s:
            ids = list(s.scalars(select(SocialAccount.id).where(
                SocialAccount.platform == "facebook", SocialAccount.id != account_id)))
        return any(self.secrets.get(self._token_key(i, "user_token")) == user_token
                   for i in ids)

    def _mark(self, account_id: str, status: str, error: str | None = None) -> None:
        with self.sf.begin() as s:
            a = s.get(SocialAccount, account_id)
            if a is not None:
                a.status, a.last_error = status, error

    # ------------------------------------------------------------------ tokens

    async def access_token(self, account_id: str, force_refresh: bool = False) -> str:
        with self.sf() as s:
            a = s.get(SocialAccount, account_id)
            if a is None:
                raise PublishAuthError("Tài khoản đã bị ngắt kết nối")
            platform, publisher_id, status = a.platform, a.publisher_id, a.status
        tokens = self._load_tokens(account_id)
        if not tokens.get("access_token"):
            raise PublishAuthError("Không tìm thấy token — hãy kết nối lại tài khoản.")
        if publisher_id.startswith("mock_"):
            return tokens["access_token"]
        if platform == "facebook":
            if force_refresh or status == "expired":
                raise PublishAuthError("Token Trang Facebook không còn hiệu lực — hãy kết nối "
                                       "lại.")
            return tokens["access_token"]
        expires = _parse_dt(tokens.get("expires_at"))
        if not force_refresh and expires and expires - datetime.now(UTC) > REFRESH_MARGIN:
            return tokens["access_token"]
        lock = self._refresh_locks.setdefault(account_id, asyncio.Lock())
        async with lock:
            tokens = self._load_tokens(account_id)
            expires = _parse_dt(tokens.get("expires_at"))
            if not force_refresh and expires and expires - datetime.now(UTC) > REFRESH_MARGIN:
                return tokens["access_token"]
            client_id, client_secret = self._app_credentials("youtube")
            try:
                fresh = await oauth.google_refresh(client_id, client_secret,
                                                   tokens["refresh_token"])
            except PublishAuthError as exc:
                self._mark(account_id, "expired", exc.message)
                self.notify(level="error", title="Tài khoản YouTube cần kết nối lại",
                            message=exc.message, link="/settings?tab=social")
                raise
            tokens.update(fresh)
            self._save_tokens(account_id, tokens)
            if status != "connected":
                self._mark(account_id, "connected")
            return tokens["access_token"]

    # ------------------------------------------------------------------ OAuth flows

    def _new_flow(self, platform: str) -> OAuthFlow:
        with self._lock:
            now = time.monotonic()
            for fid in [f for f, fl in self._flows.items() if now - fl.created > FLOW_TTL_S]:
                old = self._flows.pop(fid)
                if old.server:
                    old.server.stop()
            flow = OAuthFlow(id=pysecrets.token_urlsafe(12), platform=platform,
                             state=pysecrets.token_urlsafe(24))
            self._flows[flow.id] = flow
            return flow

    def flow(self, flow_id: str) -> OAuthFlow:
        flow = self._flows.get(flow_id)
        if flow is None:
            raise SocialError("Phiên kết nối đã hết hạn, hãy thử lại", 404)
        return flow

    def start_youtube(self, open_browser: bool = True) -> dict[str, Any]:
        client_id, client_secret = self._app_credentials("youtube")
        flow = self._new_flow("youtube")
        flow.verifier, challenge = oauth.pkce_pair()

        def on_result(params: dict[str, str]) -> tuple[bool, str]:
            if params.get("state") != flow.state:
                flow.status, flow.error = "error", "Sai tham số state (có thể bị giả mạo)"
                return False, flow.error
            if params.get("error"):
                flow.status = "error"
                flow.error = ("Bạn đã từ chối cấp quyền" if params["error"] == "access_denied"
                              else f"Google báo lỗi: {params['error']}")
                return False, flow.error
            flow.status = "exchanging"
            try:
                account = asyncio.run(self._finish_youtube(flow, params["code"], client_id,
                                                           client_secret))
            except (PublishError, SocialError) as exc:
                flow.status, flow.error = "error", exc.message
                return False, exc.message
            except Exception as exc:  # noqa: BLE001
                log.exception("YouTube OAuth failed")
                flow.status, flow.error = "error", f"Lỗi không mong muốn: {type(exc).__name__}"
                return False, flow.error
            flow.status = "done"
            return True, f"Đã kết nối kênh YouTube “{account['name']}”."

        flow.server = oauth.LoopbackServer(on_result).start()
        flow.redirect_uri = flow.server.redirect_uri
        flow.auth_url = oauth.google_auth_url(client_id, flow.redirect_uri, flow.state,
                                              challenge)
        if open_browser:
            try:
                self.open_browser(flow.auth_url)
            except Exception:  # noqa: BLE001 - the UI shows the link as a fallback
                log.warning("Could not open the system browser")
        return flow.public()

    async def _finish_youtube(self, flow: OAuthFlow, code: str, client_id: str,
                              client_secret: str) -> dict[str, Any]:
        tokens = await oauth.google_exchange_code(client_id, client_secret, code,
                                                  flow.verifier, flow.redirect_uri)
        channel = await oauth.youtube_channel(tokens["access_token"])
        testing = self.app_config("youtube").get("testing_mode", True)
        expires = datetime.now(UTC) + GOOGLE_TESTING_REFRESH_TTL if testing else None
        with self.sf.begin() as s:
            row = self._upsert(s, platform="youtube", publisher_id="youtube",
                               external_id=channel["id"], name=channel["title"],
                               kind="channel", avatar_url=channel.get("avatar_url"),
                               meta={"custom_url": channel.get("custom_url"),
                                     "long_uploads": channel.get("long_uploads"),
                                     "scope": tokens.get("scope", "")},
                               expires=expires)
            account_id = row.id
            out = self.account_out(row)
        self._save_tokens(account_id, tokens)
        flow.account_ids = [account_id]
        return out

    def start_facebook(self) -> dict[str, Any]:
        app_id, _ = self._app_credentials("facebook")
        flow = self._new_flow("facebook")
        flow.redirect_uri = oauth.FB_REDIRECT_URI
        flow.auth_url = oauth.facebook_auth_url(app_id, flow.state)
        return flow.public()

    async def complete_facebook(self, flow_id: str, url: str) -> dict[str, Any]:
        flow = self.flow(flow_id)
        params = oauth.parse_redirect(url)
        if params.get("error") or params.get("error_reason"):
            flow.status = "error"
            flow.error = "Bạn đã huỷ đăng nhập Facebook" if params.get(
                "error_reason") == "user_denied" else f"Facebook báo lỗi: {params.get('error')}"
            raise SocialError(flow.error, 400)
        if params.get("state") != flow.state:
            raise SocialError("Sai tham số state (có thể bị giả mạo)", 400)
        if not params.get("code"):
            raise SocialError("Địa chỉ không chứa mã đăng nhập (code)", 400)
        app_id, app_secret = self._app_credentials("facebook")
        flow.status = "exchanging"
        try:
            flow.user_token = await oauth.facebook_exchange_code(app_id, app_secret,
                                                                 params["code"])
            flow.pages = await oauth.facebook_pages(flow.user_token["access_token"])
        except PublishError as exc:
            flow.status, flow.error = "error", exc.message
            raise SocialError(exc.message, 502) from exc
        if not flow.pages:
            flow.status = "error"
            flow.error = ("Tài khoản không quản trị Trang nào (hoặc chưa cấp quyền cho Trang "
                          "khi đăng nhập).")
            raise SocialError(flow.error, 422)
        flow.status = "select_pages"
        return flow.public()

    async def select_facebook_pages(self, flow_id: str, page_ids: list[str]) -> dict[str, Any]:
        flow = self.flow(flow_id)
        if flow.status != "select_pages" or flow.user_token is None:
            raise SocialError("Phiên kết nối chưa sẵn sàng", 409)
        chosen = [p for p in flow.pages if p["id"] in set(page_ids)]
        if not chosen:
            raise SocialError("Chọn ít nhất một Trang", 422)
        app_id, app_secret = self._app_credentials("facebook")
        user_token = flow.user_token["access_token"]
        for page in chosen:
            expires = None
            try:
                info = await oauth.facebook_debug_token(page["access_token"], app_id,
                                                        app_secret)
                stamp = info.get("data_access_expires_at") or info.get("expires_at")
                if stamp:
                    expires = datetime.fromtimestamp(int(stamp), UTC)
            except PublishError:
                log.info("debug_token unavailable; page token expiry unknown")
            with self.sf.begin() as s:
                row = self._upsert(s, platform="facebook", publisher_id="facebook_page",
                                   external_id=page["id"], name=page["name"], kind="page",
                                   avatar_url=page.get("picture"),
                                   meta={"category": page.get("category"),
                                         "tasks": page.get("tasks", [])},
                                   expires=expires)
                account_id = row.id
            self._save_tokens(account_id, {"access_token": page["access_token"]})
            self.secrets.set(self._token_key(account_id, "user_token"), user_token)
            flow.account_ids.append(account_id)
        flow.status = "done"
        flow.user_token, flow.pages = None, []
        return flow.public()

    # ------------------------------------------------------------------ expiry

    def expiring_accounts(self) -> list[dict[str, Any]]:
        return [a for a in self.list_accounts() if a["warning"]]

