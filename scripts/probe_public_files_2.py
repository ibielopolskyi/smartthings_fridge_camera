#!/usr/bin/env python3
"""Second probe: look harder for a public SmartThings route to fridge pictures.

1. Fetch SmartThings' published OpenAPI description and list every path that
   looks file/image/media related.
2. Read the capability definitions (viewInside, fileTransfer, sendContents,
   execute) and the device's presentation.
3. Ask the fridge for its camera OCF resource through the public `execute`
   command and read the result back from execute.data.
4. Try a few more guessed paths, plus OPTIONS/HEAD on the files path.

Prints status codes and short excerpts only. The token is never printed;
device/picture IDs and URL query strings are masked.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time

import requests

API = "https://api.smartthings.com/v1"
VIEW_INSIDE = "samsungce.viewInside"
token = (os.environ.get("ST_TOKEN") or "").strip()
if not token:
    sys.exit("No token")
AUTH = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
hide: list[str] = []


def mask(v):
    if v:
        hide.append(v)
        print(f"::add-mask::{v}")


def clean(text, limit=300):
    text = re.sub(r'(https?://[^\s"\'?]+)\?[^\s"\']*', r"\1?<query hidden>", str(text))
    for h in hide:
        text = text.replace(h, "<id>")
    return text.replace("\n", " ")[:limit]


def get(url, **kw):
    kw.setdefault("headers", AUTH)
    return requests.get(url, timeout=30, **kw)


# ---- 1. OpenAPI description -------------------------------------------------
print("== 1. Published API description")
spec_urls = [
    "https://swagger.api.smartthings.com/public/st-api.yml",
    "https://swagger.api.smartthings.com/public/st-api.json",
    "https://developer.smartthings.com/api/public/st-api.yml",
    "https://api.smartthings.com/openapi.json",
    "https://raw.githubusercontent.com/SmartThingsDevelopers/api-docs/master/st-api.yml",
]
spec_text = None
for u in spec_urls:
    try:
        r = requests.get(u, timeout=30)
        print(f"   {r.status_code} {len(r.content):8} {u}")
        if r.ok and ("paths" in r.text[:200000]) and len(r.content) > 5000 and spec_text is None:
            spec_text = r.text
    except requests.RequestException as err:
        print(f"   ERR {u}: {clean(err, 120)}")
if spec_text:
    paths = sorted(set(re.findall(r"^\s{2}(/[A-Za-z0-9_{}\-/\.]+):\s*$", spec_text, re.M)))
    if not paths:  # JSON spec
        try:
            paths = sorted(json.loads(spec_text).get("paths", {}))
        except ValueError:
            paths = []
    print(f"   {len(paths)} paths in the published API")
    hits = [p for p in paths if re.search(r"file|image|media|download|content|picture|photo|udo|attach", p, re.I)]
    print("   file/image/media-like paths:", hits or "NONE")
    print("   all /devices paths:")
    for p in paths:
        if p.startswith("/devices"):
            print("     ", p)
else:
    print("   no API description could be fetched")

# ---- find fridge + picture ids ---------------------------------------------
r = get(f"{API}/devices")
print("\n== device list:", r.status_code)
if r.status_code == 401:
    sys.exit("Token rejected (revoked or expired?) - create a new one to rerun")
r.raise_for_status()
dev = next((d for d in r.json().get("items", [])
            if any(any(c.get("id") == VIEW_INSIDE for c in comp.get("capabilities") or [])
                   for comp in d.get("components") or [])), None)
if not dev:
    sys.exit("No fridge found")
fridge = dev["deviceId"]
mask(fridge)
for k in ("locationId", "roomId", "ownerId"):
    mask(dev.get(k))
print("presentationId:", dev.get("presentationId"), "| manufacturer:", dev.get("manufacturerName"),
      "| type:", dev.get("type"), "| ocf deviceType:", (dev.get("ocf") or {}).get("ocfDeviceType"))

# ---- 2. capability definitions ----------------------------------------------
print("\n== 2. Capability definitions")
for cap in (VIEW_INSIDE, "samsungce.fileTransfer", "samsungce.sendContents", "execute"):
    rv = get(f"{API}/capabilities/{cap}")
    versions = [i.get("version") for i in (rv.json().get("items") or [])] if rv.ok else []
    print(f"-- {cap}: versions {versions or rv.status_code}")
    for v in versions[-1:] or [1]:
        rd = get(f"{API}/capabilities/{cap}/{v}")
        if rd.ok:
            d = rd.json()
            print("   attributes:", clean(json.dumps(d.get("attributes")), 900))
            print("   commands:  ", clean(json.dumps(d.get("commands")), 900))
        else:
            print("   definition:", rd.status_code, clean(rd.text, 150))

# ---- presentation -----------------------------------------------------------
print("\n== presentation")
pid, mnmn = dev.get("presentationId"), dev.get("manufacturerName")
if pid:
    rp = get(f"{API}/presentation", params={"presentationId": pid, "manufacturerName": mnmn})
    print("   status:", rp.status_code, "bytes:", len(rp.content))
    if rp.ok:
        t = rp.text
        for m in re.finditer(r"viewInside|fileTransfer|file_links|image|udo", t, re.I):
            s = max(0, m.start() - 120)
            print("   ...", clean(t[s:m.end() + 180], 300))
            break
        print("   mentions:", {w: len(re.findall(w, t, re.I)) for w in
                               ("viewInside", "fileTransfer", "file_links", "image", "udo", "url")})

# ---- 3. read the camera OCF resource via the public execute command --------
print("\n== 3. execute: read the camera resource through the public API")
for href in ("/udo/contents/provider/vs/0", "/udo/contents/provider/0", "/file/vs/0"):
    c = requests.post(f"{API}/devices/{fridge}/commands", headers=AUTH, timeout=30,
                      json={"commands": [{"component": "main", "capability": "execute",
                                          "command": "execute", "arguments": [href]}]})
    print(f"-- {href}: command {c.status_code} {clean(c.text, 150)}")
    time.sleep(6)
    s = get(f"{API}/devices/{fridge}/components/main/capabilities/execute/status")
    data = (s.json().get("data") or {}) if s.ok else {}
    print("   execute.data:", clean(json.dumps(data), 1500))

# ---- 4. more guesses + OPTIONS/HEAD -----------------------------------------
st = get(f"{API}/devices/{fridge}/components/main/capabilities/{VIEW_INSIDE}/status").json()
ids = [i["fileId"] for i in ((st.get("contents") or {}).get("value") or []) if i.get("fileId")]
for i in ids:
    mask(i)
fid = ids[0] if ids else "unknown"
print("\n== 4. More guessed paths (Accept json | */*)")
guesses = {
    "devices/{d}/files": f"{API}/devices/{fridge}/files",
    "devices/{d}/media/{f}": f"{API}/devices/{fridge}/media/{fid}",
    "devices/{d}/images/{f}": f"{API}/devices/{fridge}/images/{fid}",
    "images/{f}": f"{API}/images/{fid}",
    "media/{f}": f"{API}/media/{fid}",
    "files/{f}/download": f"{API}/files/{fid}/download",
    "viewInside/files/{f}": f"{API}/devices/{fridge}/components/main/capabilities/{VIEW_INSIDE}/files/{fid}",
    "devices/{d}/status (whole device)": f"{API}/devices/{fridge}/status",
}
for name, url in guesses.items():
    for acc in ("application/json", "*/*"):
        resp = requests.get(url, headers={**AUTH, "Accept": acc}, timeout=30)
        body = "<JPEG IMAGE>" if resp.content[:2] == b"\xff\xd8" else clean(resp.text, 140)
        if "status (whole" in name:
            body = f"{len(resp.content)} bytes, mentions file_links/url: " + str(
                {w: resp.text.count(w) for w in ("file_links", "http", "fileId")})
        print(f"   {resp.status_code} {acc:18} {name:34} {body}")
for method in ("OPTIONS", "HEAD"):
    resp = requests.request(method, f"{API}/devices/{fridge}/files/{fid}", headers=AUTH, timeout=30)
    print(f"   {method} devices/{{d}}/files/{{f}}: {resp.status_code} Allow={resp.headers.get('Allow')} "
          f"ctype={resp.headers.get('content-type')}")
