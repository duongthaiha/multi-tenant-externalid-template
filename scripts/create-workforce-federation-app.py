#!/usr/bin/env python3
"""Idempotently create the workforce Entra app used for External ID federation."""

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


DEFAULT_WORKFORCE_TENANT_ID = "66666666-6666-4666-8666-666666666666"
DEFAULT_WORKFORCE_TENANT_DOMAIN = "contosoworkforce.onmicrosoft.com"
DEFAULT_EXTERNAL_TENANT_ID = "11111111-1111-4111-8111-111111111111"
DEFAULT_EXTERNAL_TENANT_SUBDOMAIN = "contosoexternalid"
DEFAULT_DISPLAY_NAME = "Contoso Asset Management - External ID Federation"
DEFAULT_OUTPUT = "docs/workforce-entra-federation-app.local.json"
GRAPH_APP_ID = "00000003-0000-0000-c000-000000000000"
DEFAULT_APP_ROLE_ID = "00000000-0000-0000-0000-000000000000"
GRAPH_DELEGATED_SCOPES = ("openid", "profile", "email", "User.Read")
ALLOWED_ROLES = {"TenantAdmin", "PortfolioManager", "PortfolioViewer"}


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


def graph_request(
    token: str,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = None if body is None else json.dumps(body, separators=(",", ":")).encode("utf-8")
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


def require_uuid(value: str, field_name: str) -> str:
    try:
        return str(uuid.UUID(value))
    except (AttributeError, TypeError, ValueError) as exc:
        raise RuntimeError(f"{field_name} must be a UUID, got {value!r}.") from exc


def require_text(document: dict[str, Any], field_name: str, context: str) -> str:
    value = document.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"{context}.{field_name} must be a non-empty string.")
    return value.strip()


def validate_domain(value: str, field_name: str) -> str:
    value = value.strip().lower()
    if not value or any(character in value for character in "/:@ "):
        raise RuntimeError(f"{field_name} must be a DNS name, got {value!r}.")
    labels = value.split(".")
    if any(
        not label
        or len(label) > 63
        or label.startswith("-")
        or label.endswith("-")
        or not all(character.isalnum() or character == "-" for character in label)
        for label in labels
    ):
        raise RuntimeError(f"{field_name} must be a valid DNS name, got {value!r}.")
    return value


def validate_inputs(args: argparse.Namespace) -> tuple[str, str, str, str, str]:
    workforce_tenant_id = require_uuid(args.workforce_tenant_id, "--workforce-tenant-id")
    workforce_tenant_domain = validate_domain(
        args.workforce_tenant_domain,
        "--workforce-tenant-domain",
    )
    external_tenant_id = require_uuid(args.external_tenant_id, "--external-tenant-id")
    subdomain = validate_domain(args.external_tenant_subdomain, "--external-tenant-subdomain")
    if "." in subdomain:
        raise RuntimeError("--external-tenant-subdomain must be a single DNS label.")
    domain = validate_domain(
        args.external_tenant_domain or f"{subdomain}.onmicrosoft.com",
        "--external-tenant-domain",
    )
    if not isinstance(args.display_name, str) or not args.display_name.strip():
        raise RuntimeError("--display-name must be a non-empty string.")
    if not isinstance(args.output, str) or not args.output.strip():
        raise RuntimeError("--output must be a non-empty path.")
    return workforce_tenant_id, workforce_tenant_domain, external_tenant_id, subdomain, domain


def redirect_uris(external_tenant_id: str, subdomain: str, domain: str) -> list[str]:
    host = f"{subdomain}.ciamlogin.com"
    return [
        f"https://{host}/{external_tenant_id}/federation/oauth2",
        f"https://{host}/{domain}/federation/oauth2",
    ]


