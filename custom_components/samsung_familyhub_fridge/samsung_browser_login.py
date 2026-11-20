"""Samsung Account sign-in through Samsung's own web page.

Unlike the email/password login in ``auth.py`` (which Samsung rejects for
accounts with 2-step verification), this opens Samsung's real sign-in page in
the user's browser, so Samsung itself asks for the SMS code. Afterwards the
page redirects to an ``ms-app://...`` address that browsers can't open; the
user copies that address back into Home Assistant, and we exchange the code in
it for the same self-renewing IoT token the password login produces.

The flow mirrors the open-source uTag app (github.com/KieronQuinn/uTag,
``utils/SignInUtils.kt``, ``repositories/AuthRepository.kt``,
``ui/screens/login/AuthResponseViewModel.kt``):

1. GET  account.samsung.com/accounts/ANDROIDSDK/getEntryPoint
        → signInURI, chkDoNum, pkiPublicKey
2. Build an encrypted ``svcParam`` (PKCE challenge, state, redirect URI, ...)
   and open ``signInURI?locale=..&svcParam=..&mode=C`` in a browser.
3. Samsung redirects to ``ms-app://<sid>?code=..&state=..&auth_server_url=..
   &retValue=..`` with every value AES-encrypted (key derived from our state).
4. POST {auth_server_url}/auth/oauth2/authenticate → userauth_token
5. Existing ``get_samsung_iot_token`` → IoT access + refresh token.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
import string
from urllib.parse import parse_qs, quote, urlparse

import requests

from .auth import AuthError, SamsungIoTCredentials, get_samsung_iot_token

_LOGGER = logging.getLogger(__name__)

ENTRY_POINT_URL = "https://account.samsung.com/accounts/ANDROIDSDK/getEntryPoint"
LOGIN_CLIENT_ID = "yfrtglt53o"
ONECONNECT_PACKAGE = "com.samsung.android.oneconnect"
ONECONNECT_VERSION = "1.8.21.28"
# Redirect registered for the login client (Samsung Account app for Windows).
REDIRECT_URI = (
    "ms-app://s-1-15-2-4027708247-2189610-1983755848-2937435718-"
    "1578786913-2158692839-1974417358"
)
DEVICE_MODEL = "SM-G991B"
DEVICE_MANUFACTURER = "samsung"
ANDROID_SDK = "33"
ANDROID_RELEASE = "13"
TIMEOUT = 30

_ALNUM = string.ascii_letters + string.digits


def _random_alnum(length: int) -> str:
    return "".join(secrets.choice(_ALNUM) for _ in range(length))


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def osp_headers() -> dict[str, str]:
    """Headers the SmartThings app sends to Samsung Account (OspInterceptor)."""
    return {
        "X-Osp-Clientosversion": ANDROID_SDK,
        "X-Osp-Clientmodel": DEVICE_MODEL,
        "X-Osp-Appid": LOGIN_CLIENT_ID,
        "X-Osp-Packagename": ONECONNECT_PACKAGE,
        "X-Osp-Packageversion": ONECONNECT_VERSION,
        "User-Agent": (
            f"Android/Oneapp/{ONECONNECT_VERSION} ({DEVICE_MODEL}; Android "
            f"{ANDROID_SDK}/{ANDROID_RELEASE}) 4.11.0 QcApplication"
        ),
    }


def code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def encrypt_svc_param(svc_param: dict, b64_public_key: str, chk_do_num: str) -> str:
    """Port of SignInUtils.encrypt: AES-CBC payload + RSA-wrapped key."""
    from cryptography.hazmat.primitives import padding as sym_padding
    from cryptography.hazmat.primitives.asymmetric import padding as rsa_padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives.serialization import load_der_public_key

    iterations = int(chk_do_num)
    public_key = load_der_public_key(base64.b64decode(b64_public_key))

    # getKey: PBKDF2-HMAC-SHA256(password=b64(sha256(chkDoNum)), random salt)
    password = _b64(hashlib.sha256(chk_do_num.encode()).digest()).encode()
    key = hashlib.pbkdf2_hmac("sha256", password, os.urandom(16), iterations, 16)

    # getEncryptedKey: RSA/ECB/PKCS1 over base64(key)
    svc_enc_ky = _b64(public_key.encrypt(_b64(key).encode(), rsa_padding.PKCS1v15()))

    # getEncryptedValue: AES/CBC/PKCS5 with random IV
    iv = os.urandom(16)
    padder = sym_padding.PKCS7(128).padder()
    plain = padder.update(
        json.dumps(svc_param, separators=(",", ":")).encode()
    ) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    svc_enc_param = _b64(encryptor.update(plain) + encryptor.finalize())

    outer = json.dumps(
        {
            "chkDoNum": chk_do_num,
            "svcEncParam": svc_enc_param,
            "svcEncKY": svc_enc_ky,
            "svcEncIV": iv.hex(),
        },
        separators=(",", ":"),
    )
    return quote(_b64(outer.encode()), safe="")


def decrypt_value(hex_data: str, key: str) -> str:
    """Port of SignInUtils.decrypt: AES/ECB/PKCS5, key = first 16 key bytes."""
    from cryptography.hazmat.primitives import padding as sym_padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    decryptor = Cipher(algorithms.AES(key.encode()[:16]), modes.ECB()).decryptor()
    padded = decryptor.update(bytes.fromhex(hex_data)) + decryptor.finalize()
    unpadder = sym_padding.PKCS7(128).unpadder()
    return (unpadder.update(padded) + unpadder.finalize()).decode()


class SamsungBrowserLogin:
    """One browser sign-in attempt. Keep the instance between start/finish."""

    def __init__(self, country: str = "us", language: str = "en") -> None:
        self.country = (country or "us").lower()
        self.language = language or "en"
        self.state = _random_alnum(20)
        self.code_verifier = _random_alnum(43)
        # Plays the role of the Android ID in uTag.
        self.device_id = secrets.token_hex(8)

    def _svc_param(self) -> dict:
        # Field order and omitted nulls match Gson serialisation in uTag.
        return {
            "clientId": LOGIN_CLIENT_ID,
            "code_challenge": code_challenge(self.code_verifier),
            "code_challenge_method": "S256",
            "competitorDeviceYNFlag": "N",
            "countryCode": self.country,
            "deviceInfo": f"{DEVICE_MANUFACTURER}|com.android.chrome",
            "deviceModelID": DEVICE_MODEL,
            "deviceName": f"{DEVICE_MANUFACTURER} {DEVICE_MODEL}",
            "deviceOSVersion": ANDROID_SDK,
            "devicePhysicalAddressText": f"ANID:{self.device_id}",
            "deviceType": "APP",
            "deviceUniqueID": self.device_id,
            "redirect_uri": REDIRECT_URI,
            "replaceableClientConnectYN": "N",
            "replaceableClientId": "",
            "replaceableDevicePhysicalAddressText": "",
            "responseEncryptionType": "1",
            "responseEncryptionYNFlag": "Y",
            "scope": "",
            "state": self.state,
            "svcIptLgnID": "",
            "iosYNFlag": "Y",
        }

    def start(self) -> str:
        """Return the Samsung sign-in URL for the user to open."""
        resp = requests.get(ENTRY_POINT_URL, headers=osp_headers(), timeout=TIMEOUT)
        if not resp.ok:
            raise AuthError(
                f"Samsung sign-in entry point failed: HTTP {resp.status_code} "
                f"{resp.text[:200]}"
            )
        entry = resp.json()
        try:
            sign_in_uri = entry["signInURI"]
            chk_do_num = str(entry["chkDoNum"])
            public_key = entry["pkiPublicKey"]
        except (KeyError, TypeError) as err:
            raise AuthError(f"Unexpected Samsung entry point reply: {entry}") from err
        svc = encrypt_svc_param(self._svc_param(), public_key, chk_do_num)
        locale = f"{self.language}-{self.country.upper()}"
        return f"{sign_in_uri}?locale={locale}&svcParam={svc}&mode=C"

    @staticmethod
    def parse_redirect(redirect: str) -> dict[str, str]:
        """Pull code/state/auth_server_url/retValue out of the pasted address."""
        text = (redirect or "").strip()
        # Accept text copied from Chrome's console message around the URL.
        start = text.find("ms-app://")
        if start > 0:
            text = text[start:]
        for quote_char in ("'", '"', " "):
            if quote_char in text:
                text = text.split(quote_char, 1)[0]
        query = urlparse(text).query if "?" in text else text
        params = {k: v[0] for k, v in parse_qs(query).items() if v}
        missing = [
            k for k in ("code", "state", "auth_server_url", "retValue")
            if not params.get(k)
        ]
        if missing:
            raise ValueError(
                f"Address is missing {', '.join(missing)} — copy the whole "
                "ms-app://... address"
            )
        return params

    def finish(self, redirect: str) -> SamsungIoTCredentials:
        """Exchange the pasted ms-app:// address for Samsung IoT tokens."""
        params = self.parse_redirect(redirect)
        try:
            state_key = decrypt_value(params["state"], self.state)
            auth_server = decrypt_value(params["auth_server_url"], state_key)
            code = decrypt_value(params["code"], state_key)
            username = decrypt_value(params["retValue"], state_key)
        except (ValueError, UnicodeDecodeError) as err:
            raise AuthError(
                "Could not decrypt Samsung's reply. Make sure you pasted the "
                "address from the sign-in link shown on this screen (each "
                "link works once; restart setup for a fresh one)."
            ) from err
        if not auth_server.startswith("https://"):
            auth_server = f"https://{auth_server}"

        resp = requests.post(
            f"{auth_server}/auth/oauth2/authenticate",
            data={
                "grant_type": "authorization_code",
                "serviceType": "M",
                "code": code,
                "client_id": LOGIN_CLIENT_ID,
                "code_verifier": self.code_verifier,
                "username": username,
                "physical_address_text": self.device_id,
            },
            headers=osp_headers(),
            timeout=TIMEOUT,
        )
        if not resp.ok:
            raise AuthError(
                f"Samsung authenticate failed: HTTP {resp.status_code} "
                f"{resp.text[:300]}"
            )
        userauth_token = resp.json().get("userauth_token")
        if not userauth_token:
            raise AuthError(f"Samsung authenticate returned no token: {resp.text[:300]}")
        _LOGGER.debug("Samsung browser sign-in: got userauth_token")

        return get_samsung_iot_token(
            userauth_token=userauth_token,
            login_id=username,
            auth_server_url=auth_server,
            physical_address_text=self.device_id,
            code_verifier=self.code_verifier,
        )
