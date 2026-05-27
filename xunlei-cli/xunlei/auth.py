"""Authentication module for Xunlei cloud drive.

Implements the two-step login flow:
1. CoreLogin: xluser.core.login/v3/login -> sessionID
2. SignIn: v1/auth/signin/token -> access_token/refresh_token
"""

import hashlib
import re
import time
import uuid
from typing import Any, Dict, Optional

import httpx

from .config import Config

# ===== Constants (from AList thunder driver) =====
CLIENT_ID = "Xp6vsxz_7IYVw2BB"
CLIENT_SECRET = "Xp6vsy4tN9toTVdMSpomVdXpRmES"
CLIENT_VERSION = "8.31.0.9726"
PACKAGE_NAME = "com.xunlei.downloadprovider"
APPID = "40"
APP_KEY = "34a062aaa22f906fca4fefe9fb3a3021"

ALGORITHMS = [
    "9uJNVj/wLmdwKrJaVj/omlQ",
    "Oz64Lp0GigmChHMf/6TNfxx7O9PyopcczMsnf",
    "Eb+L7Ce+Ej48u",
    "jKY0",
    "ASr0zCl6v8W4aidjPK5KHd1Lq3t+vBFf41dqv5+fnOd",
    "wQlozdg6r1qxh0eRmt3QgNXOvSZO6q/GXK",
    "gmirk+ciAvIgA/cxUUCema47jr/YToixTT+Q6O",
    "5IiCoM9B1/788ntB",
    "P07JH0h6qoM6TSUAK2aL9T5s2QBVeY9JWvalf",
    "+oK0AN",
]

USER_AGENT = (
    "ANDROID-com.xunlei.downloadprovider/8.31.0.9726 "
    "netWorkType/5G appid/40 deviceName/Xiaomi_M2004J7AC "
    "deviceModel/M2004J7AC OSVersion/12 protocolVersion/301 "
    "platformVersion/10 sdkVersion/512000 Oauth2Client/0.9 "
    "(Linux 4_14_186-perf-gddfs8vbb238b) (JAVA 0)"
)

DOWNLOAD_USER_AGENT = (
    "Dalvik/2.1.0 (Linux; U; Android 12; M2004J7AC Build/SP1A.210812.016)"
)

XLUSER_API_URL = "https://xluser-ssl.xunlei.com/v1"
XLUSER_CORE_URL = "https://xluser-ssl.xunlei.com"
DRIVE_API_URL = "https://api-pan.xunlei.com/drive/v1"


class ReviewPanelData:
    """Data for review panel verification (SMS verification)."""

    def __init__(self, data: dict):
        self.creditkey = data.get("creditkey", "")
        self.reviewurl = data.get("reviewurl", "")
        self.deviceid = data.get("deviceid", "")
        self.devicesign = data.get("devicesign", "")

    def to_dict(self) -> dict:
        return {
            "creditkey": self.creditkey,
            "reviewurl": self.reviewurl,
            "deviceid": self.deviceid,
            "devicesign": self.devicesign,
        }


class ReviewPanelException(Exception):
    """Exception raised when review panel (SMS) verification is required."""

    def __init__(self, review_data: ReviewPanelData, message: str = ""):
        self.review_data = review_data
        self.message = message or "SMS verification required"
        super().__init__(self.message)


class TokenInfo:
    """Token information."""

    # Buffer time before actual expiry to proactively refresh
    EXPIRY_BUFFER = 300  # 5 minutes

    def __init__(self, data: dict):
        self.token_type = data.get("token_type", "Bearer")
        self.access_token = data.get("access_token", "")
        self.refresh_token = data.get("refresh_token", "")
        self.expires_in = data.get("expires_in", 7200)
        self.user_id = data.get("user_id", data.get("sub", ""))
        # Always recalculate expire_at from expires_in for accuracy
        # Only use stored expire_at as fallback
        if self.expires_in:
            self.expire_at = int(time.time()) + self.expires_in - self.EXPIRY_BUFFER
        else:
            expire_at = data.get("expire_at", 0)
            self.expire_at = expire_at if expire_at else 0

    @property
    def authorization(self) -> str:
        return f"{self.token_type} {self.access_token}"

    def is_expired(self) -> bool:
        """Check if token is expired (with buffer)."""
        if not self.expire_at:
            return True
        return time.time() >= self.expire_at

    def to_dict(self) -> dict:
        """Serialize to dict for storage."""
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_in": self.expires_in,
            "token_type": self.token_type,
            "user_id": self.user_id,
            "expire_at": self.expire_at,
        }


