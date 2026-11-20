"""Tests for the Samsung web-page sign-in (handles 2-step verification)."""
from __future__ import annotations

import base64
import json
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, unquote, urlencode, urlparse

import pytest
import requests_mock as rm
from cryptography.hazmat.primitives import padding as sym_padding
from cryptography.hazmat.primitives.asymmetric import padding as rsa_padding
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
)

from tests.conftest import _HomeAssistant

from custom_components.samsung_familyhub_fridge.samsung_browser_login import (
    ENTRY_POINT_URL,
    REDIRECT_URI,
    SamsungBrowserLogin,
    code_challenge,
    decrypt_value,
    encrypt_svc_param,
)

AUTH_SERVER = "us-auth2.samsungosp.com"


def _ecb_encrypt(plain: str, key: str) -> str:
    """What Samsung does to each value in the redirect (inverse of decrypt)."""
    padder = sym_padding.PKCS7(128).padder()
    data = padder.update(plain.encode()) + padder.finalize()
    enc = Cipher(algorithms.AES(key.encode()[:16]), modes.ECB()).encryptor()
    return (enc.update(data) + enc.finalize()).hex()


@pytest.fixture
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _b64_public(key) -> str:
    der = key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    return base64.b64encode(der).decode()


def _server_decrypt_svc(svc_param_urlenc: str, key) -> dict:
    """Do what Samsung's server does with svcParam, to check our encryption."""
    outer = json.loads(base64.b64decode(unquote(svc_param_urlenc)))
    aes_key = base64.b64decode(
        key.decrypt(base64.b64decode(outer["svcEncKY"]), rsa_padding.PKCS1v15())
    )
    assert len(aes_key) == 16
    dec = Cipher(
        algorithms.AES(aes_key), modes.CBC(bytes.fromhex(outer["svcEncIV"]))
    ).decryptor()
    padded = dec.update(base64.b64decode(outer["svcEncParam"])) + dec.finalize()
    unpadder = sym_padding.PKCS7(128).unpadder()
    return json.loads(unpadder.update(padded) + unpadder.finalize())


def test_code_challenge_is_s256_base64url():
    import hashlib

    verifier = "abc123XYZ" * 5
    expected = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).decode().rstrip("=")
    assert code_challenge(verifier) == expected
    assert "=" not in expected and "+" not in expected and "/" not in expected


def test_svc_param_round_trips_through_server_side_decryption(rsa_key):
    svc = {"clientId": "yfrtglt53o", "state": "abc"}
    out = encrypt_svc_param(svc, _b64_public(rsa_key), "1000")
    assert _server_decrypt_svc(out, rsa_key) == svc


def test_decrypt_value_inverts_samsung_encryption():
    assert decrypt_value(_ecb_encrypt("hello@example.com", "K" * 20), "K" * 20) == (
        "hello@example.com"
    )


def test_start_builds_sign_in_url(rsa_key):
    login = SamsungBrowserLogin()
    with rm.Mocker() as m:
        m.get(
            ENTRY_POINT_URL,
            json={
                "signInURI": "https://account.samsung.com/accounts/ANDROIDSDK/signInGate",
                "signOutURI": "https://account.samsung.com/x",
                "chkDoNum": "1000",
                "pkiPublicKey": _b64_public(rsa_key),
            },
        )
        url = login.start()
        assert m.last_request.headers["X-Osp-Appid"] == "yfrtglt53o"
    parsed = urlparse(url)
    assert parsed.path.endswith("/signInGate")
    q = parse_qs(parsed.query)
    assert q["mode"] == ["C"]
    assert q["locale"] == ["en-US"]
    # parse_qs already URL-decoded svcParam once; re-quote for the helper.
    from urllib.parse import quote
    svc = _server_decrypt_svc(quote(q["svcParam"][0], safe=""), rsa_key)
    assert svc["clientId"] == "yfrtglt53o"
    assert svc["redirect_uri"] == REDIRECT_URI
    assert svc["state"] == login.state
    assert svc["code_challenge"] == code_challenge(login.code_verifier)
    assert svc["deviceUniqueID"] == login.device_id


def _redirect_for(login: SamsungBrowserLogin, state_key="S" * 20) -> str:
    query = {
        "code": _ecb_encrypt("AUTH-CODE", state_key),
        "state": _ecb_encrypt(state_key, login.state),
        "auth_server_url": _ecb_encrypt(AUTH_SERVER, state_key),
        "api_server_url": _ecb_encrypt("us-api.samsungosp.com", state_key),
        "retValue": _ecb_encrypt("user@example.com", state_key),
    }
    return f"{REDIRECT_URI}?{urlencode(query)}"


def _mock_token_endpoints(m, privacy_retry=False):
    base = f"https://{AUTH_SERVER}"
    m.post(f"{base}/auth/oauth2/authenticate",
           json={"userauth_token": "UAT", "userId": "u1"})
    if privacy_retry:
        m.get(f"{base}/auth/oauth2/v2/authorize", [
            {"json": {"code": None, "privacyAccepted": "N"}},
            {"json": {"code": "IOT-CODE"}},
        ])
    else:
        m.get(f"{base}/auth/oauth2/v2/authorize", json={"code": "IOT-CODE"})
    m.post(f"{base}/auth/oauth2/token",
           json={"access_token": "IOT-AT", "refresh_token": "IOT-RT"})


