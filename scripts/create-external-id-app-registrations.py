#!/usr/bin/env python3
"""Idempotently create Contoso External ID app registrations without secrets."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from typing import Any


DEFAULT_TENANT_ID = "11111111-1111-4111-8111-111111111111"
DEFAULT_TENANT_NAME = "contosoexternalid"
DEFAULT_ISSUER = f"https://{DEFAULT_TENANT_ID}.ciamlogin.com/{DEFAULT_TENANT_ID}/v2.0"
DEFAULT_AUTHORITY = f"https://{DEFAULT_TENANT_NAME}.ciamlogin.com/{DEFAULT_TENANT_ID}"
DEFAULT_REDIRECT_URIS = ["http://localhost:5173", "http://127.0.0.1:5173"]

DISPLAY_NAMES = {
    "spa": "Contoso Asset Management POC SPA",
    "frontend_api": "Contoso Asset Management POC Frontend API",
    "claims_provider": "Contoso Asset Management POC Custom Claims Provider",
}


class GraphError(RuntimeError):
    """Raised when Microsoft Graph returns an error."""


def run_az(args: list[str]) -> str:
    completed = subprocess.run(
        ["az", *args],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(f"az {' '.join(args)} failed: {message}")
    return completed.stdout.strip()


def get_graph_token(tenant_id: str) -> str:
    return run_az(
        [
            "account",
            "get-access-token",
            "--tenant",
            tenant_id,
            "--resource-type",
            "ms-graph",
            "--query",
            "accessToken",
            "--output",
            "tsv",
        ]
    )


def graph_request(token: str, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        f"https://graph.microsoft.com/v1.0{path}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            content = response.read()
            return {} if not content else json.loads(content.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise GraphError(f"Graph {method} {path} failed: HTTP {exc.code}: {detail}") from exc


def filter_literal(value: str) -> str:
    return value.replace("'", "''")


def find_app(token: str, display_name: str) -> dict[str, Any] | None:
    query = urllib.parse.urlencode(
        {
            "$filter": f"displayName eq '{filter_literal(display_name)}'",
            "$select": "id,appId,displayName,identifierUris,api,spa,web,appRoles,requiredResourceAccess",
        }
    )
    result = graph_request(token, "GET", f"/applications?{query}")
    matches = result.get("value", [])
    if len(matches) > 1:
        ids = ", ".join(app["appId"] for app in matches)
        raise RuntimeError(f"Multiple app registrations named {display_name!r} already exist: {ids}")
    return matches[0] if matches else None


def create_or_get_app(token: str, display_name: str) -> tuple[dict[str, Any], bool]:
    existing = find_app(token, display_name)
    if existing is not None:
        return existing, False

    created = graph_request(
        token,
        "POST",
        "/applications",
        {
            "displayName": display_name,
            "signInAudience": "AzureADMyOrg",
            "passwordCredentials": [],
        },
    )
    refreshed = find_app(token, display_name)
    return (refreshed or created), True


def find_service_principal(token: str, app_id: str) -> dict[str, Any] | None:
    query = urllib.parse.urlencode(
        {
            "$filter": f"appId eq '{filter_literal(app_id)}'",
            "$select": "id,appId,displayName,accountEnabled,servicePrincipalType",
        }
    )
    matches = graph_request(token, "GET", f"/servicePrincipals?{query}").get("value", [])
    if len(matches) > 1:
        raise RuntimeError(f"Multiple service principals found for appId {app_id!r}.")
    return matches[0] if matches else None


def create_or_get_service_principal(
    token: str,
    app: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    existing = find_service_principal(token, app["appId"])
    if existing is not None:
        return existing, False

    graph_request(token, "POST", "/servicePrincipals", {"appId": app["appId"]})
    created = find_service_principal(token, app["appId"])
    if created is None:
        raise RuntimeError(
            f"Unable to find newly created service principal for {app['displayName']!r}."
        )
    return created, True


def stable_guid(*parts: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "|".join(parts)))


def merge_scopes(app: dict[str, Any], tenant_id: str, scopes: list[dict[str, str]]) -> list[dict[str, Any]]:
    current = list((app.get("api") or {}).get("oauth2PermissionScopes") or [])
    by_value = {scope.get("value"): scope for scope in current if scope.get("value")}
    for scope in scopes:
        existing = by_value.get(scope["value"])
        scope_id = existing.get("id") if existing else stable_guid(tenant_id, app["displayName"], scope["value"])
        by_value[scope["value"]] = {
            "id": scope_id,
            "adminConsentDisplayName": scope["admin_display"],
            "adminConsentDescription": scope["admin_description"],
            "userConsentDisplayName": scope["user_display"],
            "userConsentDescription": scope["user_description"],
            "isEnabled": True,
            "type": "User",
            "value": scope["value"],
        }
    return list(by_value.values())


def merge_app_roles(app: dict[str, Any], tenant_id: str, roles: list[dict[str, str]]) -> list[dict[str, Any]]:
    current = list(app.get("appRoles") or [])
    by_value = {role.get("value"): role for role in current if role.get("value")}
    for role in roles:
        existing = by_value.get(role["value"])
        role_id = existing.get("id") if existing else stable_guid(tenant_id, app["displayName"], role["value"])
        by_value[role["value"]] = {
            "allowedMemberTypes": ["Application"],
            "description": role["description"],
            "displayName": role["display_name"],
            "id": role_id,
            "isEnabled": True,
            "value": role["value"],
        }
    return list(by_value.values())


def patch_app(token: str, app: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    graph_request(token, "PATCH", f"/applications/{app['id']}", patch)
    refreshed = find_app(token, app["displayName"])
    if refreshed is None:
        raise RuntimeError(f"Unable to refresh app registration {app['displayName']!r}")
    return refreshed


def configure_api_app(
    token: str,
    app: dict[str, Any],
    tenant_id: str,
    app_id_uri: str | None = None,
    scopes: list[dict[str, str]] | None = None,
    roles: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    app_id_uri = app_id_uri or f"api://{app['appId']}"
    api = dict(app.get("api") or {})
    api["requestedAccessTokenVersion"] = 2
    if scopes:
        api["oauth2PermissionScopes"] = merge_scopes(app, tenant_id, scopes)

    patch: dict[str, Any] = {"identifierUris": [app_id_uri], "api": api}
    if roles:
        patch["appRoles"] = merge_app_roles(app, tenant_id, roles)
    return patch_app(token, app, patch)


def configure_spa(
    token: str,
    app: dict[str, Any],
    redirect_uris: list[str],
    frontend_api: dict[str, Any],
) -> dict[str, Any]:
    frontend_scopes = [
        scope
        for scope in (frontend_api.get("api") or {}).get("oauth2PermissionScopes") or []
        if scope.get("value") in {"assets.read", "assets.write"}
    ]
    required_resource_access = list(app.get("requiredResourceAccess") or [])
    required_resource_access = [
        item for item in required_resource_access if item.get("resourceAppId") != frontend_api["appId"]
    ]
    required_resource_access.append(
        {
            "resourceAppId": frontend_api["appId"],
            "resourceAccess": [{"id": scope["id"], "type": "Scope"} for scope in frontend_scopes],
        }
    )
    return patch_app(
        token,
        app,
        {
            "spa": {"redirectUris": sorted(set(redirect_uris))},
            "requiredResourceAccess": required_resource_access,
        },
    )


def preauthorize_spa(token: str, frontend_api: dict[str, Any], spa: dict[str, Any]) -> dict[str, Any]:
    api = dict(frontend_api.get("api") or {})
    scopes = [
        scope
        for scope in api.get("oauth2PermissionScopes") or []
        if scope.get("value") in {"assets.read", "assets.write"}
    ]
    existing = [
        item
        for item in api.get("preAuthorizedApplications") or []
        if item.get("appId") != spa["appId"]
    ]
    existing.append(
        {
            "appId": spa["appId"],
            "delegatedPermissionIds": [scope["id"] for scope in scopes],
        }
    )
    api["preAuthorizedApplications"] = existing
    return patch_app(token, frontend_api, {"api": api})


def summarize(
    args: argparse.Namespace,
    apps: dict[str, dict[str, Any]],
    created: dict[str, bool],
    service_principals: dict[str, dict[str, Any]],
    service_principals_created: dict[str, bool],
) -> dict[str, Any]:
    frontend_uri = f"https://{args.tenant_name}.onmicrosoft.com/contoso-asset-management/frontend-api"
    claims_provider_uri = f"api://{apps['claims_provider']['appId']}"
    frontend_metadata_address = f"{args.authority}/v2.0/.well-known/openid-configuration?appid={apps['frontend_api']['appId']}"
    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "tenant": {
            "directoryName": f"{args.tenant_name}.onmicrosoft.com",
            "tenantId": args.tenant_id,
            "issuer": args.issuer,
            "msalAuthority": args.authority,
        },
        "appRegistrations": {
            "spa": {
                "displayName": apps["spa"]["displayName"],
                "clientId": apps["spa"]["appId"],
                "created": created["spa"],
                "redirectUris": sorted(set(args.redirect_uri)),
                "deployedRedirectUriPlaceholder": "https://<static-web-app-default-hostname>",
            },
            "frontendApi": {
                "displayName": apps["frontend_api"]["displayName"],
                "clientId": apps["frontend_api"]["appId"],
                "created": created["frontend_api"],
                "appIdUri": frontend_uri,
                "scopes": [f"{frontend_uri}/assets.read", f"{frontend_uri}/assets.write"],
                "audience": frontend_uri,
            },
            "customClaimsProvider": {
                "displayName": apps["claims_provider"]["displayName"],
                "clientId": apps["claims_provider"]["appId"],
                "created": created["claims_provider"],
                "appIdUri": claims_provider_uri,
                "audience": claims_provider_uri,
                "applicationRole": "CustomClaimsProvider.Invoke",
            },
        },
        "enterpriseApplications": {
            key: {
                "displayName": service_principals[key]["displayName"],
                "clientId": service_principals[key]["appId"],
                "servicePrincipalId": service_principals[key]["id"],
                "created": service_principals_created[key],
            }
            for key in DISPLAY_NAMES
        },
        "configuration": {
            "spaConfig": {
                "auth.authority": args.authority,
                "auth.clientId": apps["spa"]["appId"],
                "api.scopes": [f"{frontend_uri}/assets.read", f"{frontend_uri}/assets.write"],
            },
            "frontendApi": {
                "Auth__Authority": f"{args.authority}/v2.0",
                "Auth__MetadataAddress": frontend_metadata_address,
                "Auth__Issuer": args.issuer,
                "Auth__Audience": frontend_uri,
                "BackendApi__ServiceTokenScopes__0": "<internal-entra-backend-api-audience>/.default",
            },
            "backendApi": {
                "Auth__Authority": f"{args.authority}/v2.0",
                "Auth__MetadataAddress": frontend_metadata_address,
                "Auth__Issuer": args.issuer,
                "Auth__Audience": frontend_uri,
                "ServiceAuth__Issuer": "https://sts.windows.net/<mngenv-tenant-id>/",
                "ServiceAuth__Audience": "<internal-entra-backend-api-audience>",
                "ServiceAuth__ReadRoles__0": "Backend.Read",
                "ServiceAuth__ReadRoles__1": "Backend.Write",
                "ServiceAuth__WriteRoles__0": "Backend.Write",
            },
            "customClaimsProvider": {
                "ExternalId__TenantId": args.tenant_id,
                "ExternalId__TenantName": args.tenant_name,
            },
            "azdEnv": {
                "EXTERNAL_ID_TENANT_ID": args.tenant_id,
                "EXTERNAL_ID_ISSUER": args.issuer,
                "EXTERNAL_ID_AUTHORITY": args.authority,
                "API_AUDIENCE": frontend_uri,
                "SPA_CLIENT_ID": apps["spa"]["appId"],
                "FRONTEND_API_CLIENT_ID": apps["frontend_api"]["appId"],
                "CLAIMS_PROVIDER_APP_ID": apps["claims_provider"]["appId"],
                "CLAIMS_PROVIDER_AUDIENCE": claims_provider_uri,
            },
        },
        "notes": [
            "No client secrets are created or stored by this script.",
            "Replace the deployed redirect placeholder only after Azure Static Web Apps has a real hostname.",
            "Backend API service authentication is intentionally not created in External ID. Configure it in the internal MngEnv Entra tenant.",
            "Configure the External ID Custom Authentication Extension callback separately if Microsoft Graph customAuthenticationExtensions permissions are not available to this script.",
        ],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", default=DEFAULT_TENANT_ID)
    parser.add_argument("--tenant-name", default=DEFAULT_TENANT_NAME)
    parser.add_argument("--issuer", default=DEFAULT_ISSUER)
    parser.add_argument("--authority", default=DEFAULT_AUTHORITY)
    parser.add_argument("--redirect-uri", action="append", default=list(DEFAULT_REDIRECT_URIS))
    parser.add_argument("--output", default="docs/external-id-app-registrations.local.json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        account = json.loads(run_az(["account", "show", "--output", "json"]))
        print(
            "Current Azure CLI account: "
            f"{account.get('user', {}).get('name')} in tenant {account.get('tenantId')} "
            f"({account.get('name')})",
            file=sys.stderr,
        )
        token = get_graph_token(args.tenant_id)

        apps: dict[str, dict[str, Any]] = {}
        created: dict[str, bool] = {}
        for key, display_name in DISPLAY_NAMES.items():
            apps[key], created[key] = create_or_get_app(token, display_name)

        apps["frontend_api"] = configure_api_app(
            token,
            apps["frontend_api"],
            args.tenant_id,
            app_id_uri=f"https://{args.tenant_name}.onmicrosoft.com/contoso-asset-management/frontend-api",
            scopes=[
                {
                    "value": "assets.read",
                    "admin_display": "Read tenant asset data",
                    "admin_description": "Allows reading portfolios, positions, and tenant asset data.",
                    "user_display": "Read asset data",
                    "user_description": "Read your tenant asset data.",
                },
                {
                    "value": "assets.write",
                    "admin_display": "Write tenant asset data",
                    "admin_description": "Allows approving transactions and changing tenant asset data.",
                    "user_display": "Write asset data",
                    "user_description": "Approve transactions and change your tenant asset data.",
                },
            ],
        )
        apps["claims_provider"] = configure_api_app(
            token,
            apps["claims_provider"],
            args.tenant_id,
            roles=[
                {
                    "value": "CustomClaimsProvider.Invoke",
                    "display_name": "Invoke Custom Claims Provider",
                    "description": "Allows External ID custom authentication extension calls to the claims provider endpoint.",
                }
            ],
        )
        apps["spa"] = configure_spa(token, apps["spa"], args.redirect_uri, apps["frontend_api"])
        apps["frontend_api"] = preauthorize_spa(token, apps["frontend_api"], apps["spa"])

        service_principals: dict[str, dict[str, Any]] = {}
        service_principals_created: dict[str, bool] = {}
        for key, app in apps.items():
            service_principals[key], service_principals_created[key] = (
                create_or_get_service_principal(token, app)
            )

        output = summarize(
            args,
            apps,
            created,
            service_principals,
            service_principals_created,
        )
        with open(args.output, "w", encoding="utf-8") as handle:
            json.dump(output, handle, indent=2)
            handle.write("\n")
        print(json.dumps(output, indent=2))
        print(f"Wrote {args.output}", file=sys.stderr)
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
