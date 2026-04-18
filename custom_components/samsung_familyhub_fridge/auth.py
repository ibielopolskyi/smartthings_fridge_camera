"""SmartThings OAuth 2.0 authentication with token refresh.

SmartThings personal access tokens (PATs) expire after 24 hours.
This module provides two authentication approaches:

1. **Browser-based OAuth** (SmartThingsOAuth):
   Standard OAuth 2.0 Authorization Code + PKCE flow via the SmartThings API.
   Requires one-time browser login; tokens refresh indefinitely.

2. **Headless email/password login** (SamsungAccountAuth):
   Direct Samsung Account authentication using the mobile app API endpoints.
   No browser needed — just email and password. The resulting token works with
   the SmartThings API. Does NOT support 2FA-enabled accounts.

Prerequisites for browser-based flow:
    $ smartthings apps:create   # choose "OAuth-In App"
    Save the resulting client_id and client_secret.

Prerequisites for headless flow:
    Samsung Account email/password and Samsung OAuth client credentials
    (signin_client_id and signin_client_secret from the SmartThings APK).
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import secrets
from dataclasses import dataclass, field
from urllib.parse import urlencode, urlparse, parse_qs

import requests

_LOGGER = logging.getLogger(__name__)

# --- SmartThings API OAuth endpoints ---
AUTHORIZE_URL = "https://api.smartthings.com/oauth/authorize"
TOKEN_URL = "https://api.smartthings.com/oauth/token"

# --- Samsung Account endpoints (mobile app API) ---
SAMSUNG_AUTH_URL = "https://us-auth2.samsungosp.com/auth/oauth2/requestAuthentication"
SAMSUNG_TOKEN_URL = "https://us-auth2.samsungosp.com/auth/oauth2/authWithTncMandatory"
SAMSUNG_IOT_AUTHORIZE_URL = "https://us-auth2.samsungosp.com/auth/oauth2/v2/authorize"
SAMSUNG_IOT_TOKEN_URL = "https://us-auth2.samsungosp.com/auth/oauth2/token"

# Default scopes needed by the fridge camera integration.
DEFAULT_SCOPES = "r:devices:* w:devices:* x:devices:*"
SAMSUNG_SCOPES = "iot.client+mcs.client+galaxystore.openapi"

# Samsung Account client IDs (from decompiled SmartThings app)
SAMSUNG_LOGIN_CLIENT_ID = "yfrtglt53o"
SAMSUNG_IOT_CLIENT_ID = "6iado3s6jc"

# Default redirect URI — httpbin echoes query params, making it easy to copy
# the authorization code.  Users may substitute their own.
DEFAULT_REDIRECT_URI = "https://httpbin.org/get"


def _generate_pkce_pair() -> tuple[str, str]:
    """Generate a PKCE code_verifier and its S256 code_challenge."""
    verifier = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode()
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


@dataclass
class OAuthCredentials:
    """Bundle returned by token exchange and refresh."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = 0
    scope: str = ""
    installed_app_id: str = ""


