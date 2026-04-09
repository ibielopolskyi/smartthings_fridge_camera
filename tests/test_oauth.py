"""Unit tests for the SmartThingsOAuth module."""

import pytest
import requests_mock as rm

from custom_components.samsung_familyhub_fridge.auth import (
    SmartThingsOAuth,
    OAuthCredentials,
    AUTHORIZE_URL,
    TOKEN_URL,
    _generate_pkce_pair,
)


@pytest.fixture
def oauth():
    return SmartThingsOAuth(
        client_id="test-client-id",
        client_secret="test-client-secret",
        redirect_uri="https://httpbin.org/get",
        scopes="r:devices:* w:devices:*",
    )


class TestPKCE:
    def test_pkce_pair_format(self):
        verifier, challenge = _generate_pkce_pair()
        assert isinstance(verifier, str)
        assert isinstance(challenge, str)
        assert len(verifier) > 20
        assert len(challenge) > 20
        assert verifier != challenge

    def test_pkce_pair_unique(self):
        v1, c1 = _generate_pkce_pair()
        v2, c2 = _generate_pkce_pair()
        assert v1 != v2
        assert c1 != c2


class TestAuthorizationURL:
    def test_url_contains_required_params(self, oauth):
        url = oauth.get_authorization_url()
        assert AUTHORIZE_URL in url
        assert "client_id=test-client-id" in url
        assert "response_type=code" in url
        assert "redirect_uri=" in url
        assert "scope=" in url
        assert "code_challenge=" in url
        assert "code_challenge_method=S256" in url

    def test_generates_code_verifier(self, oauth):
        assert oauth._code_verifier == ""
        oauth.get_authorization_url()
        assert oauth._code_verifier != ""


class TestExtractCode:
    def test_extract_from_full_url(self):
        url = "https://httpbin.org/get?code=abc123&state=xyz"
        code = SmartThingsOAuth.extract_code_from_redirect(url)
        assert code == "abc123"

    def test_extract_raises_on_missing_code(self):
        url = "https://httpbin.org/get?state=xyz"
        with pytest.raises(ValueError, match="No 'code' parameter"):
            SmartThingsOAuth.extract_code_from_redirect(url)


class TestTokenExchange:
    def test_exchange_code_success(self, oauth):
        oauth.get_authorization_url()  # sets _code_verifier

        token_response = {
            "access_token": "new-access-token",
            "refresh_token": "new-refresh-token",
            "token_type": "bearer",
            "expires_in": 86399,
            "scope": "r:devices:* w:devices:*",
            "installed_app_id": "app-123",
        }

        with rm.Mocker() as m:
            m.post(TOKEN_URL, json=token_response)
            creds = oauth.exchange_code("auth-code-xyz")

        assert creds.access_token == "new-access-token"
        assert creds.refresh_token == "new-refresh-token"
        assert creds.expires_in == 86399
        assert creds.scope == "r:devices:* w:devices:*"
        assert creds.installed_app_id == "app-123"

    def test_exchange_code_sends_correct_params(self, oauth):
        oauth.get_authorization_url()

        with rm.Mocker() as m:
            m.post(TOKEN_URL, json={
                "access_token": "a", "refresh_token": "r",
            })
            oauth.exchange_code("the-code")

        body = m.last_request.body
        assert "grant_type=authorization_code" in body
        assert "client_id=test-client-id" in body
        assert "code=the-code" in body
        assert "code_verifier=" in body

    def test_exchange_code_uses_basic_auth(self, oauth):
        oauth.get_authorization_url()

        with rm.Mocker() as m:
            m.post(TOKEN_URL, json={
                "access_token": "a", "refresh_token": "r",
            })
            oauth.exchange_code("c")

        auth_header = m.last_request.headers.get("Authorization", "")
        assert auth_header.startswith("Basic ")

    def test_exchange_code_raises_on_error(self, oauth):
        oauth.get_authorization_url()

        with rm.Mocker() as m:
            m.post(TOKEN_URL, status_code=400, json={"error": "invalid_grant"})
            with pytest.raises(Exception):
                oauth.exchange_code("bad-code")


class TestRefresh:
    def test_refresh_success(self, oauth):
        token_response = {
            "access_token": "refreshed-access",
            "refresh_token": "refreshed-refresh",
            "token_type": "bearer",
            "expires_in": 86399,
            "scope": "r:devices:*",
        }
        with rm.Mocker() as m:
            m.post(TOKEN_URL, json=token_response)
            creds = oauth.refresh("old-refresh-token")

        assert creds.access_token == "refreshed-access"
        assert creds.refresh_token == "refreshed-refresh"
        assert creds.expires_in == 86399

    def test_refresh_sends_correct_params(self, oauth):
        with rm.Mocker() as m:
            m.post(TOKEN_URL, json={
                "access_token": "a", "refresh_token": "r",
            })
            oauth.refresh("my-refresh-token")

        body = m.last_request.body
        assert "grant_type=refresh_token" in body
        assert "client_id=test-client-id" in body
        assert "refresh_token=my-refresh-token" in body

    def test_refresh_uses_basic_auth(self, oauth):
        with rm.Mocker() as m:
            m.post(TOKEN_URL, json={
                "access_token": "a", "refresh_token": "r",
            })
            oauth.refresh("tok")

        auth_header = m.last_request.headers.get("Authorization", "")
        assert auth_header.startswith("Basic ")

    def test_refresh_raises_on_401(self, oauth):
        with rm.Mocker() as m:
            m.post(TOKEN_URL, status_code=401)
            with pytest.raises(Exception):
                oauth.refresh("expired-refresh-token")


class TestOAuthCredentials:
    def test_dataclass_fields(self):
        creds = OAuthCredentials(
            access_token="at",
            refresh_token="rt",
            token_type="bearer",
            expires_in=3600,
            scope="r:devices:*",
            installed_app_id="app-1",
        )
        assert creds.access_token == "at"
        assert creds.refresh_token == "rt"
        assert creds.token_type == "bearer"
        assert creds.expires_in == 3600