def load_authorized_users(path: str | None, workforce_tenant_id: str) -> list[dict[str, Any]]:
    if path is None:
        return []
    manifest_path = Path(path)
    try:
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RuntimeError(f"Unable to read authorized-user manifest {manifest_path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Authorized-user manifest {manifest_path} is not valid JSON: {exc}") from exc

    root_source_tenant_id: str | None = None
    if isinstance(document, list):
        records = document
    elif isinstance(document, dict):
        records = document.get("users")
        if "sourceTenantId" in document:
            root_source_tenant_id = require_uuid(document["sourceTenantId"], "sourceTenantId")
        if not isinstance(records, list):
            raise RuntimeError("Authorized-user manifest must contain a users array.")
    else:
        raise RuntimeError("Authorized-user manifest must be a JSON array or an object containing a users array.")

    if root_source_tenant_id is not None and root_source_tenant_id != workforce_tenant_id:
        raise RuntimeError(
            f"Manifest sourceTenantId {root_source_tenant_id} does not match workforce tenant {workforce_tenant_id}."
        )

    users: list[dict[str, Any]] = []
    seen_object_ids: set[str] = set()
    for index, record in enumerate(records):
        context = f"users[{index}]"
        if not isinstance(record, dict):
            raise RuntimeError(f"{context} must be an object.")
        record_source_tenant_id = record.get("sourceTenantId", root_source_tenant_id)
        if record_source_tenant_id is None:
            raise RuntimeError(f"{context}.sourceTenantId is required.")
        source_tenant_id = require_uuid(record_source_tenant_id, f"{context}.sourceTenantId")
        if source_tenant_id != workforce_tenant_id:
            raise RuntimeError(
                f"{context}.sourceTenantId {source_tenant_id} does not match workforce tenant {workforce_tenant_id}."
            )
        source_object_id = require_uuid(
            require_text(record, "sourceObjectId", context),
            f"{context}.sourceObjectId",
        )
        if source_object_id in seen_object_ids:
            raise RuntimeError(f"Duplicate sourceObjectId {source_object_id} in authorized-user manifest.")
        seen_object_ids.add(source_object_id)

        email = require_text(record, "email", context).lower()
        if email.count("@") != 1 or email.startswith("@") or email.endswith("@"):
            raise RuntimeError(f"{context}.email must be a valid email address.")
        display_name = require_text(record, "displayName", context)
        business_tenant_id = require_text(record, "businessTenantId", context)
        status = require_text(record, "status", context).lower()
        if status != "active":
            raise RuntimeError(f"{context}.status must be 'active' before the user can be authorized.")
        roles = record.get("roles")
        if (
            not isinstance(roles, list)
            or not roles
            or any(not isinstance(role, str) or not role.strip() for role in roles)
        ):
            raise RuntimeError(f"{context}.roles must be a non-empty array of strings.")
        normalized_roles = sorted(set(role.strip() for role in roles))
        unsupported_roles = sorted(set(normalized_roles) - ALLOWED_ROLES)
        if unsupported_roles:
            raise RuntimeError(
                f"{context}.roles contains unsupported roles: {', '.join(unsupported_roles)}."
            )

        users.append(
            {
                "sourceTenantId": source_tenant_id,
                "sourceObjectId": source_object_id,
                "email": email,
                "displayName": display_name,
                "businessTenantId": business_tenant_id,
                "roles": normalized_roles,
                "status": status,
            }
        )
    return users


def filter_literal(value: str) -> str:
    return value.replace("'", "''")


def find_app(token: str, display_name: str) -> dict[str, Any] | None:
    query = urllib.parse.urlencode(
        {
            "$filter": f"displayName eq '{filter_literal(display_name)}'",
            "$select": (
                "id,appId,displayName,signInAudience,web,optionalClaims,"
                "requiredResourceAccess,appRoles"
            ),
        }
    )
    matches = graph_request(token, "GET", f"/applications?{query}").get("value", [])
    if len(matches) > 1:
        ids = ", ".join(app["appId"] for app in matches)
        raise RuntimeError(f"Multiple app registrations named {display_name!r} already exist: {ids}")
    return matches[0] if matches else None


def create_or_get_app(token: str, display_name: str) -> tuple[dict[str, Any], bool]:
    existing = find_app(token, display_name)
    if existing is not None:
        return existing, False
    graph_request(
        token,
        "POST",
        "/applications",
        {
            "displayName": display_name,
            "signInAudience": "AzureADMyOrg",
            "passwordCredentials": [],
        },
    )
    created = find_app(token, display_name)
    if created is None:
        raise RuntimeError(f"Unable to find newly created app registration {display_name!r}.")
    return created, True