@dataclass
class SmartThingsOAuth:
    """Handles the SmartThings OAuth 2.0 Authorization Code flow."""

    client_id: str
    client_secret: str
    redirect_uri: str = DEFAULT_REDIRECT_URI
    scopes: str = DEFAULT_SCOPES

    # Internal PKCE state (populated by get_authorization_url)
    _code_verifier: str = field(default="", init=False, repr=False)

    # ---- Step 1: Authorization URL ----------------------------------------

    def get_authorization_url(self) -> str:
        """Build the URL the user should open in a browser to authorize."""
        self._code_verifier, code_challenge = _generate_pkce_pair()
        params = {
            "client_id": self.client_id,
            "response_type": "code",
            "redirect_uri": self.redirect_uri,
            "scope": self.scopes,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        return f"{AUTHORIZE_URL}?{urlencode(params)}"

    # ---- Step 2: Exchange code for tokens ---------------------------------

    def exchange_code(self, authorization_code: str) -> OAuthCredentials:
        """Exchange an authorization code for access + refresh tokens."""
        data = {
            "grant_type": "authorization_code",
            "client_id": self.client_id,
            "code": authorization_code,
            "redirect_uri": self.redirect_uri,
        }
        if self._code_verifier:
            data["code_verifier"] = self._code_verifier

        resp = requests.post(
            TOKEN_URL,
            data=data,
            auth=(self.client_id, self.client_secret),
            timeout=30,
        )
        resp.raise_for_status()
        return _parse_token_response(resp.json())

    # ---- Step 3: Refresh --------------------------------------------------

    def refresh(self, refresh_token: str) -> OAuthCredentials:
        """Use a refresh token to obtain a new access + refresh token pair."""
        data = {
            "grant_type": "refresh_token",
            "client_id": self.client_id,
            "refresh_token": refresh_token,
        }
        resp = requests.post(
            TOKEN_URL,
            data=data,
            auth=(self.client_id, self.client_secret),
            timeout=30,
        )
        if resp.status_code == 401:
            _LOGGER.error("Refresh token rejected (HTTP 401). Re-authorization required.")
        resp.raise_for_status()
        return _parse_token_response(resp.json())

    # ---- Helpers ----------------------------------------------------------

    @staticmethod
    def extract_code_from_redirect(redirect_url: str) -> str:
        """Pull the ``code`` query parameter from a redirect URL."""
        parsed = urlparse(redirect_url)
        codes = parse_qs(parsed.query).get("code", [])
        if not codes:
            raise ValueError(
                f"No 'code' parameter found in redirect URL: {redirect_url}"
            )
        return codes[0]


def _parse_token_response(data: dict) -> OAuthCredentials:
    return OAuthCredentials(
        access_token=data["access_token"],
        refresh_token=data["refresh_token"],
        token_type=data.get("token_type", "bearer"),
        expires_in=data.get("expires_in", 0),
        scope=data.get("scope", ""),
        installed_app_id=data.get("installed_app_id", ""),
    )


# ======================================================================
# Headless Samsung Account login (email + password)
# ======================================================================


@dataclass
class LoginCredentials:
    """Result of a successful headless Samsung Account login."""

    access_token: str
    userauth_token: str = ""


@dataclass
class SamsungAccountAuth:
    """Headless Samsung Account authentication using the mobile app API.

    This mimics the SmartThings Android app login flow to obtain a bearer
    token directly from email + password, without any browser interaction.

    NOTE: This does NOT work with accounts that have 2FA/MFA enabled.
    """

    email: str
    password: str
    signin_client_id: str
    signin_client_secret: str
    client_id: str = ""
    auth_url: str = SAMSUNG_AUTH_URL
    token_url: str = SAMSUNG_TOKEN_URL

    # Simulated device fingerprint — these are static values that identify
    # the "device" making the request (standard for Samsung mobile API).
    _device_id: str = field(default="", init=False)

    def __post_init__(self):
        if not self.client_id:
            self.client_id = self.signin_client_id
        # Generate a stable-ish device identifier
        self._device_id = base64.urlsafe_b64encode(
            hashlib.sha256(self.email.encode()).digest()[:8]
        ).rstrip(b"=").decode()

    def login(self) -> LoginCredentials:
        """Authenticate with email + password and return a bearer token.

        Two-step flow:
          1. requestAuthentication → userauth_token
          2. authWithTncMandatory  → access_token (Bearer token)
        """
        userauth_token = self._request_authentication()
        access_token = self._get_bearer_token(userauth_token)
        return LoginCredentials(
            access_token=access_token,
            userauth_token=userauth_token,
        )

    def login_iot(self) -> SamsungIoTCredentials:
        """Full login → IoT-scoped token (works with client.smartthings.com).

        Three-step flow:
          1. requestAuthentication → userauth_token
          2. /v2/authorize          → IoT authorization code
          3. /token                 → access_token + refresh_token
        """
        userauth_token = self._request_authentication()
        return get_samsung_iot_token(
            userauth_token=userauth_token,
            login_id=self.email,
            auth_server_url="https://us-auth2.samsungosp.com",
        )

    def _request_authentication(self) -> str:
        """Step 1: Submit email + password to get a userauth_token."""
        physical_addr = f"IMEI%3A{self._device_id}"
        data = {
            "signin_client_id": self.signin_client_id,
            "signin_client_secret": self.signin_client_secret,
            "check_2factor_authentication": "Y",
            "originalAppID": self.client_id,
            "devicePhysicalAddressText": physical_addr,
            "customerCode": "NEE",
            "deviceMultiUserID": "0",
            "phoneNumberText": "",
            "deviceName": "HomeAssistant",
            "client_id": self.client_id,
            "deviceTypeCode": "PHONE DEVICE",
            "password": self.password,
            "deviceUniqueID": physical_addr,
            "scope": SAMSUNG_SCOPES,
            "serviceRequired": "N",
            "physical_address_text": physical_addr,
            "login_id_type": "email_id",
            "mobileCountryCode": "310",
            "mobileNetworkCode": "00",
            "deviceNetworkAddressText": "02%3A00%3A00%3A00%3A00%3A00",
            "service_type": "M",
            "isRegisterDevice": "Y",
            "deviceModelID": "SM-G991B",
            "deviceSerialNumberText": self._device_id,
            "softwareVersion": "RP1A.200720.012",
            "username": self.email,
            "login_id": self.email,
        }

        resp = requests.post(
            self.auth_url,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
            timeout=30,
        )

        if resp.status_code == 401:
            _LOGGER.error("Samsung login failed: invalid email or password")
            raise AuthError("Invalid Samsung Account email or password")
        if resp.status_code == 403:
            _LOGGER.error(
                "Samsung login blocked — account may require 2FA or CAPTCHA"
            )
            raise AuthError(
                "Samsung Account login blocked. If 2FA is enabled, "
                "use the browser-based OAuth flow instead."
            )
        resp.raise_for_status()

        body = resp.json()
        token = body.get("userauth_token")
        if not token:
            error_msg = body.get("error_description", body.get("error", str(body)))
            raise AuthError(f"Samsung login did not return userauth_token: {error_msg}")

        _LOGGER.debug("Samsung Account authentication successful")
        return token

    def _get_bearer_token(self, userauth_token: str) -> str:
        """Step 2: Exchange userauth_token for a SmartThings bearer token."""
        data = {
            "check_email_validation": "Y",
            "authenticate": "Y",
            "data_collection_accepted": "N",
            "client_id": self.client_id,
            "lang_code": "EN",
            "appId": self.client_id,
            "scope": SAMSUNG_SCOPES,
            "login_id": self.email,
            "package": "com.samsung.android.oneconnect",
            "login_id_type": "email_id",
            "physical_address_text": f"IMEI%3A{self._device_id}",
            "userauth_token": userauth_token,
        }

        resp = requests.post(
            self.token_url,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
            timeout=30,
        )
        resp.raise_for_status()

        body = resp.json()
        try:
            access_token = body["token"]["access_token"]
        except (KeyError, TypeError):
            error_msg = body.get("error_description", body.get("error", str(body)))
            raise AuthError(f"Token exchange failed: {error_msg}")

        _LOGGER.debug("Samsung bearer token obtained successfully")
        return access_token


class AuthError(Exception):
    """Raised when Samsung Account authentication fails."""


# ======================================================================
# Samsung Account IoT token (for client.smartthings.com)
# ======================================================================


@dataclass
class SamsungIoTCredentials:
    """Samsung Account IoT-scoped token pair — works with client.smartthings.com."""

    access_token: str
    refresh_token: str
    auth_server_url: str = "https://us-auth2.samsungosp.com"


def get_samsung_iot_token(
    userauth_token: str,
    login_id: str = "",
    auth_server_url: str = "https://us-auth2.samsungosp.com",
) -> SamsungIoTCredentials:
    """Exchange a Samsung Account ``userauth_token`` for an IoT-scoped token.

    The resulting token carries Samsung Account identity and works with
    ``client.smartthings.com`` endpoints (unlike SmartThings API OAuth
    tokens which return "No samsung id available").

    Flow (from decompiled SmartThings Android app):
      1. GET  /auth/oauth2/v2/authorize  → authorization code
      2. POST /auth/oauth2/token         → access_token + refresh_token
    """
    verifier, challenge = _generate_pkce_pair()
    device_id = base64.urlsafe_b64encode(os.urandom(8)).rstrip(b"=").decode()

    # Step 1: Get authorization code for IoT scope
    params = {
        "response_type": "code",
        "client_id": SAMSUNG_IOT_CLIENT_ID,
        "scope": "iot.client",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "userauth_token": userauth_token,
        "serviceType": "M",
        "childAccountSupported": "Y",
        "physical_address_text": device_id,
    }
    if login_id:
        params["login_id"] = login_id

    resp = requests.get(
        f"{auth_server_url}/auth/oauth2/v2/authorize",
        params=params,
        allow_redirects=False,
        timeout=30,
    )
    if resp.status_code not in (200, 302):
        raise AuthError(
            f"IoT authorize failed: HTTP {resp.status_code} {resp.text[:200]}"
        )

    # The code might be in the response body (JSON) or in a redirect Location
    auth_code = None
    if resp.status_code == 302:
        loc = resp.headers.get("Location", "")
        codes = parse_qs(urlparse(loc).query).get("code", [])
        if codes:
            auth_code = codes[0]
    if not auth_code:
        body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
        auth_code = body.get("code") or body.get("auth_code")
    if not auth_code:
        raise AuthError(
            f"IoT authorize did not return a code: {resp.text[:300]}"
        )

    _LOGGER.debug("Got IoT authorization code")

    # Step 2: Exchange code for access + refresh tokens
    data = {
        "grant_type": "authorization_code",
        "client_id": SAMSUNG_IOT_CLIENT_ID,
        "code": auth_code,
        "code_verifier": verifier,
        "physical_address_text": device_id,
    }
    resp = requests.post(
        f"{auth_server_url}/auth/oauth2/token",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
        timeout=30,
    )
    if resp.status_code != 200:
        raise AuthError(
            f"IoT token exchange failed: HTTP {resp.status_code} {resp.text[:200]}"
        )
    body = resp.json()
    access = body.get("access_token")
    refresh = body.get("refresh_token")
    if not access or not refresh:
        raise AuthError(f"IoT token response missing tokens: {body}")

    _LOGGER.info("Samsung IoT token obtained successfully")
    return SamsungIoTCredentials(
        access_token=access,
        refresh_token=refresh,
        auth_server_url=auth_server_url,
    )


def refresh_samsung_iot_token(
    refresh_token: str,
    auth_server_url: str = "https://us-auth2.samsungosp.com",
) -> SamsungIoTCredentials:
    """Refresh a Samsung IoT token. Returns new access + refresh tokens."""
    data = {
        "grant_type": "refresh_token",
        "client_id": SAMSUNG_IOT_CLIENT_ID,
        "refresh_token": refresh_token,
    }
    resp = requests.post(
        f"{auth_server_url}/auth/oauth2/token",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
        timeout=30,
    )
    if resp.status_code == 401:
        raise AuthError("Samsung IoT refresh token expired — re-login required")
    resp.raise_for_status()
    body = resp.json()
    return SamsungIoTCredentials(
        access_token=body["access_token"],
        refresh_token=body.get("refresh_token", refresh_token),
        auth_server_url=auth_server_url,
    )
