#!/usr/bin/env python3
"""Acquire a short-lived External ID access token for live validation.

The script uses the SPA public client with OAuth 2.0 authorization code + PKCE.
It creates a temporary localhost callback server for the redirect URI already
registered for local SPA development, then exchanges the code for an access
token. It never stores the token.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import http.server
import json
import secrets
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass
from typing import Any


DEFAULT_AUTHORITY = "https://contosoexternalid.ciamlogin.com/11111111-1111-4111-8111-111111111111"
DEFAULT_CLIENT_ID = "22222222-2222-4222-8222-222222222222"
DEFAULT_REDIRECT_URI = "http://127.0.0.1:5173"
DEFAULT_APIM_URL = "https://contoso-apim.example.com"
DEFAULT_TENANT = "AlphaCapital"
DEFAULT_CROSS_TENANT = "BetaWealth"
DEFAULT_SCOPES = [
    "https://contosoexternalid.onmicrosoft.com/contoso-asset-management/frontend-api/assets.read",
    "https://contosoexternalid.onmicrosoft.com/contoso-asset-management/frontend-api/assets.write",
]


@dataclass
class CallbackResult:
    code: str | None = None
    error: str | None = None
    error_description: str | None = None
    state: str | None = None


def base64_url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def decode_jwt_without_validation(token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) < 2:
        raise ValueError("Token is not a JWT.")
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


def make_handler(result: CallbackResult, expected_state: str):
    class CallbackHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            parsed = urllib.parse.urlparse(self.path)
            query = urllib.parse.parse_qs(parsed.query)
            result.state = query.get("state", [""])[0]
            result.code = query.get("code", [None])[0]
            result.error = query.get("error", [None])[0]
            result.error_description = query.get("error_description", [None])[0]

            if result.state != expected_state:
                result.error = "state_mismatch"
                result.error_description = "The redirect state did not match the request state."

            self.send_response(200 if result.code else 400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                b"<html><body><h1>Authentication complete</h1>"
                b"<p>You can close this browser tab and return to the terminal.</p></body></html>"
            )

        def log_message(self, format: str, *args: Any) -> None:
            return

    return CallbackHandler


def acquire_code(args: argparse.Namespace, verifier: str, challenge: str, state: str) -> str:
    redirect = urllib.parse.urlparse(args.redirect_uri)
    if redirect.scheme != "http" or redirect.hostname not in {"127.0.0.1", "localhost"} or redirect.port is None:
        raise ValueError("--redirect-uri must be a registered localhost HTTP redirect URI with an explicit port.")

    result = CallbackResult()
    server = http.server.ThreadingHTTPServer((redirect.hostname, redirect.port), make_handler(result, state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    params = {
        "client_id": args.client_id,
        "response_type": "code",
        "redirect_uri": args.redirect_uri,
        "response_mode": "query",
        "scope": " ".join(["openid", "profile", "offline_access", *args.scope]),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    auth_url = f"{args.authority.rstrip('/')}/oauth2/v2.0/authorize?{urllib.parse.urlencode(params)}"

    print("Opening browser for External ID sign-in...", file=sys.stderr)
    print(auth_url, file=sys.stderr)
    if not args.no_browser:
        webbrowser.open(auth_url)

    deadline = time.time() + args.timeout
    while time.time() < deadline and not (result.code or result.error):
        time.sleep(0.25)
    server.shutdown()

    if result.error:
        raise RuntimeError(f"Authorization failed: {result.error} {result.error_description or ''}".strip())
    if not result.code:
        raise TimeoutError("Timed out waiting for the local redirect. Re-run with a free localhost redirect port.")
    return result.code


def exchange_code(args: argparse.Namespace, code: str, verifier: str) -> str:
    token_endpoint = f"{args.authority.rstrip('/')}/oauth2/v2.0/token"
    form = urllib.parse.urlencode(
        {
            "client_id": args.client_id,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": args.redirect_uri,
            "code_verifier": verifier,
            "scope": " ".join(args.scope),
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        token_endpoint,
        data=form,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Token exchange failed: HTTP {exc.code}: {detail}") from exc

    token = payload.get("access_token")
    if not token:
        raise RuntimeError(f"Token response did not contain access_token: {payload}")
    return token


def curl_status(url: str, token: str) -> int:
    completed = subprocess.run(
        [
            "curl",
            "-sS",
            "-o",
            "/dev/null",
            "-w",
            "%{http_code}",
            "--max-time",
            "30",
            "-H",
            f"Authorization: Bearer {token}",
            url,
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "curl failed")
    return int(completed.stdout.strip())


def validate_bff(args: argparse.Namespace, token: str) -> None:
    base = args.apim_url.rstrip("/")
    same = f"{base}/api/tenants/{urllib.parse.quote(args.tenant)}/portfolios"
    cross = f"{base}/api/tenants/{urllib.parse.quote(args.cross_tenant)}/portfolios"
    same_status = curl_status(same, token)
    cross_status = curl_status(cross, token)
    print(f"BFF same-tenant status: {same_status}")
    print(f"BFF cross-tenant status: {cross_status}")
    if same_status != 200:
        raise RuntimeError(f"Expected same-tenant BFF call to return 200, got {same_status}.")
    if cross_status != 403:
        raise RuntimeError(f"Expected cross-tenant BFF call to return 403, got {cross_status}.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--authority", default=DEFAULT_AUTHORITY)
    parser.add_argument("--client-id", default=DEFAULT_CLIENT_ID)
    parser.add_argument("--redirect-uri", default=DEFAULT_REDIRECT_URI)
    parser.add_argument("--scope", action="append", default=list(DEFAULT_SCOPES))
    parser.add_argument("--tenant", default=DEFAULT_TENANT)
    parser.add_argument("--cross-tenant", default=DEFAULT_CROSS_TENANT)
    parser.add_argument("--apim-url", default=DEFAULT_APIM_URL)
    parser.add_argument("--validate-bff", action="store_true", help="Call APIM/BFF same-tenant and cross-tenant routes after acquiring the token.")
    parser.add_argument("--print-token", action="store_true", help="Print the access token to stdout. Avoid shell history and logs when using this option.")
    parser.add_argument("--no-browser", action="store_true", help="Print the sign-in URL but do not open a browser.")
    parser.add_argument("--timeout", type=int, default=180)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        verifier = base64_url(secrets.token_bytes(32))
        challenge = base64_url(hashlib.sha256(verifier.encode("ascii")).digest())
        state = secrets.token_urlsafe(24)
        code = acquire_code(args, verifier, challenge, state)
        token = exchange_code(args, code, verifier)
        claims = decode_jwt_without_validation(token)
        safe_claims = {
            "aud": claims.get("aud"),
            "iss": claims.get("iss"),
            "extension_tenantId": claims.get("extension_tenantId"),
            "tenant_roles": claims.get("tenant_roles"),
            "tenant_status": claims.get("tenant_status"),
            "scp": claims.get("scp"),
            "exp": claims.get("exp"),
        }
        print(json.dumps({"claims": safe_claims}, indent=2), file=sys.stderr)
        if args.validate_bff:
            validate_bff(args, token)
        if args.print_token:
            print(token)
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