def graph_scope_ids(token: str) -> dict[str, str]:
    query = urllib.parse.urlencode(
        {
            "$filter": f"appId eq '{GRAPH_APP_ID}'",
            "$select": "id,appId,oauth2PermissionScopes",
        }
    )
    matches = graph_request(token, "GET", f"/servicePrincipals?{query}").get("value", [])
    if len(matches) != 1:
        raise RuntimeError(f"Expected one Microsoft Graph service principal, found {len(matches)}.")
    available = {
        scope["value"]: scope["id"]
        for scope in matches[0].get("oauth2PermissionScopes", [])
        if scope.get("isEnabled") and scope.get("value") and scope.get("id")
    }
    missing = [scope for scope in GRAPH_DELEGATED_SCOPES if scope not in available]
    if missing:
        raise RuntimeError(f"Microsoft Graph service principal is missing delegated scopes: {', '.join(missing)}.")
    return {scope: available[scope] for scope in GRAPH_DELEGATED_SCOPES}


def configure_app(
    token: str,
    app: dict[str, Any],
    planned_redirect_uris: list[str],
    scope_ids: dict[str, str],
) -> dict[str, Any]:
    required_resource_access = [
        item for item in app.get("requiredResourceAccess", []) if item.get("resourceAppId") != GRAPH_APP_ID
    ]
    current_graph_access = next(
        (
            item.get("resourceAccess", [])
            for item in app.get("requiredResourceAccess", [])
            if item.get("resourceAppId") == GRAPH_APP_ID
        ),
        [],
    )
    by_permission = {
        (item.get("id"), item.get("type")): item
        for item in current_graph_access
        if item.get("id") and item.get("type")
    }
    for scope_id in scope_ids.values():
        by_permission[(scope_id, "Scope")] = {"id": scope_id, "type": "Scope"}
    required_resource_access.append(
        {
            "resourceAppId": GRAPH_APP_ID,
            "resourceAccess": sorted(
                by_permission.values(),
                key=lambda item: (item["type"], item["id"]),
            ),
        }
    )

    optional_claims = dict(app.get("optionalClaims") or {})
    id_token_claims = [
        claim for claim in optional_claims.get("idToken", []) if claim.get("name") != "email"
    ]
    id_token_claims.append(
        {
            "name": "email",
            "source": None,
            "essential": False,
            "additionalProperties": [],
        }
    )
    optional_claims["idToken"] = id_token_claims

    graph_request(
        token,
        "PATCH",
        f"/applications/{app['id']}",
        {
            "signInAudience": "AzureADMyOrg",
            "web": {"redirectUris": planned_redirect_uris},
            "requiredResourceAccess": required_resource_access,
            "optionalClaims": optional_claims,
        },
    )
    refreshed = find_app(token, app["displayName"])
    if refreshed is None:
        raise RuntimeError(f"Unable to refresh app registration {app['displayName']!r}.")
    return refreshed


def find_service_principal(token: str, app_id: str) -> dict[str, Any] | None:
    query = urllib.parse.urlencode(
        {
            "$filter": f"appId eq '{filter_literal(app_id)}'",
            "$select": "id,appId,displayName,appRoleAssignmentRequired",
        }
    )
    matches = graph_request(token, "GET", f"/servicePrincipals?{query}").get("value", [])
    if len(matches) > 1:
        ids = ", ".join(service_principal["id"] for service_principal in matches)
        raise RuntimeError(f"Multiple service principals with appId {app_id!r} already exist: {ids}")
    return matches[0] if matches else None


def create_or_get_service_principal(token: str, app_id: str) -> tuple[dict[str, Any], bool]:
    existing = find_service_principal(token, app_id)
    created = existing is None
    if existing is None:
        graph_request(token, "POST", "/servicePrincipals", {"appId": app_id})
        existing = find_service_principal(token, app_id)
    if existing is None:
        raise RuntimeError(f"Unable to find newly created service principal for appId {app_id!r}.")
    if existing.get("appRoleAssignmentRequired") is not True:
        graph_request(
            token,
            "PATCH",
            f"/servicePrincipals/{existing['id']}",
            {"appRoleAssignmentRequired": True},
        )
        existing = find_service_principal(token, app_id)
        if existing is None:
            raise RuntimeError(f"Unable to refresh service principal for appId {app_id!r}.")
    return existing, created


