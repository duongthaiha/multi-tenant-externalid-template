#!/usr/bin/env python3
"""Idempotently create Contoso demo local users in an Azure External ID tenant.

The script creates only fictional local accounts from the seed fixture. It prints a
safe user/object-id mapping for control-plane seeding and never writes generated
passwords unless --password-output is explicitly provided.
"""

from __future__ import annotations

import argparse
import json
import secrets
import string
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_FIXTURE = SCRIPT_DIR / "seed-data.fixtures.json"
DEFAULT_OUTPUT = Path("docs/external-id-demo-users.local.json")
DEFAULT_TENANT_ID = "11111111-1111-4111-8111-111111111111"
DEFAULT_TENANT_DOMAIN = "contosoexternalid.onmicrosoft.com"
INITIAL_TENANTS = {"AlphaCapital", "BetaWealth", "GammaFund"}


class GraphError(RuntimeError):
    """Raised when Microsoft Graph returns an error."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", default=DEFAULT_TENANT_ID)
    parser.add_argument("--tenant-domain", default=DEFAULT_TENANT_DOMAIN)
    parser.add_argument("--fixture", default=str(DEFAULT_FIXTURE))
    parser.add_argument("--tenant", action="append", help="Tenant to include. Repeat for multiple tenants. Defaults to Alpha/Beta/Gamma.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Ignored local JSON mapping output. Contains no passwords.")
    parser.add_argument("--password-output", help="Ignored local JSON password output. Use only for one-time demo setup; never commit it.")
    parser.add_argument("--force-change-password-next-sign-in", action="store_true", help="Require each demo user to change the generated password on first sign-in.")
    parser.add_argument("--dry-run", action="store_true", help="Validate fixture and Graph token, then print intended user operations without writes.")
    parser.add_argument("--check-user-flow-support", action="store_true", help="Probe Graph user-flow endpoints and include support notes in output.")
    return parser.parse_args()


def run_az(args: list[str]) -> str:
    completed = subprocess.run(["az", *args], check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip() or "Azure CLI command failed"
        raise RuntimeError(f"az {' '.join(args)} failed: {message}")
    return completed.stdout.strip()


def get_graph_token(tenant_id: str) -> str:
    return run_az([
        "account", "get-access-token", "--tenant", tenant_id, "--resource-type", "ms-graph",
        "--query", "accessToken", "--output", "tsv",
    ])


def graph_request(token: str, method: str, path: str, body: dict[str, Any] | None = None, *, api: str = "v1.0") -> dict[str, Any]:
    data = None if body is None else json.dumps(body, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        f"https://graph.microsoft.com/{api}{path}",
        data=data,
        method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            content = response.read()
            return {} if not content else json.loads(content.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise GraphError(f"Graph {api} {method} {path} failed: HTTP {exc.code}: {detail}") from exc


def filter_literal(value: str) -> str:
    return value.replace("'", "''")


def load_demo_users(path: str, selected_tenants: set[str]) -> list[dict[str, str]]:
    fixture = json.loads(Path(path).read_text(encoding="utf-8"))
    users: list[dict[str, str]] = []
    for tenant in fixture.get("tenants", []):
        tenant_id = tenant.get("tenantId")
        if tenant_id not in selected_tenants:
            continue
        for user in tenant.get("users", []):
            user_id = require_text(user, "userId")
            email = require_text(user, "email").lower()
            if not email.endswith("@example.com"):
                raise RuntimeError(f"Demo user {user_id} must use reserved example.com email, got {email}")
            users.append({
                "tenantId": tenant_id,
                "fixtureUserId": user_id,
                "displayName": require_text(user, "displayName"),
                "email": email,
                "roles": list(user.get("roles") or []),
            })
    missing = sorted(selected_tenants - {user["tenantId"] for user in users})
    if missing:
        raise RuntimeError(f"Fixture does not contain users for tenants: {', '.join(missing)}")
    if len(users) != len({user["email"] for user in users}):
        raise RuntimeError("Demo user emails must be unique.")
    return users


def require_text(document: dict[str, Any], field: str) -> str:
    value = document.get(field)
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"Missing required text field {field} in fixture user.")
    return value


def find_user_by_email(token: str, tenant_domain: str, email: str) -> dict[str, Any] | None:
    identity_filter = (
        "identities/any(c:c/issuerAssignedId eq "
        f"'{filter_literal(email)}' and c/issuer eq '{filter_literal(tenant_domain)}')"
    )
    query = urllib.parse.urlencode({"$filter": identity_filter, "$select": "id,displayName,userPrincipalName,identities,accountEnabled"})
    try:
        matches = graph_request(token, "GET", f"/users?{query}").get("value", [])
    except GraphError:
        query = urllib.parse.urlencode({"$filter": f"otherMails/any(c:c eq '{filter_literal(email)}')", "$select": "id,displayName,userPrincipalName,identities,accountEnabled"})
        matches = graph_request(token, "GET", f"/users?{query}").get("value", [])
    if len(matches) > 1:
        ids = ", ".join(match["id"] for match in matches)
        raise RuntimeError(f"Multiple External ID users already match {email}: {ids}")
    return matches[0] if matches else None


def generate_password() -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*()-_=+"
    while True:
        password = "".join(secrets.choice(alphabet) for _ in range(24))
        if all(any(ch in group for ch in password) for group in [string.ascii_lowercase, string.ascii_uppercase, string.digits, "!@#$%^&*()-_=+"]):
            return password


def create_user(token: str, tenant_domain: str, user: dict[str, str], password: str, force_change: bool) -> dict[str, Any]:
    body = {
        "accountEnabled": True,
        "displayName": user["displayName"],
        "identities": [{"signInType": "emailAddress", "issuer": tenant_domain, "issuerAssignedId": user["email"]}],
        "otherMails": [user["email"]],
        "passwordProfile": {"password": password, "forceChangePasswordNextSignIn": force_change},
        "passwordPolicies": "DisablePasswordExpiration",
    }
    created = graph_request(token, "POST", "/users", body)
    return graph_request(token, "GET", f"/users/{created['id']}?$select=id,displayName,userPrincipalName,identities,accountEnabled")


def check_user_flow_support(token: str) -> list[dict[str, str]]:
    checks: list[dict[str, str]] = []
    for api, path in [("v1.0", "/identity/b2cUserFlows"), ("beta", "/identity/b2cUserFlows"), ("beta", "/identity/authenticationEventsFlows")]:
        try:
            graph_request(token, "GET", path, api=api)
            checks.append({"api": api, "path": path, "status": "available"})
        except GraphError as exc:
            checks.append({"api": api, "path": path, "status": "blocked", "detail": str(exc).splitlines()[0]})
    return checks


def safe_summary(args: argparse.Namespace, users: list[dict[str, str]], results: list[dict[str, Any]], flow_checks: list[dict[str, str]]) -> dict[str, Any]:
    by_email = {result["email"]: result for result in results}
    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "tenant": {"tenantId": args.tenant_id, "domain": args.tenant_domain},
        "users": [
            {
                "tenantId": user["tenantId"],
                "fixtureUserId": user["fixtureUserId"],
                "displayName": user["displayName"],
                "email": user["email"],
                "roles": user["roles"],
                "externalIdObjectId": by_email[user["email"]]["id"],
                "userPrincipalName": by_email[user["email"]].get("userPrincipalName"),
                "created": by_email[user["email"]]["created"],
            }
            for user in users
        ],
        "seedData": {"command": f"scripts/seed-data.sh --user-id-map {args.output}"},
        "userFlowSupport": flow_checks,
        "notes": [
            "This file intentionally contains no passwords or secrets.",
            "Use externalIdObjectId values as control-plane userId values so OnTokenIssuanceStart resolves by the External ID user object id.",
        ],
    }


def write_json(path: str, value: Any) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2)
        handle.write("\n")


def main() -> int:
    args = parse_args()
    selected = set(args.tenant or INITIAL_TENANTS)
    users = load_demo_users(args.fixture, selected)
    account = json.loads(run_az(["account", "show", "--output", "json"]))
    print(f"Current Azure CLI account: {account.get('user', {}).get('name')} in tenant {account.get('tenantId')}", file=sys.stderr)
    token = get_graph_token(args.tenant_id)
    flow_checks = check_user_flow_support(token) if args.check_user_flow_support else []

    results: list[dict[str, Any]] = []
    passwords: list[dict[str, str]] = []
    for user in users:
        existing = find_user_by_email(token, args.tenant_domain, user["email"])
        if existing:
            print(f"EXISTS {user['email']} -> {existing['id']}", file=sys.stderr)
            results.append({"email": user["email"], "created": False, **existing})
            continue
        if args.dry_run:
            synthetic_id = f"<external-id-object-id-for-{user['fixtureUserId']}>"
            print(f"DRY-RUN create External ID local user {user['email']} ({user['displayName']})", file=sys.stderr)
            results.append({"email": user["email"], "created": False, "id": synthetic_id, "displayName": user["displayName"], "userPrincipalName": None})
            continue
        password = generate_password()
        created = create_user(token, args.tenant_domain, user, password, args.force_change_password_next_sign_in)
        print(f"CREATED {user['email']} -> {created['id']}", file=sys.stderr)
        results.append({"email": user["email"], "created": True, **created})
        passwords.append({"email": user["email"], "temporaryPassword": password})

    summary = safe_summary(args, users, results, flow_checks)
    write_json(args.output, summary)
    print(json.dumps(summary, indent=2))
    print(f"Wrote safe mapping to {args.output}", file=sys.stderr)
    if passwords:
        if not args.password_output:
            print("WARNING: Generated passwords were not written. Re-run with --password-output if you need one-time passwords for newly created users.", file=sys.stderr)
        else:
            write_json(args.password_output, {"generatedAt": datetime.now(timezone.utc).isoformat(), "users": passwords})
            print(f"Wrote one-time passwords to {args.password_output}; keep it ignored and delete after use.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
