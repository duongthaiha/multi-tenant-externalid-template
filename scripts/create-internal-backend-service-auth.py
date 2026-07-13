#!/usr/bin/env python3
"""Create internal Entra backend API service-auth app registration without secrets."""

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
from pathlib import Path
from typing import Any


DEFAULT_MNGENV_TENANT_ID = "55555555-5555-4555-8555-555555555555"
DEFAULT_OUTPUT = "docs/internal-entra-service-auth.local.json"
BACKEND_DISPLAY_NAME = "Contoso Asset Management POC Backend Service Auth"
BACKEND_APP_ROLES = [
    {
        "value": "Backend.Read",
        "display_name": "Backend API read access",
        "description": "Allows an approved workload identity to call backend read operations.",
    },
    {
        "value": "Backend.Write",
        "display_name": "Backend API write access",
        "description": "Allows an approved workload identity to call backend write operations.",
    },
]


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
        raise RuntimeError(f"Graph {method} {path} failed: HTTP {exc.code}: {detail}") from exc


def filter_literal(value: str) -> str:
    return value.replace("'", "''")


def find_app(token: str, display_name: str) -> dict[str, Any] | None:
    query = urllib.parse.urlencode(
        {
            "$filter": f"displayName eq '{filter_literal(display_name)}'",
            "$select": "id,appId,displayName,identifierUris,api,appRoles",
        }
    )
    result = graph_request(token, "GET", f"/applications?{query}")
    matches = result.get("value", [])
    if len(matches) > 1:
        ids = ", ".join(app["appId"] for app in matches)
        raise RuntimeError(f"Multiple app registrations named {display_name!r} already exist: {ids}")
    return matches[0] if matches else None


def find_service_principal_by_app_id(token: str, app_id: str) -> dict[str, Any] | None:
    query = urllib.parse.urlencode(
        {
            "$filter": f"appId eq '{filter_literal(app_id)}'",
            "$select": "id,appId,displayName,appRoles,appRoleAssignments",
        }
    )
    result = graph_request(token, "GET", f"/servicePrincipals?{query}")
    matches = result.get("value", [])
    if len(matches) > 1:
        ids = ", ".join(sp["id"] for sp in matches)
        raise RuntimeError(f"Multiple service principals with appId {app_id!r} already exist: {ids}")
    return matches[0] if matches else None


def create_or_get_service_principal(token: str, app_id: str) -> dict[str, Any]:
    existing = find_service_principal_by_app_id(token, app_id)
    if existing is not None:
        return existing
    graph_request(token, "POST", "/servicePrincipals", {"appId": app_id})
    created = find_service_principal_by_app_id(token, app_id)
    if created is None:
        raise RuntimeError(f"Unable to find newly created service principal for appId {app_id!r}")
    return created


def create_or_get_app(token: str, display_name: str) -> tuple[dict[str, Any], bool]:
    existing = find_app(token, display_name)
    if existing is not None:
        return existing, False
    graph_request(token, "POST", "/applications", {"displayName": display_name, "signInAudience": "AzureADMyOrg"})
    created = find_app(token, display_name)
    if created is None:
        raise RuntimeError(f"Unable to find newly created app registration {display_name!r}")
    return created, True


def stable_guid(*parts: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "|".join(parts)))


