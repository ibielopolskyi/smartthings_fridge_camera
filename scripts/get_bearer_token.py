#!/usr/bin/env python3
"""Print a Samsung IoT bearer token for Family Hub image mode.

This uses the existing headless Samsung Account login helper. It does not
inspect phone traffic, browser cookies, or certificates. It will not work for
accounts that require 2FA/CAPTCHA during Samsung Account login.
"""

from __future__ import annotations

import argparse
from getpass import getpass
import importlib.util
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUTH_PATH = os.path.join(
    REPO_ROOT, "custom_components", "samsung_familyhub_fridge", "auth.py"
)
CID = "5Hic3rk1FP"

spec = importlib.util.spec_from_file_location("samsung_familyhub_auth", AUTH_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError(f"Could not load auth helper from {AUTH_PATH}")
auth_module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = auth_module
spec.loader.exec_module(auth_module)

AuthError = auth_module.AuthError
SamsungAccountAuth = auth_module.SamsungAccountAuth
get_samsung_iot_token = auth_module.get_samsung_iot_token
SAMSUNG_LOGIN_CLIENT_ID = auth_module.SAMSUNG_LOGIN_CLIENT_ID


def main() -> int:
    """Run the token helper."""
    parser = argparse.ArgumentParser(
        description="Get a Samsung client bearer token for Family Hub images."
    )
    parser.add_argument("--email", help="Samsung Account email address")
    args = parser.parse_args()

    email = args.email or input("Samsung Account email: ").strip()
    password = getpass("Samsung Account password: ")

    try:
        print("Logging in to Samsung Account...")
        auth = SamsungAccountAuth(
            email=email,
            password=password,
            signin_client_id=SAMSUNG_LOGIN_CLIENT_ID,
            signin_client_secret="",
        )
        samsung_creds = auth.login()

        print("Requesting IoT-scoped bearer token...")
        iot_creds = get_samsung_iot_token(
            userauth_token=samsung_creds.userauth_token,
            login_id=email,
        )
    except AuthError as err:
        print()
        print(f"ERROR: {err}", file=sys.stderr)
        print(
            "If your Samsung account uses 2FA/CAPTCHA, this headless helper "
            "cannot complete the login.",
            file=sys.stderr,
        )
        return 1

    print()
    print("CID:")
    print(CID)
    print()
    print("Bearer token:")
    print(iot_creds.access_token)
    print()
    print("Paste the CID and bearer token into Samsung client bearer token mode.")
    print("Treat the bearer token like a password and do not share it publicly.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
