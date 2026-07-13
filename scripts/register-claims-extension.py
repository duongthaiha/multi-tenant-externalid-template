#!/usr/bin/env python3
"""Register the External ID token issuance custom claims provider.

The script is intentionally idempotent. It creates or updates:

- an onTokenIssuanceStart custom authentication extension;
- an onTokenIssuanceStart authentication event listener for the frontend API
  resource application, so API access tokens are enriched;
- a claims mapping policy that emits extension_tenantId, tenant_roles, and
  tenant_status from the custom claims provider into the token.

No secrets or tokens are written to disk.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


DEFAULT_TENANT_ID = "11111111-1111-4111-8111-111111111111"
DEFAULT_TENANT_NAME = "contosoexternalid"
DEFAULT_APP_REG_OUTPUT = "docs/external-id-app-registrations.local.json"
DEFAULT_EXTENSION_DISPLAY_NAME = "Contoso Asset Management POC Token Claims Provider"
DEFAULT_LISTENER_DISPLAY_NAME = "Contoso Asset Management POC Token Claims Listener - Frontend API"
DEFAULT_POLICY_DISPLAY_NAME = "Contoso Asset Management POC Custom Claims Mapping - Frontend API"
CLAIM_IDS = ("extension_tenantId", "tenant_roles", "tenant_status")
GRAPH_APP_ID = "00000003-0000-0000-c000-000000000000"
CUSTOM_AUTH_EXTENSION_PERMISSION_ID = "214e810f-fda8-4fd7-a475-29461495eb00"


class GraphError(RuntimeError):
    """Raised when Microsoft Graph returns an error."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", default=os.getenv("EXTERNAL_ID_TENANT_ID", DEFAULT_TENANT_ID))
    parser.add_argument("--tenant-name", default=os.getenv("EXTERNAL_ID_TENANT_NAME", DEFAULT_TENANT_NAME))
    parser.add_argument("--callback-url", default=os.getenv("CLAIMS_PROVIDER_CALLBACK_URL"), help="Deployed Function callback URL, for example https://<app>.azurewebsites.net/api/OnTokenIssuanceStart.")
    parser.add_argument("--claims-provider-audience", default=os.getenv("CLAIMS_PROVIDER_AUDIENCE"), help="App ID URI used as the azureAdTokenAuthentication resourceId.")
    parser.add_argument("--frontend-api-client-id", default=os.getenv("FRONTEND_API_CLIENT_ID"), help="Frontend API application/client ID whose access tokens are enriched.")
    parser.add_argument("--app-registration-output", default=DEFAULT_APP_REG_OUTPUT)
    parser.add_argument("--extension-display-name", default=DEFAULT_EXTENSION_DISPLAY_NAME)
    parser.add_argument("--listener-display-name", default=DEFAULT_LISTENER_DISPLAY_NAME)
    parser.add_argument("--policy-display-name", default=DEFAULT_POLICY_DISPLAY_NAME)
    parser.add_argument("--validation-access-token", default=os.getenv("VALIDATION_ACCESS_TOKEN"), help="Optional short-lived API access token to decode and verify required claims. Never store this value.")
    parser.add_argument("--dry-run", action="store_true", help="Read current Graph state and print planned writes without changing the tenant.")
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