def configure_backend_app(token: str, app: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    app_id_uri = f"api://{app['appId']}"
    current_roles = list(app.get("appRoles") or [])
    by_value = {role.get("value"): role for role in current_roles if role.get("value")}
    for role_definition in BACKEND_APP_ROLES:
        existing = by_value.get(role_definition["value"])
        role_id = existing.get("id") if existing else stable_guid(tenant_id, app["displayName"], role_definition["value"])
        by_value[role_definition["value"]] = {
            "allowedMemberTypes": ["Application"],
            "description": role_definition["description"],
            "displayName": role_definition["display_name"],
            "id": role_id,
            "isEnabled": True,
            "value": role_definition["value"],
        }
    roles = list(by_value.values())
    graph_request(
        token,
        "PATCH",
        f"/applications/{app['id']}",
        {
            "identifierUris": [app_id_uri],
            "api": {"requestedAccessTokenVersion": 2},
            "appRoles": roles,
        },
    )
    refreshed = find_app(token, app["displayName"])
    if refreshed is None:
        raise RuntimeError("Unable to refresh backend service auth app")
    return refreshed


def assign_app_roles(
    token: str,
    resource_sp: dict[str, Any],
    principal_client_id: str,
    role_values: list[str],
) -> list[str]:
    principal_sp = create_or_get_service_principal(token, principal_client_id)
    existing_assignments = graph_request(
        token,
        "GET",
        f"/servicePrincipals/{principal_sp['id']}/appRoleAssignments?$select=id,appRoleId,resourceId",
    ).get("value", [])
    resource_roles = {
        role["value"]: role["id"]
        for role in resource_sp.get("appRoles", [])
        if role.get("isEnabled") and role.get("value") in role_values
    }
    missing = [role for role in role_values if role not in resource_roles]
    if missing:
        raise RuntimeError(f"Backend service principal is missing app roles: {', '.join(missing)}")

    assigned: list[str] = []
    for role_value, role_id in resource_roles.items():
        already_assigned = any(
            item.get("resourceId") == resource_sp["id"] and item.get("appRoleId") == role_id
            for item in existing_assignments
        )
        if not already_assigned:
            graph_request(
                token,
                "POST",
                f"/servicePrincipals/{principal_sp['id']}/appRoleAssignments",
                {
                    "principalId": principal_sp["id"],
                    "resourceId": resource_sp["id"],
                    "appRoleId": role_id,
                },
            )
        assigned.append(role_value)
    return sorted(assigned)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", default=DEFAULT_MNGENV_TENANT_ID)
    parser.add_argument("--display-name", default=BACKEND_DISPLAY_NAME)
    parser.add_argument(
        "--assign-read-client-id",
        action="append",
        default=[],
        help="Managed identity or service principal client ID to grant Backend.Read. Repeat for multiple callers.",
    )
    parser.add_argument(
        "--assign-write-client-id",
        action="append",
        default=[],
        help="Managed identity or service principal client ID to grant Backend.Write. Repeat for multiple callers.",
    )
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.dry_run:
            print("Would create/update internal Entra backend service-auth app registration.")
            print(f"Tenant: {args.tenant_id}")
            print(f"Display name: {args.display_name}")
            print("Creates Backend.Read and Backend.Write application roles.")
            print("Optionally assigns those roles to caller service principals by client ID.")
            return 0

        token = get_graph_token(args.tenant_id)
        app, created = create_or_get_app(token, args.display_name)
        app = configure_backend_app(token, app, args.tenant_id)
        service_token_scope = f"api://{app['appId']}/.default"
        audience = app["appId"]
        resource_sp = create_or_get_service_principal(token, app["appId"])
        assignments: dict[str, list[str]] = {}
        for client_id in args.assign_read_client_id:
            assignments.setdefault(client_id, [])
            assignments[client_id] = sorted(set(assignments[client_id] + assign_app_roles(token, resource_sp, client_id, ["Backend.Read"])))
        for client_id in args.assign_write_client_id:
            assignments.setdefault(client_id, [])
            assignments[client_id] = sorted(set(assignments[client_id] + assign_app_roles(token, resource_sp, client_id, ["Backend.Write"])))
        output = {
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "tenant": {
                "tenantId": args.tenant_id,
                "issuer": f"https://sts.windows.net/{args.tenant_id}/",
                "authority": f"https://login.microsoftonline.com/{args.tenant_id}",
            },
            "backendServiceAuth": {
                "displayName": app["displayName"],
                "clientId": app["appId"],
                "created": created,
                "audience": audience,
                "serviceTokenScope": service_token_scope,
                "applicationRoles": [role["value"] for role in BACKEND_APP_ROLES],
                "roleAssignments": assignments,
            },
            "azdEnv": {
                "BACKEND_SERVICE_AUTHORITY": f"https://login.microsoftonline.com/{args.tenant_id}",
                "BACKEND_SERVICE_ISSUER": f"https://sts.windows.net/{args.tenant_id}/",
                "BACKEND_API_AUDIENCE": audience,
                "BACKEND_API_SERVICE_TOKEN_SCOPE": service_token_scope,
            },
            "notes": [
                "No client secrets are created or stored by this script.",
                "Bicep creates the frontend API and APIM MCP gateway managed identities. Grant them Backend.Read and/or Backend.Write with --assign-read-client-id and --assign-write-client-id.",
                "Adding a new backend service caller should be an Entra app role assignment, not a backend app deployment.",
            ],
        }
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as handle:
            json.dump(output, handle, indent=2)
            handle.write("\n")
        print(json.dumps(output, indent=2))
        print(f"Wrote {output_path}", file=sys.stderr)
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
