#!/usr/bin/env python3
"""Seed or disable workforce-federated control-plane entitlements."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_WORKFORCE_TENANT_ID = "66666666-6666-4666-8666-666666666666"
DEFAULT_WORKFORCE_TENANT_DOMAIN = "contosoworkforce.onmicrosoft.com"
DEFAULT_EXTERNAL_TENANT_ID = "11111111-1111-4111-8111-111111111111"
ALLOWED_BUSINESS_TENANTS = {
    "AlphaCapital",
    "BetaWealth",
    "GammaFund",
    "DeltaEquity",
}
ALLOWED_ROLES = {"TenantAdmin", "PortfolioManager", "PortfolioViewer"}
ENTITLEMENT_CONTAINERS = {
    "memberships": "/userId",
    "roleAssignments": "/tenantId",
}


def load_seed_helper() -> ModuleType:
    helper_path = SCRIPT_DIR / "seed-data.py"
    spec = importlib.util.spec_from_file_location("_seed_data", helper_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load seed helper from {helper_path}.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_SEED = load_seed_helper()
SeedError = _SEED.SeedError


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--user-map",
        required=True,
        help="Safe JSON output from create-workforce-federated-users.py.",
    )
    parser.add_argument(
        "--resource-group",
        default=os.getenv("AZURE_RESOURCE_GROUP") or os.getenv("AZURE_RESOURCE_GROUP_NAME"),
        help="Azure resource group containing the control-plane Cosmos account.",
    )
    parser.add_argument(
        "--environment-name",
        default=os.getenv("AZURE_ENV_NAME"),
        help="azd/environment tag used to discover the resource group when --resource-group is omitted.",
    )
    parser.add_argument(
        "--resource-app-id",
        default=os.getenv("API_AUDIENCE", "api://contoso-asset-management"),
        help="Resource API audience stored on role assignments.",
    )
    parser.add_argument(
        "--workforce-tenant-id",
        default=DEFAULT_WORKFORCE_TENANT_ID,
        help="Expected workforce tenant ID.",
    )
    parser.add_argument(
        "--external-tenant-id",
        default=DEFAULT_EXTERNAL_TENANT_ID,
        help="Expected External ID tenant ID.",
    )
    parser.add_argument(
        "--workforce-tenant-domain",
        default=DEFAULT_WORKFORCE_TENANT_DOMAIN,
        help="Verified workforce tenant domain used by the federated identity issuer.",
    )
    parser.add_argument(
        "--disable-source-object-id",
        action="append",
        default=[],
        help="Disable only this mapped workforce object ID. Repeat for multiple users.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate locally and print intended upserts without Azure authentication or network access.",
    )
    return parser.parse_args()


def require_uuid(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SeedError(f"{context} must be a UUID.")
    try:
        return str(uuid.UUID(value.strip()))
    except ValueError as exc:
        raise SeedError(f"{context} must be a UUID.") from exc


def require_text(record: dict[str, Any], field: str, context: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        raise SeedError(f"{context}.{field} must be a non-empty string.")
    if value != value.strip():
        raise SeedError(f"{context}.{field} must not contain leading or trailing whitespace.")
    return value


def expected_federated_issuer(
    workforce_tenant_domain: str,
    external_tenant_id: str,
) -> str:
    return (
        f"https://login.microsoftonline.com/{workforce_tenant_domain}/v2.0"
        f"<{external_tenant_id}>"
    )


def load_user_map(
    path: str | Path,
    expected_workforce_tenant_id: str = DEFAULT_WORKFORCE_TENANT_ID,
    expected_external_tenant_id: str = DEFAULT_EXTERNAL_TENANT_ID,
    expected_workforce_tenant_domain: str = DEFAULT_WORKFORCE_TENANT_DOMAIN,
) -> list[dict[str, Any]]:
    workforce_tenant_id = require_uuid(
        expected_workforce_tenant_id, "--workforce-tenant-id"
    )
    external_tenant_id = require_uuid(
        expected_external_tenant_id, "--external-tenant-id"
    )
    if workforce_tenant_id == external_tenant_id:
        raise SeedError("Workforce and External ID tenant IDs must be different.")

    map_path = Path(path)
    try:
        document = json.loads(map_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SeedError(f"Unable to read user map {map_path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SeedError(f"User map {map_path} is not valid JSON: {exc}") from exc

    if not isinstance(document, dict):
        raise SeedError("--user-map must contain a JSON object.")
    root_workforce_id = require_uuid(document.get("workforceTenantId"), "workforceTenantId")
    root_external_id = require_uuid(document.get("externalTenantId"), "externalTenantId")
    if root_workforce_id != workforce_tenant_id:
        raise SeedError(
            f"workforceTenantId {root_workforce_id} does not match expected tenant "
            f"{workforce_tenant_id}."
        )
    if root_external_id != external_tenant_id:
        raise SeedError(
            f"externalTenantId {root_external_id} does not match expected tenant "
            f"{external_tenant_id}."
        )
    records = document.get("users")
    if not isinstance(records, list) or not records:
        raise SeedError("--user-map must contain a non-empty users array.")

    issuer = expected_federated_issuer(
        expected_workforce_tenant_domain.strip().lower(),
        external_tenant_id,
    )
    users: list[dict[str, Any]] = []
    seen_source_ids: set[str] = set()
    seen_external_ids: set[str] = set()
    for index, record in enumerate(records):
        context = f"users[{index}]"
        if not isinstance(record, dict):
            raise SeedError(f"{context} must be an object.")
        source_tenant_id = require_uuid(
            record.get("sourceTenantId"), f"{context}.sourceTenantId"
        )
        if source_tenant_id != workforce_tenant_id:
            raise SeedError(
                f"{context}.sourceTenantId {source_tenant_id} does not match "
                f"workforce tenant {workforce_tenant_id}."
            )
        source_object_id = require_uuid(
            record.get("sourceObjectId"), f"{context}.sourceObjectId"
        )
        external_object_id = require_uuid(
            record.get("externalIdObjectId"), f"{context}.externalIdObjectId"
        )
        if source_object_id in seen_source_ids:
            raise SeedError(f"Duplicate sourceObjectId {source_object_id} in user map.")
        if external_object_id in seen_external_ids:
            raise SeedError(
                f"Duplicate externalIdObjectId {external_object_id} in user map."
            )
        seen_source_ids.add(source_object_id)
        seen_external_ids.add(external_object_id)

        email = require_text(record, "email", context)
        if (
            email != email.lower()
            or any(character.isspace() for character in email)
            or email.count("@") != 1
            or email.startswith("@")
            or email.endswith("@")
        ):
            raise SeedError(f"{context}.email must be a normalized lowercase email address.")
        display_name = require_text(record, "displayName", context)
        tenant_id = require_text(record, "businessTenantId", context)
        if tenant_id not in ALLOWED_BUSINESS_TENANTS:
            allowed = ", ".join(sorted(ALLOWED_BUSINESS_TENANTS))
            raise SeedError(
                f"{context}.businessTenantId {tenant_id!r} is invalid; allowed values are: {allowed}."
            )
        status = require_text(record, "status", context)
        if status != "active":
            raise SeedError(f"{context}.status must be 'active' for provisioning input.")
        roles = record.get("roles")
        if (
            not isinstance(roles, list)
            or not roles
            or any(not isinstance(role, str) or not role.strip() for role in roles)
        ):
            raise SeedError(f"{context}.roles must be a non-empty array of strings.")
        normalized_roles = sorted(set(roles))
        if normalized_roles != roles:
            raise SeedError(f"{context}.roles must be unique and sorted.")
        unsupported = sorted(set(roles) - ALLOWED_ROLES)
        if unsupported:
            raise SeedError(
                f"{context}.roles contains unsupported roles: {', '.join(unsupported)}."
            )
        if record.get("federatedIssuer") != issuer:
            raise SeedError(
                f"{context}.federatedIssuer must exactly equal {issuer!r}."
            )
        if not isinstance(record.get("created"), bool):
            raise SeedError(f"{context}.created must be a boolean.")

        users.append(
            {
                "sourceTenantId": source_tenant_id,
                "sourceObjectId": source_object_id,
                "externalIdObjectId": external_object_id,
                "email": email,
                "displayName": display_name,
                "businessTenantId": tenant_id,
                "roles": roles,
                "status": status,
                "created": record["created"],
                "federatedIssuer": issuer,
            }
        )
    return users


def select_users(
    users: list[dict[str, Any]], disable_source_object_ids: list[str]
) -> tuple[list[dict[str, Any]], bool]:
    if not disable_source_object_ids:
        return users, False

    normalized_ids = [
        require_uuid(value, "--disable-source-object-id")
        for value in disable_source_object_ids
    ]
    if len(set(normalized_ids)) != len(normalized_ids):
        raise SeedError("--disable-source-object-id values must be unique.")
    users_by_source_id = {user["sourceObjectId"]: user for user in users}
    missing = sorted(set(normalized_ids) - users_by_source_id.keys())
    if missing:
        raise SeedError(
            "Every --disable-source-object-id must exist in --user-map; not found: "
            + ", ".join(missing)
        )
    selected_ids = set(normalized_ids)
    return [
        user for user in users if user["sourceObjectId"] in selected_ids
    ], True


def build_documents(
    user: dict[str, Any], resource_app_id: str, disabled: bool = False
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(resource_app_id, str) or not resource_app_id.strip():
        raise SeedError("--resource-app-id must be a non-empty string.")
    if resource_app_id != resource_app_id.strip():
        raise SeedError("--resource-app-id must not contain leading or trailing whitespace.")

    tenant_id = user["businessTenantId"]
    external_object_id = user["externalIdObjectId"]
    status = "inactive" if disabled else "active"
    stable_identity = {
        "userId": external_object_id,
        "sourceTenantId": user["sourceTenantId"],
        "sourceObjectId": user["sourceObjectId"],
        "externalIdObjectId": external_object_id,
        "identityProvider": "workforceFederation",
    }
    membership = {
        "id": f"membership-{tenant_id}-{external_object_id}",
        **stable_identity,
        "email": user["email"],
        "displayName": user["displayName"],
        "tenantId": tenant_id,
        "status": status,
        "documentType": "UserTenantMembership",
    }
    role_assignment = {
        "id": f"role-{tenant_id}-{external_object_id}",
        **stable_identity,
        "tenantId": tenant_id,
        "resourceAppId": resource_app_id,
        "roles": [] if disabled else list(user["roles"]),
        "status": status,
        "documentType": "RoleAssignment",
    }
    return membership, role_assignment


def print_dry_run(container: str, partition_key: str, document: dict[str, Any]) -> None:
    operation = {
        "database": _SEED.CONTROL_DATABASE,
        "container": container,
        "partitionKey": partition_key,
        "document": document,
    }
    print("UPSERT " + json.dumps(operation, sort_keys=True, separators=(",", ":")))


def main() -> int:
    args = parse_args()
    users = load_user_map(
        args.user_map,
        args.workforce_tenant_id,
        args.external_tenant_id,
        args.workforce_tenant_domain,
    )
    selected_users, disabled = select_users(users, args.disable_source_object_id)
    documents: list[tuple[str, str, dict[str, Any]]] = []
    for user in selected_users:
        membership, role_assignment = build_documents(
            user, args.resource_app_id, disabled
        )
        documents.extend(
            [
                ("memberships", membership["userId"], membership),
                ("roleAssignments", role_assignment["tenantId"], role_assignment),
            ]
        )

    if args.dry_run:
        mode = "disable" if disabled else "provision"
        print(
            f"Validated {len(users)} mapped user(s). Mode: {mode}; "
            f"selected {len(selected_users)} user(s)."
        )
        for container, partition_key, document in documents:
            print_dry_run(container, partition_key, document)
    else:
        resource_group = _SEED.discover_resource_group(args)
        control_account = _SEED.discover_account(resource_group, "control")
        _SEED.ensure_containers(
            resource_group,
            control_account["name"],
            _SEED.CONTROL_DATABASE,
            ENTITLEMENT_CONTAINERS,
            False,
        )
        token = _SEED.get_cosmos_token()
        for container, partition_key, document in documents:
            _SEED.upsert_document(
                control_account["endpoint"],
                _SEED.CONTROL_DATABASE,
                container,
                partition_key,
                document,
                token,
            )

    verb = "Planned" if args.dry_run else "Upserted"
    print(f"{verb} {len(documents)} federated entitlement document(s).")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (SeedError, RuntimeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