def graph_request(token: str, method: str, path: str, body: dict[str, Any] | None = None, *, api: str = "beta", ok: set[int] | None = None) -> dict[str, Any]:
    data = None if body is None else json.dumps(body, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        f"https://graph.microsoft.com/{api}{path}",
        data=data,
        method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Accept": "application/json"},
    )
    expected = ok or ({200, 201, 204} if method != "DELETE" else {204})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            content = response.read()
            if response.status not in expected:
                raise GraphError(f"Graph {api} {method} {path} returned unexpected HTTP {response.status}")
            return {} if not content else json.loads(content.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise GraphError(f"Graph {api} {method} {path} failed: HTTP {exc.code}: {detail}") from exc


def filter_literal(value: str) -> str:
    return value.replace("'", "''")


def load_app_defaults(path: str) -> dict[str, str]:
    defaults: dict[str, str] = {}
    if not os.path.exists(path):
        return defaults
    data = json.load(open(path, encoding="utf-8"))
    apps = data.get("appRegistrations") or {}
    config = (data.get("configuration") or {}).get("azdEnv") or {}
    if apps.get("frontendApi", {}).get("clientId"):
        defaults["frontend_api_client_id"] = apps["frontendApi"]["clientId"]
    if apps.get("customClaimsProvider", {}).get("audience"):
        defaults["claims_provider_audience"] = apps["customClaimsProvider"]["audience"]
    defaults.update({key.lower(): value for key, value in config.items() if isinstance(value, str)})
    return defaults


def require_args(args: argparse.Namespace) -> None:
    defaults = load_app_defaults(args.app_registration_output)
    args.frontend_api_client_id = args.frontend_api_client_id or defaults.get("frontend_api_client_id") or defaults.get("frontend_api_client_id".upper().lower())
    args.claims_provider_audience = args.claims_provider_audience or defaults.get("claims_provider_audience") or defaults.get("claims_provider_audience".upper().lower())
    missing = []
    if not args.callback_url:
        missing.append("--callback-url or CLAIMS_PROVIDER_CALLBACK_URL")
    if not args.claims_provider_audience:
        missing.append("--claims-provider-audience or CLAIMS_PROVIDER_AUDIENCE")
    if not args.frontend_api_client_id:
        missing.append("--frontend-api-client-id or FRONTEND_API_CLIENT_ID")
    if missing and not args.dry_run:
        raise RuntimeError("Missing required input(s): " + ", ".join(missing))
    if args.callback_url and not args.callback_url.startswith("https://"):
        raise RuntimeError("The callback URL must use https:// for deployed External ID callbacks.")


def redact_url(url: str | None) -> str:
    if not url:
        return "<missing>"
    parsed = urllib.parse.urlsplit(url)
    if parsed.query:
        return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "<redacted-query>", parsed.fragment))
    return url


def list_collection(token: str, path: str, *, api: str = "beta") -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    next_path = path
    while next_path:
        result = graph_request(token, "GET", next_path, api=api)
        items.extend(result.get("value") or [])
        next_link = result.get("@odata.nextLink")
        next_path = urllib.parse.urlsplit(next_link).path.replace(f"/{api}", "", 1) + ("?" + urllib.parse.urlsplit(next_link).query if next_link and urllib.parse.urlsplit(next_link).query else "") if next_link else ""
    return items


def find_by_display_name(items: list[dict[str, Any]], display_name: str) -> dict[str, Any] | None:
    matches = [item for item in items if item.get("displayName") == display_name]
    if len(matches) > 1:
        ids = ", ".join(item.get("id", "<unknown>") for item in matches)
        raise RuntimeError(f"Multiple Graph objects named {display_name!r} exist: {ids}")
    return matches[0] if matches else None


def extension_payload(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "@odata.type": "#microsoft.graph.onTokenIssuanceStartCustomExtension",
        "displayName": args.extension_display_name,
        "description": "Adds Contoso business-tenant, role, and tenant status claims at token issuance.",
        "endpointConfiguration": {
            "@odata.type": "#microsoft.graph.httpRequestEndpoint",
            "targetUrl": args.callback_url or "https://<function-app>.azurewebsites.net/api/OnTokenIssuanceStart",
        },
        "authenticationConfiguration": {
            "@odata.type": "#microsoft.graph.azureAdTokenAuthentication",
            "resourceId": args.claims_provider_audience or "api://<claims-provider-app-id>",
        },
        "clientConfiguration": {"timeoutInMilliseconds": 3000, "maximumRetries": 1},
        "claimsForTokenConfiguration": [{"claimIdInApiResponse": claim} for claim in CLAIM_IDS],
    }


def listener_payload(args: argparse.Namespace, extension_id: str) -> dict[str, Any]:
    return {
        "@odata.type": "#microsoft.graph.onTokenIssuanceStartListener",
        "displayName": args.listener_display_name,
        "conditions": {
            "applications": {
                "includeAllApplications": False,
                "includeApplications": [{"appId": args.frontend_api_client_id or "<frontend-api-client-id>"}],
            }
        },
        "priority": 500,
        "handler": {
            "@odata.type": "#microsoft.graph.onTokenIssuanceStartCustomExtensionHandler",
            "customExtension": {"id": extension_id},
        },
    }