def generate_device_sign(device_id: str, package_name: str = PACKAGE_NAME) -> str:
    """Generate device sign for login."""
    signature_base = f"{device_id}{package_name}{APPID}{APP_KEY}"
    sha1_hash = hashlib.sha1(signature_base.encode()).hexdigest()
    md5_hash = hashlib.md5(sha1_hash.encode()).hexdigest()
    return f"div101.{device_id}{md5_hash}"


def get_captcha_sign(device_id: str) -> tuple[str, str]:
    """Generate captcha timestamp and sign."""
    timestamp = str(int(time.time() * 1000))
    s = f"{CLIENT_ID}{CLIENT_VERSION}{PACKAGE_NAME}{device_id}{timestamp}"
    for algo in ALGORITHMS:
        s = hashlib.md5((s + algo).encode()).hexdigest()
    sign = f"1.{s}"
    return timestamp, sign


class AuthManager:
    """Manages authentication with Xunlei servers."""

    def __init__(self, config: Config):
        self.config = config
        self._token: Optional[TokenInfo] = None
        self._captcha_token: str = ""
        self.client = httpx.AsyncClient(timeout=30.0, follow_redirects=True)
        self.device_id = self._get_device_id()

    def _get_device_id(self) -> str:
        """Get or generate 32-char device ID (MD5)."""
        cfg = self.config.load_config()
        device_id = cfg.get("device_id", "")
        if len(device_id) != 32:
            device_id = hashlib.md5(str(uuid.getnode()).encode()).hexdigest()
            cfg["device_id"] = device_id
            self.config.save_config(cfg)
        return device_id

    def _base_headers(self) -> Dict[str, str]:
        return {
            "user-agent": USER_AGENT,
            "accept": "application/json;charset=UTF-8",
            "x-device-id": self.device_id,
            "x-client-id": CLIENT_ID,
            "x-client-version": CLIENT_VERSION,
        }

    async def _request(
        self, method: str, url: str, **kwargs: Any
    ) -> Dict[str, Any]:
        """Make a basic request."""
        headers = kwargs.pop("headers", {})
        headers = {**self._base_headers(), **headers}

        resp = await self.client.request(method, url, headers=headers, **kwargs)

        if resp.status_code == 401:
            raise httpx.HTTPStatusError(
                f"401 Unauthorized: {resp.text}", request=resp.request, response=resp
            )

        resp.raise_for_status()
        data = resp.json()

        # Check for API error
        error_code = data.get("error_code")
        error = data.get("error", "")
        if error_code or (error and error != "success"):
            error_msg = data.get("error", "")
            error_desc = data.get("error_description", "")

            # Handle review_panel (SMS verification required)
            if error_msg == "review_panel":
                creditkey = data.get("creditkey", "")
                reviewurl = data.get("reviewurl", "")
                # Generate deviceSign ourselves (API doesn't return it)
                device_sign = generate_device_sign(self.device_id)
                # Append deviceid to reviewurl
                if "deviceid=" not in reviewurl:
                    sep = "&" if "?" in reviewurl else "?"
                    reviewurl = f"{reviewurl}{sep}deviceid={device_sign}"
                review_data = ReviewPanelData({
                    "creditkey": creditkey,
                    "reviewurl": reviewurl,
                    "deviceid": device_sign,
                    "devicesign": device_sign,
                })
                raise ReviewPanelException(review_data)

            raise ValueError(f"API error: {error_msg} - {error_desc}")

        return data

    async def refresh_captcha_token(self, action: str, username: str = "") -> None:
        """Refresh captcha token (for login flow)."""
        metas: Dict[str, str] = {
            "client_version": CLIENT_VERSION,
            "package_name": PACKAGE_NAME,
        }

        if username:
            # Determine username type
            if re.match(
                r"\w+([-+.\w+)*@\w+([-.\w+)*\.\w+([-.\w+)*", username
            ):
                metas["email"] = username
            elif 11 <= len(username) <= 18:
                metas["phone_number"] = username
            else:
                metas["username"] = username

        await self._do_refresh_captcha(action, metas)

    async def refresh_captcha_token_at_login(self, action: str) -> None:
        """Refresh captcha token after login (uses user_id).

        This is used for all API requests after successful login.
        """
        metas: Dict[str, str] = {
            "client_version": CLIENT_VERSION,
            "package_name": PACKAGE_NAME,
            "user_id": self.user_id,
        }
        await self._do_refresh_captcha(action, metas)

    async def _do_refresh_captcha(
        self, action: str, metas: Dict[str, str]
    ) -> None:
        """Internal: call captcha init API."""
        timestamp, captcha_sign = get_captcha_sign(self.device_id)
        metas["timestamp"] = timestamp
        metas["captcha_sign"] = captcha_sign

        data = await self._request(
            "POST",
            f"{XLUSER_API_URL}/shield/captcha/init",
            json={
                "action": action,
                "captcha_token": self._captcha_token,
                "client_id": CLIENT_ID,
                "device_id": self.device_id,
                "meta": metas,
                "redirect_uri": "xlaccsdk01://xunlei.com/callback?state=harbor",
            },
        )

        if data.get("url"):
            raise ValueError(f"Verification required. Please visit: {data['url']}")

        self._captcha_token = data.get("captcha_token", "")
        if not self._captcha_token:
            raise ValueError("Failed to get captcha token")

    async def core_login(
        self, username: str, password: str, creditkey: str = ""
    ) -> str:
        """Step 1: Core login to get sessionID.

        Args:
            username: Phone (+86...), email, or username
            password: Plain text password
            creditkey: From SMS verification (for new devices)

        Returns:
            sessionID string
        """
        device_sign = generate_device_sign(self.device_id)

        data = await self._request(
            "POST",
            f"{XLUSER_CORE_URL}/xluser.core.login/v3/login",
            headers={
                "user-agent": "android-ok-http-client/xl-acc-sdk/version-5.0.12.512000"
            },
            json={
                "protocolVersion": "301",
                "sequenceNo": "1000012",
                "platformVersion": "10",
                "isCompressed": "0",
                "appid": APPID,
                "clientVersion": CLIENT_VERSION,
                "peerID": "00000000000000000000000000000000",
                "appName": "ANDROID-com.xunlei.downloadprovider",
                "sdkVersion": "512000",
                "devicesign": device_sign,
                "netWorkType": "WIFI",
                "providerName": "NONE",
                "deviceModel": "M2004J7AC",
                "deviceName": "Xiaomi_M2004j7ac",
                "OSVersion": "12",
                "creditkey": creditkey,
                "hl": "zh-CN",
                "userName": username,
                "passWord": password,
                "verifyKey": "",
                "verifyCode": "",
                "isMd5Pwd": "0",
            },
        )

        session_id = data.get("sessionID", "")
        if not session_id:
            raise ValueError(f"Login failed: no sessionID in response. {data}")

        return session_id

    async def signin_token(self, session_id: str) -> TokenInfo:
        """Step 2: Exchange sessionID for access token.

        Args:
            session_id: From core_login

        Returns:
            TokenInfo with access_token and refresh_token
        """
        # Refresh captcha token for signin action
        action = "POST:/v1/auth/signin/token"
        await self.refresh_captcha_token(action)

        data = await self._request(
            "POST",
            f"{XLUSER_API_URL}/auth/signin/token",
            headers={
                **self._base_headers(),
                "X-Captcha-Token": self._captcha_token,
            },
            json={
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "provider": "access_end_point_token",
                "signin_token": session_id,
            },
        )

        token = TokenInfo(data)
        self._token = token

        # Save token
        self.config.save_token(token.to_dict())

        return token

    async def login(
        self, username: str, password: str, creditkey: str = ""
    ) -> TokenInfo:
        """Full login: CoreLogin + SignIn.

        Args:
            username: Phone (+86...), email, or username
            password: Plain text password
            creditkey: From SMS verification (for new devices)

        Returns:
            TokenInfo

        Raises:
            ReviewPanelException: If SMS verification is required (new device)
        """
        # Step 1: Core login
        session_id = await self.core_login(username, password, creditkey)

        # Step 2: Sign in with sessionID
        return await self.signin_token(session_id)

    async def refresh_access_token(self, refresh_token: str) -> TokenInfo:
        """Refresh access token using refresh token."""
        data = await self._request(
            "POST",
            f"{XLUSER_API_URL}/auth/token",
            json={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
            },
        )

        token = TokenInfo(data)
        self._token = token

        self.config.save_token(token.to_dict())

        return token

    async def login_with_refresh_token(self, refresh_token: str) -> TokenInfo:
        """Login using refresh token. (Backward compat alias)"""
        return await self.refresh_access_token(refresh_token)

    async def init_captcha(self) -> Dict[str, Any]:
        """Initialize captcha verification. (Backward compat)

        Returns:
            Dict with captcha_token, url (if verification needed)
        """
        try:
            await self.refresh_captcha_token("POST:/v1/auth/signin/token")
            return {"captcha_token": self._captcha_token}
        except ValueError as e:
            msg = str(e)
            if "Verification required" in msg:
                import re as re_mod
                url_match = re_mod.search(r"https?://\S+", msg)
                if url_match:
                    return {"captcha_token": "", "url": url_match.group(0)}
            raise

    async def ensure_valid_token(self) -> str:
        """Get valid access token, refresh if needed."""
        # Load from config if not in memory
        if not self._token:
            stored = self.config.load_token()
            if stored:
                self._token = TokenInfo(stored)

        if not self._token:
            raise ValueError("Not logged in. Please run 'xunlei login' first.")

        # Check expiry
        if self._token.expire_at and time.time() >= self._token.expire_at:
            if self._token.refresh_token:
                await self.refresh_access_token(self._token.refresh_token)
            else:
                raise ValueError("Token expired and no refresh token available.")

        return self._token.access_token

    @property
    def authorization(self) -> str:
        """Get authorization header value.

        Loads from storage if not in memory.
        Note: Does NOT auto-refresh. Call ensure_valid_token() before this
        if you need a guaranteed fresh token.
        """
        if self._token:
            return self._token.authorization
        stored = self.config.load_token()
        if stored:
            self._token = TokenInfo(stored)
            return self._token.authorization
        return ""

    @property
    def user_id(self) -> str:
        """Get user ID from token."""
        if self._token:
            return self._token.user_id
        stored = self.config.load_token()
        if stored:
            self._token = TokenInfo(stored)
            return self._token.user_id
        return ""

    def is_logged_in(self) -> bool:
        """Check if user has valid credentials (access_token exists).

        Note: This does NOT check token expiry. A token may be expired
        but refreshable. Call ensure_valid_token() to guarantee a valid token.
        """
        if not self._token:
            stored = self.config.load_token()
            if stored:
                self._token = TokenInfo(stored)
        if not self._token:
            return False
        # Only check access_token exists, not expiry
        # (expired token can be refreshed)
        return bool(self._token.access_token and self._token.refresh_token)

    def logout(self) -> None:
        """Clear auth data."""
        self._token = None
        self._captcha_token = ""
        self.config.clear_auth()

    async def close(self) -> None:
        """Close HTTP client."""
        await self.client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()