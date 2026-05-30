"""Integration tests: HTTP 400 'No samsung id available' → AuthenticationError.

Run with:
    python3 -m pytest tests/ -k 'samsung_id' -v
"""

import json

import pytest
import requests_mock as rm

from tests.conftest import _HomeAssistant

from custom_components.samsung_familyhub_fridge.api import (
    AuthenticationError,
    FamilyHub,
)

FILE_LINKS_BASE = "https://client.smartthings.com/udo/file_links/"
FILE_LINKS_RE = r"https://client\.smartthings\.com/udo/file_links/.+"

NO_SAMSUNG_ID_BODY = {
    "error": {
        "code": "BadRequestError",
        "message": "No samsung id available",
    }
}

UNRELATED_400_BODY = {
    "error": {
        "code": "BadRequestError",
        "message": "Some other bad request problem",
    }
}


@pytest.fixture
def hass():
    return _HomeAssistant()


@pytest.fixture
def hub(hass):
    hub = FamilyHub(hass, token="test-token", device_id="device-abc")
    hub._current_device_status = {
        "samsungce.viewInside": {
            "contents": {"value": [{"fileId": "file-001"}]}
        }
    }
    return hub


class TestSamsungIdAuthError:
    """HTTP 400 with 'No samsung id available' must raise AuthenticationError."""

    def test_samsung_id_400_raises_auth_error_from_download_images(self, hub):
        """Matching 400 body from file_links endpoint raises AuthenticationError."""
        with rm.Mocker() as m:
            m.get(rm.ANY, status_code=400, json=NO_SAMSUNG_ID_BODY)
            with pytest.raises(AuthenticationError, match="No samsung id available"):
                hub.download_images()

    def test_samsung_id_400_unrelated_body_does_not_raise_auth_error(self, hub):
        """Non-matching 400 body must NOT raise AuthenticationError (falls through to warning)."""
        with rm.Mocker() as m:
            m.get(rm.ANY, status_code=400, json=UNRELATED_400_BODY)
            # Must not raise AuthenticationError; warning is logged instead
            hub.download_images()

    def test_samsung_id_400_malformed_json_does_not_raise_auth_error(self, hub):
        """Malformed JSON body on 400 must not raise AuthenticationError."""
        with rm.Mocker() as m:
            m.get(rm.ANY, status_code=400, text="not json at all")
            # Must not raise AuthenticationError; warning is logged instead
            hub.download_images()

    def test_samsung_id_check_response_directly_raises(self, hub):
        """_check_response raises AuthenticationError for the matching 400 body."""
        import requests

        resp = requests.models.Response()
        resp.status_code = 400
        resp._content = json.dumps(NO_SAMSUNG_ID_BODY).encode()
        resp.encoding = "utf-8"

        with pytest.raises(AuthenticationError, match="No samsung id available"):
            hub._check_response(resp)

    def test_samsung_id_check_response_unrelated_400_does_not_raise(self, hub):
        """_check_response does NOT raise AuthenticationError for an unrelated 400."""
        import requests

        resp = requests.models.Response()
        resp.status_code = 400
        resp._content = json.dumps(UNRELATED_400_BODY).encode()
        resp.encoding = "utf-8"

        hub._check_response(resp)  # no exception
