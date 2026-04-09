"""Unit tests for the SmartThingsOAuth and SamsungAccountAuth modules."""

import pytest
import requests_mock as rm

from custom_components.samsung_familyhub_fridge.auth import (
    SmartThingsOAuth,
    SamsungAccountAuth,
    OAuthCredentials,
    LoginCredentials,
    AuthError,
    AUTHORIZE_URL,
    TOKEN_URL,
    SAMSUNG_AUTH_URL,
    SAMSUNG_TOKEN_URL,
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


# ======================================================================
# SamsungAccountAuth (headless email/password login)
# ======================================================================


@pytest.fixture
def samsung_auth():
    return SamsungAccountAuth(
        email="user@example.com",
        password="my-password",
        signin_client_id="test-signin-id",
        signin_client_secret="test-signin-secret",
        client_id="test-client-id",
    )


class TestSamsungAccountLogin:
    """Test the two-step headless Samsung Account login flow."""

    def test_login_success(self, samsung_auth):
        with rm.Mocker() as m:
            m.post(SAMSUNG_AUTH_URL, json={"userauth_token": "utoken-123"})
            m.post(
                SAMSUNG_TOKEN_URL,
                json={"token": {"access_token": "bearer-abc"}},
            )
            creds = samsung_auth.login()

        assert creds.access_token == "bearer-abc"
        assert creds.userauth_token == "utoken-123"

    def test_step1_sends_email_and_password(self, samsung_auth):
        with rm.Mocker() as m:
            m.post(SAMSUNG_AUTH_URL, json={"userauth_token": "tok"})
            m.post(SAMSUNG_TOKEN_URL, json={"token": {"access_token": "a"}})
            samsung_auth.login()

        step1_body = m.request_history[0].body
        assert "login_id=user%40example.com" in step1_body or "login_id=user@example.com" in step1_body
        assert "password=my-password" in step1_body
        assert "signin_client_id=test-signin-id" in step1_body
        assert "signin_client_secret=test-signin-secret" in step1_body
        assert "login_id_type=email_id" in step1_body

    def test_step2_sends_userauth_token(self, samsung_auth):
        with rm.Mocker() as m:
            m.post(SAMSUNG_AUTH_URL, json={"userauth_token": "utoken-xyz"})
            m.post(SAMSUNG_TOKEN_URL, json={"token": {"access_token": "a"}})
            samsung_auth.login()

        step2_body = m.request_history[1].body
        assert "userauth_token=utoken-xyz" in step2_body
        assert "client_id=test-client-id" in step2_body
        assert "com.samsung.android.oneconnect" in step2_body

    def test_step1_401_raises_auth_error(self, samsung_auth):
        with rm.Mocker() as m:
            m.post(SAMSUNG_AUTH_URL, status_code=401)
            with pytest.raises(AuthError, match="Invalid Samsung Account"):
                samsung_auth.login()

    def test_step1_403_raises_auth_error_with_2fa_hint(self, samsung_auth):
        with rm.Mocker() as m:
            m.post(SAMSUNG_AUTH_URL, status_code=403)
            with pytest.raises(AuthError, match="2FA"):
                samsung_auth.login()

    def test_step1_missing_userauth_token_raises(self, samsung_auth):
        with rm.Mocker() as m:
            m.post(SAMSUNG_AUTH_URL, json={"error": "some_error"})
            with pytest.raises(AuthError, match="userauth_token"):
                samsung_auth.login()

    def test_step2_missing_token_raises(self, samsung_auth):
        with rm.Mocker() as m:
            m.post(SAMSUNG_AUTH_URL, json={"userauth_token": "tok"})
            m.post(SAMSUNG_TOKEN_URL, json={"error": "invalid_grant"})
            with pytest.raises(AuthError, match="Token exchange failed"):
                samsung_auth.login()

    def test_step1_content_type_header(self, samsung_auth):
        with rm.Mocker() as m:
            m.post(SAMSUNG_AUTH_URL, json={"userauth_token": "tok"})
            m.post(SAMSUNG_TOKEN_URL, json={"token": {"access_token": "a"}})
            samsung_auth.login()

        assert (
            m.request_history[0].headers["Content-Type"]
            == "application/x-www-form-urlencoded;charset=UTF-8"
        )

    def test_device_id_derived_from_email(self):
        auth1 = SamsungAccountAuth(
            email="a@b.com", password="p",
            signin_client_id="c", signin_client_secret="s",
        )
        auth2 = SamsungAccountAuth(
            email="a@b.com", password="p",
            signin_client_id="c", signin_client_secret="s",
        )
        auth3 = SamsungAccountAuth(
            email="different@b.com", password="p",
            signin_client_id="c", signin_client_secret="s",
        )
        assert auth1._device_id == auth2._device_id
        assert auth1._device_id != auth3._device_id


class TestLoginCredentials:
    def test_dataclass_fields(self):
        creds = LoginCredentials(
            access_token="bearer-tok",
            userauth_token="uauth-tok",
        )
        assert creds.access_token == "bearer-tok"
        assert creds.userauth_token == "uauth-tok"