def test_finish_exchanges_redirect_for_iot_tokens():
    login = SamsungBrowserLogin()
    with rm.Mocker() as m:
        _mock_token_endpoints(m)
        creds = login.finish(_redirect_for(login))
        auth_req = m.request_history[0]
        form = parse_qs(auth_req.text)
        assert form["code"] == ["AUTH-CODE"]
        assert form["username"] == ["user@example.com"]
        assert form["code_verifier"] == [login.code_verifier]
        authz = m.request_history[1].qs
        assert authz["client_id"] == ["6iado3s6jc"]
        assert authz["physical_address_text"] == [login.device_id]
        # requests_mock lower-cases values in .qs
        assert authz["code_challenge"] == [code_challenge(login.code_verifier).lower()]
    assert creds.access_token == "IOT-AT"
    assert creds.refresh_token == "IOT-RT"
    assert creds.auth_server_url == f"https://{AUTH_SERVER}"


def test_finish_retries_authorize_without_login_id():
    login = SamsungBrowserLogin()
    with rm.Mocker() as m:
        _mock_token_endpoints(m, privacy_retry=True)
        creds = login.finish(_redirect_for(login))
        authorize_calls = [r for r in m.request_history if "authorize" in r.url]
        assert "login_id" in authorize_calls[0].qs
        assert "login_id" not in authorize_calls[1].qs
    assert creds.refresh_token == "IOT-RT"


def test_finish_accepts_chrome_console_message():
    login = SamsungBrowserLogin()
    msg = (
        "Failed to launch '" + _redirect_for(login) + "' because the scheme "
        "does not have a registered handler."
    )
    with rm.Mocker() as m:
        _mock_token_endpoints(m)
        assert login.finish(msg).refresh_token == "IOT-RT"


def test_finish_rejects_incomplete_address():
    with pytest.raises(ValueError):
        SamsungBrowserLogin().finish("ms-app://s-1-15-2-123?code=abc")


def test_finish_with_other_attempts_redirect_fails_cleanly():
    from custom_components.samsung_familyhub_fridge.auth import AuthError

    other = SamsungBrowserLogin()
    with pytest.raises(AuthError):
        SamsungBrowserLogin().finish(_redirect_for(other))


# ---------------------------------------------------------------------------
# Config flow
# ---------------------------------------------------------------------------

FLOW = "custom_components.samsung_familyhub_fridge.config_flow"


def _make_flow():
    from custom_components.samsung_familyhub_fridge.config_flow import ConfigFlow

    hass = _HomeAssistant()
    entry = MagicMock()
    entry.entry_id = "st-entry"
    entry.title = "SmartThings"
    entry.source = "user"
    hass.config_entries = MagicMock()
    hass.config_entries.async_entries = MagicMock(return_value=[entry])
    flow = ConfigFlow()
    flow.hass = hass
    flow.async_create_entry = lambda title, data: {
        "type": "create_entry", "title": title, "data": data,
    }
    return flow


@pytest.mark.asyncio
async def test_oauth_step_with_browser_sign_in_goes_to_browser_step():
    flow = _make_flow()
    validated = {"auth_mode": "oauth", "linked_smartthings_entry_id": "st-entry",
                 "device_id": "fridge-1"}
    with patch(f"{FLOW}._validate_oauth", AsyncMock(return_value=validated)), \
         patch(f"{FLOW}.SamsungBrowserLogin") as MockLogin:
        MockLogin.return_value.start.return_value = "https://account.samsung.com/signin?x"
        result = await flow.async_step_oauth(
            user_input={"linked_smartthings_entry_id": "st-entry",
                        "samsung_browser_login": True}
        )
        assert result["step_id"] == "samsung_browser"
        assert "https://account.samsung.com/signin?x" in (
            result["description_placeholders"]["sign_in_url"]
        )

        creds = MagicMock(refresh_token="IOT-RT",
                          auth_server_url="https://us-auth2.samsungosp.com")
        MockLogin.return_value.finish.return_value = creds
        result = await flow.async_step_samsung_browser(
            {"redirect_url": "ms-app://s-1-15-2-1?code=a"}
        )
    assert result["type"] == "create_entry"
    assert result["data"]["device_id"] == "fridge-1"
    assert result["data"]["samsung_iot_refresh_token"] == "IOT-RT"
    assert result["data"]["samsung_iot_auth_server"] == "https://us-auth2.samsungosp.com"


@pytest.mark.asyncio
async def test_browser_step_bad_address_shows_error():
    flow = _make_flow()
    flow._pending_entry = ("t", {"auth_mode": "oauth"})
    login = MagicMock()
    login.finish.side_effect = ValueError("missing code")
    flow._browser_login = login
    flow._browser_url = "https://x"
    result = await flow.async_step_samsung_browser({"redirect_url": "nonsense"})
    assert result["step_id"] == "samsung_browser"
    assert result["errors"]["redirect_url"] == "invalid_samsung_redirect"


@pytest.mark.asyncio
async def test_browser_step_rejected_by_samsung_shows_error():
    from custom_components.samsung_familyhub_fridge.auth import AuthError

    flow = _make_flow()
    flow._pending_entry = ("t", {"auth_mode": "oauth"})
    login = MagicMock()
    login.finish.side_effect = AuthError("HTTP 400 nope")
    flow._browser_login = login
    flow._browser_url = "https://x"
    result = await flow.async_step_samsung_browser({"redirect_url": "ms-app://x?code=1"})
    assert result["errors"]["base"] == "samsung_browser_failed"
