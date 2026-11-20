#!/usr/bin/env python3
"""Live check of the Samsung web sign-in start (no account details needed).

Fetches Samsung's public sign-in entry point, builds the encrypted sign-in
link exactly as the integration does, opens it in a headless browser, and
prints what Samsung's page shows (a sign-in form, or an error). Used by the
"Samsung sign-in check" GitHub workflow, since that needs open internet.
"""
from __future__ import annotations

import importlib
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG_DIR = os.path.join(ROOT, "custom_components", "samsung_familyhub_fridge")

# Load auth.py + samsung_browser_login.py without the HA-dependent __init__.
pkg = types.ModuleType("fridge")
pkg.__path__ = [PKG_DIR]
sys.modules["fridge"] = pkg
sbl = importlib.import_module("fridge.samsung_browser_login")

import requests  # noqa: E402

print("== 1. Entry point")
r = requests.get(sbl.ENTRY_POINT_URL, headers=sbl.osp_headers(), timeout=30)
print("HTTP", r.status_code, r.headers.get("content-type"))
body = r.json()
print({k: (v[:80] + "..." if isinstance(v, str) and len(v) > 80 else v)
       for k, v in body.items()})

print("\n== 2. Sign-in link")
login = sbl.SamsungBrowserLogin(country="us", language="en")
url = login.start()
print(url.split("svcParam=")[0] + "svcParam=<%d chars>&mode=C" % len(url))

print("\n== 3. Plain HTTP fetch of the link")
p = requests.get(url, timeout=30, headers={
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"})
print("HTTP", p.status_code, "final URL:", p.url.split("?")[0])
for hop in p.history:
    print("  redirect", hop.status_code, hop.headers.get("Location", "")[:150])

print("\n== 4. Rendered in a real browser")
from playwright.sync_api import sync_playwright  # noqa: E402

with sync_playwright() as pw:
    browser = pw.chromium.launch()
    page = browser.new_page(locale="en-US")
    console = []
    page.on("console", lambda m: console.append(f"{m.type}: {m.text[:200]}"))
    page.goto(url, wait_until="networkidle", timeout=60000)
    page.wait_for_timeout(3000)
    print("final URL:", page.url.split("?")[0])
    print("title:", page.title())
    text = page.inner_text("body")
    print("--- page text (first 1500 chars) ---")
    print("\n".join(line for line in text.splitlines() if line.strip())[:1500])
    print("--- input fields ---")
    for el in page.query_selector_all("input"):
        print(" ", el.get_attribute("type"), el.get_attribute("name"),
              el.get_attribute("id"), el.get_attribute("placeholder"))
    print("--- console ---")
    print("\n".join(console[:20]) or "(none)")
    page.screenshot(path="samsung_signin.png", full_page=True)
    browser.close()
