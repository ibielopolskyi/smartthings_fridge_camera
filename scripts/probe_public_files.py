#!/usr/bin/env python3
"""One-off probe: can fridge pictures be downloaded via SmartThings' public API?

Run by the 'Probe public picture download' workflow with a SmartThings token
from a repository secret. Tries several addresses x Accept headers and prints
only status codes, content types, sizes and short reply excerpts. The token,
device ID and picture IDs are never printed (IDs are masked in the log).
"""
from __future__ import annotations

import os
import sys
import time

import requests

API = "https://api.smartthings.com/v1"
CID = "5Hic3rk1FP"  # SmartThings app client id used by the private endpoint
VIEW_INSIDE = "samsungce.viewInside"

token = (os.environ.get("ST_TOKEN") or "").strip()
if not token:
    sys.exit("No token: add a repository secret named SAMSUNG or SMARTTHINGS_TOKEN")
AUTH = {"Authorization": f"Bearer {token}"}
secrets_to_hide: list[str] = []


def mask(value: str) -> None:
    """Hide a value in GitHub's log and in our own excerpts."""
    if value:
        secrets_to_hide.append(value)
        print(f"::add-mask::{value}")


def clean(text: str) -> str:
    for s in secrets_to_hide:
        text = text.replace(s, "<id>")
    return text.replace("\n", " ")[:200]


def get(url, **kw):
    return requests.get(url, timeout=30, **kw)


# 1. Find the fridge
r = get(f"{API}/devices", headers=AUTH)
print("== device list:", r.status_code)
r.raise_for_status()
fridge = None
for d in r.json().get("items", []):
    for comp in d.get("components") or []:
        if any(c.get("id") == VIEW_INSIDE for c in comp.get("capabilities") or []):
            fridge = d["deviceId"]
            break
    if fridge:
        break
if not fridge:
    sys.exit("No fridge with samsungce.viewInside found for this token")
mask(fridge)
print("fridge found")


def picture_ids():
    s = get(f"{API}/devices/{fridge}/components/main/capabilities/{VIEW_INSIDE}/status",
            headers=AUTH)
    print("== viewInside status:", s.status_code)
    body = s.json()
    items = (body.get("contents") or {}).get("value") or []
    return [i["fileId"] for i in items if isinstance(i, dict) and i.get("fileId")], items


ids, items = picture_ids()
expires = [i.get("expiredTime", "") for i in items]
print("pictures:", len(ids), "link expiry:", expires)
expired = not ids or any(e and e < time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) for e in expires)
if expired:
    print("== links expired; sending the refresh command and waiting 20 s")
    c = requests.post(
        f"{API}/devices/{fridge}/commands", headers=AUTH, timeout=30,
        json={"commands": [{"component": "main", "capability": "execute",
                            "command": "execute",
                            "arguments": ["/udo/contents/provider/vs/0",
                                          {"x.com.samsung.da.control":
                                           {"x.com.samsung.da.command": "refresh"}}]}]})
    print("refresh command:", c.status_code)
    time.sleep(20)
    ids, items = picture_ids()
    print("pictures:", len(ids), "link expiry:", [i.get("expiredTime") for i in items])
if not ids:
    sys.exit("No picture IDs available")
for i in ids:
    mask(i)
fid = ids[0]

paths = {
    "public devices/{d}/files/{f}": f"{API}/devices/{fridge}/files/{fid}",
    "public same + cid&di": f"{API}/devices/{fridge}/files/{fid}?cid={CID}&di={fridge}",
    "public files/{f}": f"{API}/files/{fid}",
    "public components/main/files/{f}": f"{API}/devices/{fridge}/components/main/files/{fid}",
    "public udo/file_links/{f}": f"https://api.smartthings.com/udo/file_links/{fid}?cid={CID}&di={fridge}",
    "PRIVATE client.smartthings.com udo/file_links (for comparison)":
        f"https://client.smartthings.com/udo/file_links/{fid}?cid={CID}&di={fridge}",
}
accepts = [None, "*/*", "image/jpeg", "image/*", "application/octet-stream",
           "application/json", "application/vnd.smartthings+json;v=1"]

print("\n== results (status, content-type, bytes, excerpt)")
for name, url in paths.items():
    print(f"\n-- {name}")
    for accept in accepts:
        headers = dict(AUTH)
        if accept:
            headers["Accept"] = accept
        try:
            resp = get(url, headers=headers)
        except requests.RequestException as err:
            print(f"   Accept={accept!s:40} ERROR {clean(str(err))}")
            continue
        ctype = resp.headers.get("content-type", "")
        is_image = resp.content[:2] == b"\xff\xd8"
        excerpt = "<JPEG IMAGE>" if is_image else clean(resp.text)
        print(f"   Accept={accept!s:40} {resp.status_code} {ctype[:40]:40} "
              f"{len(resp.content):7}  {excerpt}")
