#!/usr/bin/env python3
"""Bootstrap helper: obtain SmartThings OAuth tokens interactively.

One-time setup to get a refresh_token that integration tests (and the
Home Assistant integration) can use to auto-renew access tokens forever.

Prerequisites
─────────────
1. Install the SmartThings CLI:  https://github.com/SmartThingsCommunity/smartthings-cli
2. Create an OAuth app:
       $ smartthings apps:create
     Choose "OAuth-In App".  Set scopes:  r:devices:* w:devices:* x:devices:*
     Set redirect URI:  https://httpbin.org/get
     Save the client_id and client_secret.

Usage
─────
    python scripts/get_token.py --client-id YOUR_ID --client-secret YOUR_SECRET

The script will:
  1. Print a URL — open it in a browser and log in with your Samsung account.
  2. After login, you'll be redirected to httpbin.org showing the "code" param.
  3. Paste the full redirect URL (or just the code) back into the prompt.
  4. The script exchanges it for tokens and saves them to .smartthings_credentials.json.

After this, integration tests can auto-refresh using the saved refresh_token:
    pytest tests/test_integration.py -m integration --credentials .smartthings_credentials.json
"""

from __future__ import annotations

import argparse
import json
import sys
import os
import webbrowser

# Allow importing the component from the repo root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from custom_components.samsung_familyhub_fridge.auth import SmartThingsOAuth


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Obtain SmartThings OAuth tokens interactively."
    )
    parser.add_argument("--client-id", required=True, help="OAuth client ID")
    parser.add_argument("--client-secret", required=True, help="OAuth client secret")
    parser.add_argument(
        "--redirect-uri",
        default="https://httpbin.org/get",
        help="Redirect URI registered with the OAuth app (default: httpbin.org)",
    )
    parser.add_argument(
        "--scopes",
        default="r:devices:* w:devices:* x:devices:*",
        help="Space-separated scopes",
    )
    parser.add_argument(
        "--output",
        default=".smartthings_credentials.json",
        help="File to save credentials to (default: .smartthings_credentials.json)",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Don't auto-open the browser",
    )
    args = parser.parse_args()

    oauth = SmartThingsOAuth(
        client_id=args.client_id,
        client_secret=args.client_secret,
        redirect_uri=args.redirect_uri,
        scopes=args.scopes,
    )

    # Step 1: Generate and display the authorization URL
    auth_url = oauth.get_authorization_url()
    print()
    print("=" * 70)
    print("  STEP 1: Open this URL in your browser and log in")
    print("=" * 70)
    print()
    print(auth_url)
    print()

    if not args.no_browser:
        try:
            webbrowser.open(auth_url)
            print("(Browser opened automatically)")
        except Exception:
            print("(Could not open browser — please copy the URL above)")

    # Step 2: Get the redirect URL or code from the user
    print()
    print("=" * 70)
    print("  STEP 2: After login, paste the redirect URL (or just the code)")
    print("=" * 70)
    print()
    user_input = input("Paste here: ").strip()

    if not user_input:
        print("ERROR: No input provided.", file=sys.stderr)
        sys.exit(1)

    # Accept either a full URL or just the code
    if user_input.startswith("http"):
        code = oauth.extract_code_from_redirect(user_input)
    else:
        code = user_input

    # Step 3: Exchange for tokens
    print()
    print("Exchanging authorization code for tokens...")
    try:
        creds = oauth.exchange_code(code)
    except Exception as e:
        print(f"ERROR: Token exchange failed: {e}", file=sys.stderr)
        sys.exit(1)

    # Step 4: Save credentials
    output_data = {
        "client_id": args.client_id,
        "client_secret": args.client_secret,
        "access_token": creds.access_token,
        "refresh_token": creds.refresh_token,
        "token_type": creds.token_type,
        "expires_in": creds.expires_in,
        "scope": creds.scope,
        "installed_app_id": creds.installed_app_id,
    }

    output_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        args.output,
    )
    with open(output_path, "w") as f:
        json.dump(output_data, f, indent=2)

    print()
    print("=" * 70)
    print("  SUCCESS")
    print("=" * 70)
    print()
    print(f"  Access token:  {creds.access_token[:20]}...")
    print(f"  Refresh token: {creds.refresh_token[:20]}...")
    print(f"  Expires in:    {creds.expires_in}s")
    print(f"  Scope:         {creds.scope}")
    print(f"  Saved to:      {output_path}")
    print()
    print("Run integration tests with:")
    print(f"  pytest tests/test_integration.py -m integration --credentials {args.output}")
    print()


if __name__ == "__main__":
    main()