def resolve_authorized_users(
    token: str,
    manifest_users: list[dict[str, Any]],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    resolved: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for manifest_user in manifest_users:
        object_id = urllib.parse.quote(manifest_user["sourceObjectId"], safe="")
        directory_user = graph_request(
            token,
            "GET",
            f"/users/{object_id}?$select=id,displayName,mail,userPrincipalName,accountEnabled",
        )
        if directory_user.get("id", "").lower() != manifest_user["sourceObjectId"]:
            raise RuntimeError(f"Graph returned the wrong user for object ID {manifest_user['sourceObjectId']}.")
        if directory_user.get("accountEnabled") is not True:
            raise RuntimeError(
                f"Workforce user {manifest_user['sourceObjectId']} ({manifest_user['email']}) is disabled."
            )
        resolved.append((manifest_user, directory_user))
    return resolved


def assign_authorized_users(
    token: str,
    service_principal_id: str,
    users: list[tuple[dict[str, Any], dict[str, Any]]],
) -> list[dict[str, Any]]:
    assignments: list[dict[str, Any]] = []
    for manifest_user, directory_user in users:
        query = urllib.parse.urlencode({"$select": "id,appRoleId,resourceId"})
        existing = graph_request(
            token,
            "GET",
            f"/users/{directory_user['id']}/appRoleAssignments?{query}",
        ).get("value", [])
        assignment = next(
            (
                item
                for item in existing
                if item.get("resourceId") == service_principal_id
                and item.get("appRoleId") == DEFAULT_APP_ROLE_ID
            ),
            None,
        )
        created = assignment is None
        if assignment is None:
            assignment = graph_request(
                token,
                "POST",
                f"/users/{directory_user['id']}/appRoleAssignments",
                {
                    "principalId": directory_user["id"],
                    "resourceId": service_principal_id,
                    "appRoleId": DEFAULT_APP_ROLE_ID,
                },
            )
        assignments.append(
            {
                **manifest_user,
                "workforceDisplayName": directory_user.get("displayName"),
                "workforceUserPrincipalName": directory_user.get("userPrincipalName"),
                "assignmentId": assignment.get("id"),
                "appRoleId": DEFAULT_APP_ROLE_ID,
                "created": created,
            }
        )
    return assignments


def provider_values(workforce_tenant_domain: str, client_id: str) -> dict[str, str]:
    return {
        "wellKnownEndpoint": (
            "https://login.microsoftonline.com/organizations/v2.0/"
            ".well-known/openid-configuration"
        ),
        "issuer": f"https://login.microsoftonline.com/{workforce_tenant_domain}/v2.0",
        "clientId": client_id,
        "clientAuthentication": "client_secret_post",
        "scope": "openid profile",
        "responseType": "code",
    }


def dry_run_summary(
    args: argparse.Namespace,
    workforce_tenant_id: str,
    workforce_tenant_domain: str,
    external_tenant_id: str,
    external_tenant_subdomain: str,
    external_tenant_domain: str,
    planned_redirect_uris: list[str],
    manifest_users: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "dryRun": True,
        "workforceTenantId": workforce_tenant_id,
        "workforceTenantDomain": workforce_tenant_domain,
        "externalTenant": {
            "tenantId": external_tenant_id,
            "subdomain": external_tenant_subdomain,
            "domain": external_tenant_domain,
        },
        "application": {
            "displayName": args.display_name.strip(),
            "signInAudience": "AzureADMyOrg",
            "web": {"redirectUris": planned_redirect_uris},
            "microsoftGraphDelegatedPermissions": list(GRAPH_DELEGATED_SCOPES),
            "optionalClaims": {"idToken": ["email"]},
        },
        "servicePrincipal": {"appRoleAssignmentRequired": True},
        "authorizedUsers": manifest_users,
        "externalIdPortalProviderValues": provider_values(
            workforce_tenant_domain,
            "<workforce-federation-app-client-id>",
        ),
        "output": args.output,
        "notes": [
            "Dry-run performs local validation only; it does not acquire a Graph token or write output.",
            "The client ID is available only after the app is created or resolved.",
            "The script never creates or prints a client secret.",
        ],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workforce-tenant-id", default=DEFAULT_WORKFORCE_TENANT_ID)
    parser.add_argument(
        "--workforce-tenant-domain",
        default=DEFAULT_WORKFORCE_TENANT_DOMAIN,
        help="Verified workforce tenant domain used by External ID issuer acceleration.",
    )
    parser.add_argument("--external-tenant-id", default=DEFAULT_EXTERNAL_TENANT_ID)
    parser.add_argument("--external-tenant-subdomain", default=DEFAULT_EXTERNAL_TENANT_SUBDOMAIN)
    parser.add_argument(
        "--external-tenant-domain",
        help="External tenant domain used by the second callback. Defaults to <subdomain>.onmicrosoft.com.",
    )
    parser.add_argument("--display-name", default=DEFAULT_DISPLAY_NAME)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--authorized-user-manifest",
        help=(
            "Local JSON manifest containing sourceTenantId, sourceObjectId, email, "
            "displayName, businessTenantId, roles, and active status."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate local inputs and print the exact plan without Graph authentication or writes.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        (
            workforce_tenant_id,
            workforce_tenant_domain,
            external_tenant_id,
            subdomain,
            domain,
        ) = validate_inputs(args)
        manifest_users = load_authorized_users(args.authorized_user_manifest, workforce_tenant_id)
        planned_redirect_uris = redirect_uris(external_tenant_id, subdomain, domain)
        if args.dry_run:
            print(
                json.dumps(
                    dry_run_summary(
                        args,
                        workforce_tenant_id,
                        workforce_tenant_domain,
                        external_tenant_id,
                        subdomain,
                        domain,
                        planned_redirect_uris,
                        manifest_users,
                    ),
                    indent=2,
                )
            )
            return 0

        token = get_graph_token(workforce_tenant_id)
        resolved_users = resolve_authorized_users(token, manifest_users)
        app, app_created = create_or_get_app(token, args.display_name.strip())
        scope_ids = graph_scope_ids(token)
        app = configure_app(token, app, planned_redirect_uris, scope_ids)
        service_principal, service_principal_created = create_or_get_service_principal(
            token,
            app["appId"],
        )
        assignments = assign_authorized_users(token, service_principal["id"], resolved_users)

        output = {
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "workforceTenantId": workforce_tenant_id,
            "workforceTenantDomain": workforce_tenant_domain,
            "externalTenant": {
                "tenantId": external_tenant_id,
                "subdomain": subdomain,
                "domain": domain,
            },
            "application": {
                "displayName": app["displayName"],
                "applicationObjectId": app["id"],
                "clientId": app["appId"],
                "created": app_created,
                "signInAudience": "AzureADMyOrg",
                "redirectUris": planned_redirect_uris,
                "microsoftGraphDelegatedPermissions": [
                    {"value": value, "id": scope_ids[value]}
                    for value in GRAPH_DELEGATED_SCOPES
                ],
                "optionalClaims": {"idToken": ["email"]},
            },
            "servicePrincipal": {
                "id": service_principal["id"],
                "created": service_principal_created,
                "appRoleAssignmentRequired": True,
            },
            "assignments": assignments,
            "externalIdPortalProviderValues": provider_values(
                workforce_tenant_domain,
                app["appId"],
            ),
            "notes": [
                "This file contains no access tokens, client secrets, or secret values.",
                "Create the federation app client secret manually as an operational step, store it in Key Vault, and configure expiration monitoring and rotation.",
                "The script intentionally does not create or print client secrets.",
                "Existing app-role assignments are never removed; only missing default assignments for users listed in the manifest are created.",
                "Enable the configured provider in the External ID sign-up/sign-in user flow separately.",
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
