#!/usr/bin/env python3
"""Probe round 3: the guessed picture addresses again, with full replies.

Read-only (sends nothing to the fridge). Token never printed; device and
picture IDs masked.
"""
from __future__ import annotations

import os
import re
import sys

import requests

API = "https://api.smartthings.com/v1"
VIEW_INSIDE = "samsungce.viewInside"
token = (os.environ.get("ST_TOKEN") or "").strip()
if not token:
    sys.exit("No token")
AUTH = {"Authorization": f"Bearer {token}"}
hide = []


def mask(v):
    if v:
        hide.append(v)
        print(f"::add-mask::{v}")


def clean(text):
    text = re.sub(r'(https?://[^\s"\'?]+)\?[^\s"\']*', r"\1?<query hidden>", text)
    for h in hide:
        text = text.replace(h, "<id>")
    return re.sub(r"\s+", " ", text)


r = requests.get(f"{API}/devices", headers=AUTH, timeout=30)
if r.status_code == 401:
    sys.exit("Token rejected (revoked or expired?)")
dev = next(d for d in r.json()["items"]
           if any(any(c.get("id") == VIEW_INSIDE for c in comp.get("capabilities") or [])
                  for comp in d.get("components") or []))
fridge = dev["deviceId"]
mask(fridge)
st = requests.get(f"{API}/devices/{fridge}/components/main/capabilities/{VIEW_INSIDE}/status",
                  headers=AUTH, timeout=30).json()
ids = [i["fileId"] for i in (st.get("contents") or {}).get("value") or [] if i.get("fileId")]
for i in ids:
    mask(i)
fid = ids[0]

paths = {
    "devices/{d}/files/{f}": f"{API}/devices/{fridge}/files/{fid}",
    "devices/{d}/files": f"{API}/devices/{fridge}/files",
    "devices/{d}/media/{f}": f"{API}/devices/{fridge}/media/{fid}",
    "devices/{d}/images/{f}": f"{API}/devices/{fridge}/images/{fid}",
    "images/{f}": f"{API}/images/{fid}",
    "files/{f}": f"{API}/files/{fid}",
    "viewInside/files/{f}": f"{API}/devices/{fridge}/components/main/capabilities/{VIEW_INSIDE}/files/{fid}",
}
for name, url in paths.items():
    for acc in ("application/json", "*/*", "image/jpeg"):
        resp = requests.get(url, headers={**AUTH, "Accept": acc}, timeout=30)
        body = "<JPEG IMAGE>" if resp.content[:2] == b"\xff\xd8" else clean(resp.text)
        print(f"{resp.status_code} | Accept {acc:16} | {name}\n    {body}")
