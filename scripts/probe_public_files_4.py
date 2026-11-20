#!/usr/bin/env python3
"""Probe round 4: does the fridge report any imageId anywhere?

Read-only. Searches the full device status and recent event history for any
image/picture/file/url-like field, then tries every image-related ID found on
/v1/images/{id}. Token never printed; IDs and URL queries masked.
"""
from __future__ import annotations

import json
import os
import re
import sys

import requests

API = "https://api.smartthings.com/v1"
VIEW_INSIDE = "samsungce.viewInside"
token = (os.environ.get("ST_TOKEN") or "").strip()
if not token:
    sys.exit("No token")
AUTH = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
KEY_RE = re.compile(r"image|img|picture|photo|file|media|url|uri", re.I)
ID_RE = re.compile(r"^[0-9a-fA-F-]{16,}$")
hide = []


def mask(v):
    if isinstance(v, str) and v and v not in hide:
        hide.append(v)
        print(f"::add-mask::{v}")


def clean(text, limit=400):
    text = re.sub(r'(https?://[^\s"\'?]+)\?[^\s"\']*', r"\1?<query hidden>", str(text))
    for h in sorted(hide, key=len, reverse=True):
        text = text.replace(h, "<id>")
    return re.sub(r"\s+", " ", text)[:limit]


def walk(obj, path=""):
    """Yield (path, key, value) for every leaf."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from walk(v, f"{path}.{k}" if path else k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from walk(v, f"{path}[{i}]")
    else:
        yield path, path.rsplit(".", 1)[-1], obj


def interesting(data, label):
    """Print image/url-like leaves; return candidate IDs from image-ish keys."""
    candidates, hits = set(), 0
    for path, key, value in walk(data):
        is_url = isinstance(value, str) and value.startswith(("http://", "https://"))
        if KEY_RE.search(path) or is_url:
            hits += 1
            if isinstance(value, str) and ID_RE.match(value):
                mask(value)
                if re.search(r"image|img|picture|photo|file|media", path, re.I):
                    candidates.add(value)
            print(f"   [{label}] {clean(path, 160)} = {clean(json.dumps(value), 200)}")
    if not hits:
        print(f"   [{label}] no image/file/url-like fields")
    return candidates


r = requests.get(f"{API}/devices", headers=AUTH, timeout=30)
if r.status_code == 401:
    sys.exit("Token rejected (revoked or expired?)")
dev = next(d for d in r.json()["items"]
           if any(any(c.get("id") == VIEW_INSIDE for c in comp.get("capabilities") or [])
                  for comp in d.get("components") or []))
fridge = dev["deviceId"]
for k in ("deviceId", "locationId", "roomId", "ownerId"):
    mask(dev.get(k))

candidates = set()

print("== 1. Device description")
candidates |= interesting(dev, "device")

print("\n== 2. Full device status (all components)")
s = requests.get(f"{API}/devices/{fridge}/status", headers=AUTH, timeout=30)
print("   status:", s.status_code, "bytes:", len(s.content))
if s.ok:
    candidates |= interesting(s.json(), "status")

print("\n== 3. Recent events")
for name, url, params in (
    ("devices/{d}/events", f"{API}/devices/{fridge}/events", {"max": 200}),
    ("history/devices", f"{API}/history/devices",
     {"deviceId": fridge, "locationId": dev.get("locationId"), "limit": 200}),
):
    e = requests.get(url, headers=AUTH, params=params, timeout=30)
    print(f"-- {name}: {e.status_code} bytes {len(e.content)}")
    if not e.ok:
        print("   ", clean(e.text, 300))
        continue
    body = e.json()
    items = body.get("items") if isinstance(body, dict) else body
    items = items if isinstance(items, list) else []
    caps = {}
    for it in items:
        cap = it.get("capability") or it.get("capabilityId") or "?"
        caps[cap] = caps.get(cap, 0) + 1
    print(f"   {len(items)} events by capability:", clean(json.dumps(caps), 600))
    candidates |= interesting(body, name)

print(f"\n== 4. Trying {len(candidates)} image-related ID(s) on /v1/images/{{id}}")
for cid in sorted(candidates):
    for acc in ("application/json", "*/*"):
        ri = requests.get(f"{API}/images/{cid}", headers={**AUTH, "Accept": acc}, timeout=30)
        body = "<JPEG IMAGE>" if ri.content[:2] == b"\xff\xd8" else clean(ri.text, 300)
        print(f"   {ri.status_code} Accept {acc:16} {body}")
if not candidates:
    print("   none found - the fridge reports no image IDs")
