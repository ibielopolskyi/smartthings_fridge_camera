"""SmartThings OAuth 2.0 authentication with token refresh.

SmartThings personal access tokens (PATs) expire after 24 hours.
This module implements the SmartThings OAuth 2.0 Authorization Code flow so
tokens can be refreshed indefinitely without manual re-creation.

Flow overview:
    1. One-time: user visits the authorization URL, logs in via Samsung Account,
       and is redirected to a callback with an authorization code.
    2. The code is exchanged for an access_token (24 h) + refresh_token (30 d).
    3. Before the access_token expires, call refresh() to obtain a fresh pair.
       Each refresh resets the 30-day window on the refresh_token.

Prerequisites:
    Create a SmartThings OAuth app via the CLI:
        $ smartthings apps:create          # choose "OAuth-In App"
    Save the resulting client_id and client_secret.
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

AUTHORIZE_URL = "https://api.smartthings.com/oauth/authorize"
TOKEN_URL = "https://api.smartthings.com/oauth/token"

# Default scopes needed by the fridge camera integration.
DEFAULT_SCOPES = "r:devices:* w:devices:* x:devices:*"

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