def claims_mapping_definition() -> str:
    policy = {
        "ClaimsMappingPolicy": {
            "Version": 1,
            "IncludeBasicClaimSet": "true",
            "ClaimsSchema": [
                {"Source": "CustomClaimsProvider", "ID": "extension_tenantId", "JwtClaimType": "extension_tenantId"},
                {"Source": "CustomClaimsProvider", "ID": "tenant_roles", "JwtClaimType": "tenant_roles"},
                {"Source": "CustomClaimsProvider", "ID": "tenant_status", "JwtClaimType": "tenant_status"},
            ],
        }
    }
    return json.dumps(policy, separators=(",", ":"))


def print_plan(args: argparse.Namespace) -> None:
    print("Planned token issuance registration:")
    print(f"- tenant: {args.tenant_id} / {args.tenant_name}.onmicrosoft.com")
    print(f"- callbackUrl: {redact_url(args.callback_url)}")
    print(f"- authentication resourceId: {args.claims_provider_audience or '<missing>'}")
    print(f"- frontend API appId: {args.frontend_api_client_id or '<missing>'}")
    print(f"- claimsForTokenConfiguration: {', '.join(CLAIM_IDS)}")
    print("- callback response action: microsoft.graph.tokenIssuanceStart.provideClaimsForToken")


def create_or_update_extension(token: str, args: argparse.Namespace) -> str:
    payload = extension_payload(args)
    existing = find_by_display_name(list_collection(token, "/identity/customAuthenticationExtensions"), args.extension_display_name)
    if args.dry_run:
        print(f"DRY-RUN: would {'update' if existing else 'create'} customAuthenticationExtension {args.extension_display_name!r}.")
        return existing.get("id", "<new-extension-id>") if existing else "<new-extension-id>"
    if existing:
        graph_request(token, "PATCH", f"/identity/customAuthenticationExtensions/{existing['id']}", payload)
        print(f"Updated custom authentication extension {args.extension_display_name!r}.")
        return existing["id"]
    created = graph_request(token, "POST", "/identity/customAuthenticationExtensions", payload, ok={201})
    print(f"Created custom authentication extension {args.extension_display_name!r}.")
    return created["id"]


def create_or_update_listener(token: str, args: argparse.Namespace, extension_id: str) -> str:
    payload = listener_payload(args, extension_id)
    existing = find_by_display_name(list_collection(token, "/identity/authenticationEventListeners"), args.listener_display_name)
    if args.dry_run:
        print(f"DRY-RUN: would {'update' if existing else 'create'} authenticationEventListener {args.listener_display_name!r}.")
        return existing.get("id", "<new-listener-id>") if existing else "<new-listener-id>"
    if existing:
        graph_request(token, "PATCH", f"/identity/authenticationEventListeners/{existing['id']}", payload)
        print(f"Updated authentication event listener {args.listener_display_name!r}.")
        return existing["id"]
    created = graph_request(token, "POST", "/identity/authenticationEventListeners", payload, ok={201})
    print(f"Created authentication event listener {args.listener_display_name!r}.")
    return created["id"]


def find_application(token: str, app_id: str) -> dict[str, Any]:
    query = urllib.parse.urlencode({"$filter": f"appId eq '{filter_literal(app_id)}'", "$select": "id,appId,displayName,api,signInAudience"})
    matches = graph_request(token, "GET", f"/applications?{query}", api="v1.0").get("value") or []
    if len(matches) != 1:
        raise RuntimeError(f"Expected exactly one frontend API application with appId {app_id}, found {len(matches)}")
    return matches[0]


def ensure_service_principal(token: str, app_id: str, *, dry_run: bool) -> dict[str, Any]:
    path = f"/servicePrincipals(appId='{filter_literal(app_id)}')?$select=id,appId,displayName"
    try:
        return graph_request(token, "GET", path, api="v1.0")
    except GraphError:
        if dry_run:
            print(f"DRY-RUN: would create missing service principal for {app_id}.")
            return {"id": "<new-service-principal-id>", "appId": app_id}
        return graph_request(token, "POST", "/servicePrincipals", {"appId": app_id}, api="v1.0", ok={201})


