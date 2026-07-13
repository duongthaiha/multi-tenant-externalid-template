#!/usr/bin/env python3
"""Idempotently precreate workforce-federated users in Azure External ID."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_WORKFORCE_TENANT_ID = "66666666-6666-4666-8666-666666666666"
DEFAULT_WORKFORCE_TENANT_DOMAIN = "contosoworkforce.onmicrosoft.com"
DEFAULT_EXTERNAL_TENANT_ID = "11111111-1111-4111-8111-111111111111"
DEFAULT_OUTPUT = Path("docs/workforce-federated-users.local.json")
ALLOWED_BUSINESS_TENANTS = {
    "AlphaCapital",
    "BetaWealth",
    "GammaFund",
    "DeltaEquity",
}


def load_registration_helper() -> ModuleType:
    helper_path = SCRIPT_DIR / "create-workforce-federation-app.py"
    spec = importlib.util.spec_from_file_location("_workforce_federation_app", helper_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load manifest validator from {helper_path}.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_REGISTRATION = load_registration_helper()
get_graph_token = _REGISTRATION.get_graph_token
require_uuid = _REGISTRATION.require_uuid


class GraphError(RuntimeError):
    """Raised when Microsoft Graph returns an error."""


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
        raise GraphError(
            f"Graph {method} {path} failed: HTTP {exc.code}: {detail}"
        ) from exc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        required=True,
        help="JSON authorized-user manifest validated by create-workforce-federation-app.py.",
    )
    parser.add_argument("--workforce-tenant-id", default=DEFAULT_WORKFORCE_TENANT_ID)
    parser.add_argument(
        "--workforce-tenant-domain",
        default=DEFAULT_WORKFORCE_TENANT_DOMAIN,
        help="Verified workforce tenant domain used by the External ID provider issuer.",
    )
    parser.add_argument("--external-tenant-id", default=DEFAULT_EXTERNAL_TENANT_ID)
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help="Ignored local JSON mapping output. Contains no tokens or secrets.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Perform local validation and print the plan without authentication, network calls, or output writes.",
    )
    return parser.parse_args()


def validate_inputs(args: argparse.Namespace) -> tuple[str, str, str, Path, Path]:
    workforce_tenant_id = require_uuid(args.workforce_tenant_id, "--workforce-tenant-id")
    workforce_tenant_domain = _REGISTRATION.validate_domain(
        args.workforce_tenant_domain,
        "--workforce-tenant-domain",
    )
    external_tenant_id = require_uuid(args.external_tenant_id, "--external-tenant-id")
    if workforce_tenant_id == external_tenant_id:
        raise RuntimeError("Workforce and External ID tenant IDs must be different.")
    if not isinstance(args.manifest, str) or not args.manifest.strip():
        raise RuntimeError("--manifest must be a non-empty path.")
    if not isinstance(args.output, str) or not args.output.strip():
        raise RuntimeError("--output must be a non-empty path.")
    manifest_path = Path(args.manifest)
    output_path = Path(args.output)
    if manifest_path.resolve() == output_path.resolve():
        raise RuntimeError("--output must not overwrite the input manifest.")
    return (
        workforce_tenant_id,
        workforce_tenant_domain,
        external_tenant_id,
        manifest_path,
        output_path,
    )


def load_manifest(path: Path, workforce_tenant_id: str) -> list[dict[str, Any]]:
    users = _REGISTRATION.load_authorized_users(str(path), workforce_tenant_id)
    if not users:
        raise RuntimeError("Authorized-user manifest must contain at least one user.")
    for index, user in enumerate(users):
        business_tenant_id = user["businessTenantId"]
        if business_tenant_id not in ALLOWED_BUSINESS_TENANTS:
            allowed = ", ".join(sorted(ALLOWED_BUSINESS_TENANTS))
            raise RuntimeError(
                f"users[{index}].businessTenantId {business_tenant_id!r} is invalid; "
                f"allowed values are: {allowed}."
            )
    return users


def federated_issuer(workforce_tenant_domain: str, external_tenant_id: str) -> str:
    return (
        f"https://login.microsoftonline.com/{workforce_tenant_domain}/v2.0"
        f"<{external_tenant_id}>"
    )


def legacy_federated_issuer(workforce_tenant_id: str, external_tenant_id: str) -> str:
    return (
        f"https://login.microsoftonline.com/{workforce_tenant_id}/v2.0/"
        f"{external_tenant_id}"
    )


def validate_source_user(
    manifest_user: dict[str, Any],
    directory_user: dict[str, Any],
) -> None:
    source_object_id = manifest_user["sourceObjectId"]
    if str(directory_user.get("id", "")).casefold() != source_object_id.casefold():
        raise RuntimeError(f"Graph returned the wrong workforce user for object ID {source_object_id}.")
    if directory_user.get("accountEnabled") is not True:
        raise RuntimeError(
            f"Workforce user {source_object_id} ({manifest_user['email']}) is disabled."
        )
    expected_email = manifest_user["email"].casefold()
    directory_addresses = {
        value.casefold()
        for value in (directory_user.get("mail"), directory_user.get("userPrincipalName"))
        if isinstance(value, str) and value.strip()
    }
    if expected_email not in directory_addresses:
        actual = ", ".join(sorted(directory_addresses)) or "<none>"
        raise RuntimeError(
            f"Manifest email mismatch for workforce user {source_object_id}: "
            f"expected {manifest_user['email']}, Graph mail/UPN values are {actual}."
        )


def resolve_source_users(
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
        validate_source_user(manifest_user, directory_user)
        resolved.append((manifest_user, directory_user))
    return resolved


def filter_literal(value: str) -> str:
    return value.replace("'", "''")


def has_federated_identity(
    user: dict[str, Any],
    issuer: str,
    source_object_id: str,
) -> bool:
    return any(
        isinstance(identity, dict)
        and identity.get("signInType") == "federated"
        and identity.get("issuer") == issuer
        and identity.get("issuerAssignedId") == source_object_id
        for identity in user.get("identities", [])
    )


def validate_external_user(
    user: dict[str, Any],
    issuer: str,
    source_object_id: str,
) -> dict[str, Any]:
    external_id = user.get("id")
    if not isinstance(external_id, str) or not external_id:
        raise RuntimeError("External ID Graph returned a user without an object ID.")
    if not has_federated_identity(user, issuer, source_object_id):
        raise RuntimeError(
            f"External ID user {external_id} does not contain the exact federated identity "
            f"for issuer {issuer!r} and source object ID {source_object_id}."
        )
    return user


def find_external_user(
    token: str,
    issuer: str,
    source_object_id: str,
) -> dict[str, Any] | None:
    identity_filter = (
        "identities/any(c:c/issuerAssignedId eq "
        f"'{filter_literal(source_object_id)}' and "
        f"c/issuer eq '{filter_literal(issuer)}')"
    )
    query = urllib.parse.urlencode(
        {
            "$filter": identity_filter,
            "$select": "id,displayName,mail,otherMails,identities,accountEnabled",
        }
    )
    response = graph_request(token, "GET", f"/users?{query}")
    matches = response.get("value", [])
    if not isinstance(matches, list):
        raise RuntimeError("External ID Graph returned an invalid user search response.")
    if len(matches) > 1 or response.get("@odata.nextLink"):
        ids = ", ".join(str(match.get("id", "<missing-id>")) for match in matches)
        raise RuntimeError(
            f"Multiple External ID users match federated source object ID "
            f"{source_object_id}: {ids}"
        )
    if not matches:
        return None
    return validate_external_user(matches[0], issuer, source_object_id)


def create_external_user(
    token: str,
    manifest_user: dict[str, Any],
    issuer: str,
) -> dict[str, Any]:
    source_object_id = manifest_user["sourceObjectId"]
    identity = {
        "signInType": "federated",
        "issuer": issuer,
        "issuerAssignedId": source_object_id,
    }
    created = graph_request(
        token,
        "POST",
        "/users",
        {
            "accountEnabled": True,
            "displayName": manifest_user["displayName"],
            "mail": manifest_user["email"],
            "otherMails": [manifest_user["email"]],
            "identities": [identity],
        },
    )
    external_id = created.get("id")
    if not isinstance(external_id, str) or not external_id:
        raise RuntimeError("External ID Graph did not return an object ID for the created user.")
    object_id = urllib.parse.quote(external_id, safe="")
    reread = graph_request(
        token,
        "GET",
        f"/users/{object_id}?$select=id,displayName,mail,otherMails,identities,accountEnabled",
    )
    return validate_external_user(reread, issuer, source_object_id)


def update_federated_identity(
    token: str,
    user: dict[str, Any],
    source_object_id: str,
    old_issuer: str,
    new_issuer: str,
) -> dict[str, Any]:
    identities = user.get("identities")
    if not isinstance(identities, list):
        raise RuntimeError(f"External ID user {user.get('id')} has an invalid identities collection.")

    updated_identities: list[dict[str, Any]] = []
    replaced = False
    for identity in identities:
        if (
            isinstance(identity, dict)
            and identity.get("signInType") == "federated"
            and identity.get("issuer") == old_issuer
            and identity.get("issuerAssignedId") == source_object_id
        ):
            updated_identities.append(
                {
                    "signInType": "federated",
                    "issuer": new_issuer,
                    "issuerAssignedId": source_object_id,
                }
            )
            replaced = True
        else:
            updated_identities.append(identity)

    if not replaced:
        raise RuntimeError(
            f"External ID user {user.get('id')} does not contain the expected legacy federated identity."
        )

    object_id = urllib.parse.quote(user["id"], safe="")
    graph_request(token, "PATCH", f"/users/{object_id}", {"identities": updated_identities})
    reread = graph_request(
        token,
        "GET",
        f"/users/{object_id}?$select=id,displayName,mail,otherMails,identities,accountEnabled",
    )
    return validate_external_user(reread, new_issuer, source_object_id)


def provision_users(
    external_token: str,
    resolved_users: list[tuple[dict[str, Any], dict[str, Any]]],
    issuer: str,
    legacy_issuer: str,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for manifest_user, _ in resolved_users:
        external_user = find_external_user(
            external_token,
            issuer,
            manifest_user["sourceObjectId"],
        )
        created = external_user is None
        issuer_updated = False
        if external_user is None:
            legacy_user = find_external_user(
                external_token,
                legacy_issuer,
                manifest_user["sourceObjectId"],
            )
            if legacy_user is not None:
                external_user = update_federated_identity(
                    external_token,
                    legacy_user,
                    manifest_user["sourceObjectId"],
                    legacy_issuer,
                    issuer,
                )
                created = False
                issuer_updated = True
            else:
                external_user = create_external_user(external_token, manifest_user, issuer)
        results.append(
            {
                "sourceTenantId": manifest_user["sourceTenantId"],
                "sourceObjectId": manifest_user["sourceObjectId"],
                "externalIdObjectId": external_user["id"],
                "email": manifest_user["email"],
                "displayName": manifest_user["displayName"],
                "businessTenantId": manifest_user["businessTenantId"],
                "roles": manifest_user["roles"],
                "status": manifest_user["status"],
                "created": created,
                "issuerUpdated": issuer_updated,
                "federatedIssuer": issuer,
            }
        )
    return results


def dry_run_summary(
    workforce_tenant_id: str,
    external_tenant_id: str,
    issuer: str,
    users: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "dryRun": True,
        "workforceTenantId": workforce_tenant_id,
        "externalTenantId": external_tenant_id,
        "federatedIssuer": issuer,
        "users": users,
        "notes": [
            "Dry-run performs local validation only.",
            "No authentication, network request, or output write was performed.",
        ],
    }


def safe_summary(
    workforce_tenant_id: str,
    external_tenant_id: str,
    users: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "workforceTenantId": workforce_tenant_id,
        "externalTenantId": external_tenant_id,
        "users": users,
        "notes": ["This file contains no access tokens, passwords, or secrets."],
    }


def write_json(path: Path, value: dict[str, Any]) -> None:
    if path.is_symlink():
        raise RuntimeError(f"Refusing to write output through symbolic link {path}.")
    path.parent.mkdir(parents=True, exist_ok=True)
    pending_path = path.with_name(f"{path.name}.new")
    if pending_path.is_symlink():
        raise RuntimeError(f"Refusing to write output through symbolic link {pending_path}.")
    try:
        with pending_path.open("w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2)
            handle.write("\n")
        os.replace(pending_path, path)
    finally:
        if pending_path.exists():
            pending_path.unlink()


def main() -> int:
    args = parse_args()
    try:
        (
            workforce_tenant_id,
            workforce_tenant_domain,
            external_tenant_id,
            manifest_path,
            output_path,
        ) = validate_inputs(args)
        users = load_manifest(manifest_path, workforce_tenant_id)
        issuer = federated_issuer(workforce_tenant_domain, external_tenant_id)
        legacy_issuer = legacy_federated_issuer(workforce_tenant_id, external_tenant_id)
        if args.dry_run:
            print(
                json.dumps(
                    dry_run_summary(
                        workforce_tenant_id,
                        external_tenant_id,
                        issuer,
                        users,
                    ),
                    indent=2,
                )
            )
            return 0

        workforce_token = get_graph_token(workforce_tenant_id)
        external_token = get_graph_token(external_tenant_id)
        resolved_users = resolve_source_users(workforce_token, users)
        provisioned_users = provision_users(
            external_token,
            resolved_users,
            issuer,
            legacy_issuer,
        )
        output = safe_summary(
            workforce_tenant_id,
            external_tenant_id,
            provisioned_users,
        )
        write_json(output_path, output)
        print(json.dumps(output, indent=2))
        print(f"Wrote safe federated-user mapping to {output_path}", file=sys.stderr)
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
