"""Tests for the debug logging of camera details."""
from __future__ import annotations

import logging

import requests_mock as rm

from tests.conftest import _HomeAssistant

from custom_components.samsung_familyhub_fridge.api import (
    FamilyHub,
    camera_related_status,
    jpeg_size,
)


def _jpeg(width: int, height: int) -> bytes:
    """Minimal JPEG header: SOI, an APP0 segment, then SOF0 with the size."""
    app0 = b"\xff\xe0" + (16).to_bytes(2, "big") + b"JFIF\x00" + b"\x00" * 9
    sof0 = (
        b"\xff\xc0" + (17).to_bytes(2, "big") + b"\x08"
        + height.to_bytes(2, "big") + width.to_bytes(2, "big")
        + b"\x03" + b"\x00" * 9
    )
    return b"\xff\xd8" + app0 + sof0 + b"\xff\xd9"


def test_jpeg_size_reads_sof_header():
    assert jpeg_size(_jpeg(1062, 350)) == (1062, 350)


def test_jpeg_size_not_a_jpeg():
    assert jpeg_size(b"PNG....") is None
    assert jpeg_size(None) is None


def test_camera_related_status_searches_all_components():
    full = {"components": {
        "main": {"samsungce.viewInside": {"contents": {}}, "contactSensor": {}},
        "cooler": {"custom.fridgeMode": {}},
        "hub": {"imageCapture": {"image": {"value": "x"}}},
    }}
    found = camera_related_status(full)
    assert set(found) == {"main/samsungce.viewInside", "hub/imageCapture"}


def test_log_camera_details_logs_components(caplog):
    hub = FamilyHub(_HomeAssistant(), token="t", device_id="fridge-1")
    caplog.set_level(logging.DEBUG)
    with rm.Mocker() as m:
        m.get(
            "https://api.smartthings.com/v1/devices/fridge-1/status",
            json={"components": {
                "main": {"samsungce.viewInside": {"contents": {"value": [1]}}},
                "cvroom": {"samsungce.viewInside": {"contents": {"value": [2]}}},
            }},
        )
        hub.log_camera_details()
    assert "main/samsungce.viewInside" in caplog.text
    assert "cvroom/samsungce.viewInside" in caplog.text


def test_download_logs_picture_size(caplog):
    hub = FamilyHub(_HomeAssistant(), token="t", device_id="fridge-1")
    hub.set_current_device_status(
        {"samsungce.viewInside": {"contents": {"value": [{"fileId": "f1"}]}}}
    )
    hub.set_image_token("pat")
    caplog.set_level(logging.DEBUG)
    with rm.Mocker() as m:
        m.get(rm.ANY, content=_jpeg(1062, 350),
              headers={"content-type": "image/jpeg"})
        assert hub.download_images() is True
    assert "picture 0 is 1062x350 pixels" in caplog.text