def ensure_claims_mapping_policy(token: str, args: argparse.Namespace) -> str:
    app = find_application(token, args.frontend_api_client_id)
    api = dict(app.get("api") or {})
    api["acceptMappedClaims"] = True
    api["requestedAccessTokenVersion"] = 2
    definition = claims_mapping_definition()
    policies = list_collection(token, "/policies/claimsMappingPolicies", api="v1.0")
    existing = find_by_display_name(policies, args.policy_display_name)
    if args.dry_run:
        print(f"DRY-RUN: would patch frontend API application {app['displayName']!r} with api.acceptMappedClaims=true and requestedAccessTokenVersion=2.")
        print(f"DRY-RUN: would {'update' if existing else 'create'} claimsMappingPolicy {args.policy_display_name!r}.")
        print("DRY-RUN: would assign the claims mapping policy to the frontend API service principal.")
        return existing.get("id", "<new-policy-id>") if existing else "<new-policy-id>"

    graph_request(token, "PATCH", f"/applications/{app['id']}", {"api": api}, api="v1.0")
    if existing:
        policy_id = existing["id"]
        graph_request(token, "PATCH", f"/policies/claimsMappingPolicies/{policy_id}", {"definition": [definition]}, api="v1.0")
        print(f"Updated claims mapping policy {args.policy_display_name!r}.")
    else:
        created = graph_request(
            token,
            "POST",
            "/policies/claimsMappingPolicies",
            {"definition": [definition], "displayName": args.policy_display_name, "isOrganizationDefault": False},
            api="v1.0",
            ok={201},
        )
        policy_id = created["id"]
        print(f"Created claims mapping policy {args.policy_display_name!r}.")

    service_principal = ensure_service_principal(token, args.frontend_api_client_id, dry_run=False)
    assigned = graph_request(token, "GET", f"/servicePrincipals/{service_principal['id']}/claimsMappingPolicies", api="v1.0").get("value") or []
    if not any(policy.get("id") == policy_id for policy in assigned):
        graph_request(
            token,
            "POST",
            f"/servicePrincipals/{service_principal['id']}/claimsMappingPolicies/$ref",
            {"@odata.id": f"https://graph.microsoft.com/v1.0/policies/claimsMappingPolicies/{policy_id}"},
            api="v1.0",
            ok={204},
        )
        print("Assigned claims mapping policy to frontend API service principal.")
    else:
        print("Claims mapping policy is already assigned to frontend API service principal.")
    return policy_id


def decode_jwt_without_validation(token: str) -> dict[str, Any]:
    try:
        payload = token.split(".")[1]
        payload += "=" * ((4 - len(payload) % 4) % 4)
        return json.loads(base64.urlsafe_b64decode(payload.encode("utf-8")).decode("utf-8"))
    except Exception as exc:
        raise RuntimeError("Unable to decode validation token payload.") from exc


def validate_claims(args: argparse.Namespace) -> None:
    if not args.validation_access_token:
        print("Token validation skipped: set VALIDATION_ACCESS_TOKEN or pass --validation-access-token after live sign-in.")
        return
    claims = decode_jwt_without_validation(args.validation_access_token)
    missing = [claim for claim in CLAIM_IDS if claim not in claims]
    if missing:
        raise RuntimeError(f"Validation token is missing required claim(s): {', '.join(missing)}")
    print("Validation token contains extension_tenantId, tenant_roles, and tenant_status.")


def main() -> int:
    args = parse_args()
    try:
        require_args(args)
        print_plan(args)
        token = get_graph_token(args.tenant_id)
        if args.dry_run:
            print("Graph token acquired; dry-run will not write tenant configuration.")
        extension_id = create_or_update_extension(token, args)
        create_or_update_listener(token, args, extension_id)
        if args.frontend_api_client_id:
            ensure_claims_mapping_policy(token, args)
        validate_claims(args)
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
