"""Integration tests: HTTP 400 'No samsung id available' raises AuthenticationError.

Run with:
    python3 -m pytest tests/ -k 'samsung_id' -v
"""

import pytest
import requests_mock as rm

from tests.conftest import _HomeAssistant

from custom_components.samsung_familyhub_fridge.api import (
    AuthenticationError,
    FamilyHub,
)

FILE_LINK_BASE = "https://client.smartthings.com/udo/file_links/"


@pytest.fixture
def hass():
    return _HomeAssistant()


@pytest.fixture
def hub(hass):
    hub = FamilyHub(hass, token="test-token", device_id="device-123")
    hub._current_device_status = {
        "samsungce.viewInside": {
            "contents": {"value": [{"fileId": "file-abc"}]}
        }
    }
    return hub


NO_SAMSUNG_ID_BODY = {
    "error": {
        "code": "BadRequestError",
        "message": "No samsung id available",
    }
}

UNRELATED_400_BODY = {
    "error": {
        "code": "BadRequestError",
        "message": "Some unrelated bad request reason",
    }
}


class TestSamsungIdAuthError:
    """HTTP 400 with 'No samsung id available' must raise AuthenticationError."""

    def test_no_samsung_id_400_raises_auth_error(self, hub):
        """HTTP 400 + 'No samsung id available' body raises AuthenticationError."""
        with rm.Mocker() as m:
            m.get(rm.ANY, status_code=400, json=NO_SAMSUNG_ID_BODY)
            with pytest.raises(AuthenticationError, match="No samsung id available"):
                hub.download_images()

    def test_unrelated_400_does_not_raise_auth_error(self, hub):
        """HTTP 400 with an unrelated body must NOT raise AuthenticationError."""
        with rm.Mocker() as m:
            m.get(rm.ANY, status_code=400, json=UNRELATED_400_BODY)
            # Must not raise AuthenticationError — warning is logged instead.
            try:
                hub.download_images()
            except AuthenticationError:
                pytest.fail("AuthenticationError was raised for an unrelated 400 body")

    def test_no_samsung_id_400_malformed_body_does_not_raise(self, hub):
        """HTTP 400 with non-JSON body falls through to warning, not exception."""
        with rm.Mocker() as m:
            m.get(rm.ANY, status_code=400, text="not json at all")
            # Must not raise AuthenticationError — malformed bodies fall through.
            try:
                hub.download_images()
            except AuthenticationError:
                pytest.fail("AuthenticationError was raised for a non-JSON 400 body")

    def test_check_response_direct_no_samsung_id(self, hub):
        """Direct _check_response call: 400 + no-samsung-id body raises."""
        import requests

        resp = requests.models.Response()
        resp.status_code = 400
        resp._content = b'{"error":{"code":"BadRequestError","message":"No samsung id available"}}'
        resp.encoding = "utf-8"
        with pytest.raises(AuthenticationError, match="No samsung id available"):
            hub._check_response(resp)

    def test_check_response_direct_unrelated_400(self, hub):
        """Direct _check_response call: 400 + unrelated body does not raise."""
        import requests

        resp = requests.models.Response()
        resp.status_code = 400
        resp._content = b'{"error":{"code":"BadRequestError","message":"Something else"}}'
        resp.encoding = "utf-8"
        hub._check_response(resp)  # no exception
