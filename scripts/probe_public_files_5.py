#!/usr/bin/env python3
"""Probe round 5: what does the private picture server actually return?

- Refresh picture links with the official samsungce.viewInside.refreshAll
  command if they've expired.
- Call client.smartthings.com/udo/file_links WITHOUT following redirects and
  show status, redirect target (host/path only) and headers.
- If it redirects, fetch the target with and without the token.
- Try any new IDs from headers / redirect path on the public API.
- Check which parts the private call needs (token, cid, di).
Token never printed; IDs masked; URL query strings hidden.
"""
from __future__ import annotations

import os
import re
import sys
import time
from urllib.parse import urlparse

import requests

API = "https://api.smartthings.com/v1"
PRIVATE = "https://client.smartthings.com/udo/file_links"
CID = "5Hic3rk1FP"
VIEW_INSIDE = "samsungce.viewInside"
token = (os.environ.get("ST_TOKEN") or "").strip()
if not token:
    sys.exit("No token")
AUTH = {"Authorization": f"Bearer {token}"}
hide = []
UUIDISH = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def mask(v):
    if isinstance(v, str) and v and v not in hide:
        hide.append(v)
        print(f"::add-mask::{v}")


def clean(text, limit=300):
    text = re.sub(r'(https?://[^\s"\'?]+)\?[^\s"\']*', r"\1?<query hidden>", str(text))
    for h in sorted(hide, key=len, reverse=True):
        text = text.replace(h, "<id>")
    return re.sub(r"\s+", " ", text)[:limit]


def describe(resp):
    body = "<JPEG IMAGE>" if resp.content[:2] == b"\xff\xd8" else clean(resp.text, 200)
    return f"{resp.status_code} {resp.headers.get('content-type', '')[:30]} {len(resp.content)}B {body}"


# ---- fridge + fresh picture IDs ---------------------------------------------
r = requests.get(f"{API}/devices", headers={**AUTH, "Accept": "application/json"}, timeout=30)
if r.status_code == 401:
    sys.exit("Token rejected (revoked or expired?)")
dev = next(d for d in r.json()["items"]
           if any(any(c.get("id") == VIEW_INSIDE for c in comp.get("capabilities") or [])
                  for comp in d.get("components") or []))
fridge = dev["deviceId"]
mask(fridge)


def view_inside():
    s = requests.get(f"{API}/devices/{fridge}/components/main/capabilities/{VIEW_INSIDE}/status",
                     headers=AUTH, timeout=30).json()
    return (s.get("contents") or {}).get("value") or []


items = view_inside()
now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
if not items or any((i.get("expiredTime") or "") < now for i in items):
    print("== links expired: trying the official samsungce.viewInside.refreshAll command")
    c = requests.post(f"{API}/devices/{fridge}/commands", headers=AUTH, timeout=30,
                      json={"commands": [{"component": "main", "capability": VIEW_INSIDE,
                                          "command": "refreshAll", "arguments": []}]})
    print("   refreshAll:", c.status_code, clean(c.text, 200))
    time.sleep(20)
    items = view_inside()
    print("   link expiry now:", [i.get("expiredTime") for i in items],
          "(fresh)" if items and all((i.get("expiredTime") or "") > now for i in items) else "(NOT refreshed)")
ids = [i["fileId"] for i in items if i.get("fileId")]
for i in ids:
    mask(i)
fid = ids[0]
url = f"{PRIVATE}/{fid}?cid={CID}&di={fridge}"

# ---- 1. private call without following redirects --------------------------
print("\n== 1. Private server, redirects NOT followed")
p = requests.get(url, headers=AUTH, timeout=30, allow_redirects=False)
print("   ", describe(p))
print("    headers:")
for k, v in p.headers.items():
    print(f"      {k}: {clean(v, 160)}")
location = p.headers.get("Location")
new_ids = set()
for v in list(p.headers.values()) + [location or ""]:
    for m in UUIDISH.findall(v):
        if m not in ids and m != fridge:
            new_ids.add(m)

# ---- 2. follow the redirect, if any ---------------------------------------
if location:
    target = urlparse(location)
    print(f"\n== 2. Redirect target: {target.scheme}://{target.netloc}{clean(target.path, 120)}")
    print("    query parameter names:", sorted(dict(q.split('=', 1)[0:1] * 2 for q in target.query.split('&') if q)))
    for label, headers in (("no login", {}), ("with token", AUTH)):
        t = requests.get(location, headers=headers, timeout=30)
        print(f"    {label:10}: {describe(t)}")
else:
    print("\n== 2. No redirect: the private server sends the picture itself")
    f = requests.get(url, headers=AUTH, timeout=30)
    print("    followed anyway:", describe(f), "| redirects:", [h.status_code for h in f.history])

# ---- 3. new IDs on the public API ------------------------------------------
print(f"\n== 3. New IDs found in the private reply: {len(new_ids)}")
for nid in sorted(new_ids):
    mask(nid)
    for name, purl in (("images/{id}", f"{API}/images/{nid}"),
                       ("devices/{d}/files/{id}", f"{API}/devices/{fridge}/files/{nid}")):
        rr = requests.get(purl, headers={**AUTH, "Accept": "application/json"}, timeout=30)
        print(f"    public {name:24} {describe(rr)}")

# ---- 4. what does the private call need? ----------------------------------
print("\n== 4. Private server requirements")
checks = {
    "no login": (f"{PRIVATE}/{fid}?cid={CID}&di={fridge}", {}),
    "no cid": (f"{PRIVATE}/{fid}?di={fridge}", AUTH),
    "no di": (f"{PRIVATE}/{fid}?cid={CID}", AUTH),
    "no cid, no di": (f"{PRIVATE}/{fid}", AUTH),
}
for label, (u, h) in checks.items():
    rr = requests.get(u, headers=h, timeout=30)
    print(f"    {label:14} {describe(rr)}")
