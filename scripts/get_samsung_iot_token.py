#!/usr/bin/env python3
"""Bootstrap a Samsung IoT refresh token for the fridge camera integration.

This script logs in to your Samsung Account and obtains an IoT-scoped
token that works with client.smartthings.com (for image downloads).
The refresh token is printed so you can paste it into the HA config entry.

Usage:
    python scripts/get_samsung_iot_token.py --email YOUR_EMAIL --password YOUR_PASSWORD

The script uses the known SmartThings app client IDs (from the decompiled APK).
Does NOT support 2FA-enabled Samsung accounts.
"""

import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from custom_components.samsung_familyhub_fridge.auth import (
    SamsungAccountAuth,
    get_samsung_iot_token,
    SAMSUNG_LOGIN_CLIENT_ID,
)


def main():
    parser = argparse.ArgumentParser(description="Get Samsung IoT refresh token")
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    args = parser.parse_args()

    print("Step 1: Logging in to Samsung Account...")
    auth = SamsungAccountAuth(
        email=args.email,
        password=args.password,
        signin_client_id=SAMSUNG_LOGIN_CLIENT_ID,
        signin_client_secret="",  # Not needed for requestAuthentication
    )
    creds = auth.login()
    print(f"  Got userauth_token: {creds.userauth_token[:20]}...")

    print("Step 2: Exchanging for IoT-scoped token...")
    iot = get_samsung_iot_token(
        userauth_token=creds.userauth_token,
        login_id=args.email,
    )
    print(f"  Access token:  {iot.access_token[:20]}...")
    print(f"  Refresh token: {iot.refresh_token[:20]}...")

    print()
    print("=" * 60)
    print("SUCCESS! Add this to your HA fridge integration config:")
    print(f"  samsung_iot_refresh_token: {iot.refresh_token}")
    print("=" * 60)


if __name__ == "__main__":
    main()
