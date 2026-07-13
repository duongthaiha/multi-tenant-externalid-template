#!/usr/bin/env python3
"""Seed Contoso Asset Management demo data with Azure CLI/AAD auth.

The script is dependency-free and idempotent: it creates expected SQL
containers if needed and upserts deterministic fixture documents.
"""

from __future__ import annotations

import argparse
import copy
import email.utils
import json
import os
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
CONTROL_DATABASE = "tenant-directory"
TENANT_DATABASE = "assets"
CONTROL_CONTAINERS = {
    "tenants": "/tenantId",
    "memberships": "/userId",
    "roleAssignments": "/tenantId",
    "tenantOnboardingState": "/tenantId",
}
TENANT_CONTAINERS = {
    "portfolios": "/tenantId",
    "positions": "/tenantId",
    "transactionApprovals": "/tenantId",
}
INITIAL_TENANTS = {"AlphaCapital", "BetaWealth", "GammaFund"}
ONBOARDING_TENANT = "DeltaEquity"


class SeedError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Seed control-plane and tenant Cosmos DB documents for the Contoso Asset Management POC."
    )
    parser.add_argument("--fixture", default=str(DEFAULT_FIXTURE), help="Path to the JSON seed fixture.")
    parser.add_argument("--resource-group", default=os.getenv("AZURE_RESOURCE_GROUP") or os.getenv("AZURE_RESOURCE_GROUP_NAME"), help="Azure resource group containing Cosmos accounts.")
    parser.add_argument("--environment-name", default=os.getenv("AZURE_ENV_NAME"), help="azd/environment tag used to discover the resource group when --resource-group is omitted.")
    parser.add_argument("--resource-app-id", default=os.getenv("API_AUDIENCE", "api://contoso-asset-management"), help="Resource API audience stored on role assignments.")
    parser.add_argument("--tenant", action="append", help="Tenant to seed. Repeat for multiple tenants. Defaults to initial tenants only.")
    parser.add_argument("--include-onboarding-tenant", action="store_true", help="Also seed DeltaEquity demo data after its infrastructure has been provisioned.")
    parser.add_argument("--dry-run", action="store_true", help="Validate fixtures and print intended Azure CLI/REST operations without writing data.")
    parser.add_argument("--user-id-map", help="Safe mapping JSON from create-external-id-demo-users.py; replaces fixture user IDs with External ID object IDs.")
    parser.add_argument("--tenant-identity-map", help="JSON map containing tenant Cosmos managed identity client/principal IDs.")
    parser.add_argument("--skip-container-create", action="store_true", help="Skip idempotent database/container create calls and only upsert documents.")
    return parser.parse_args()


def run_az(args: list[str], *, required: bool = True) -> str:
    command = ["az", *args, "--only-show-errors"]
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip() or "Azure CLI command failed"
        if required:
            raise SeedError(f"{' '.join(command)}\n{message}")
        return ""
    return completed.stdout.strip()


def load_fixture(path: str, user_id_map_path: str | None = None) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        fixture = json.load(handle)
    if user_id_map_path:
        fixture = apply_user_id_mapping(fixture, load_user_id_mapping(user_id_map_path))
    validate_fixture(fixture)
    return fixture


def load_user_id_mapping(path: str) -> dict[tuple[str, str], dict[str, str]]:
    with open(path, "r", encoding="utf-8") as handle:
        mapping_document = json.load(handle)

    mappings: dict[tuple[str, str], dict[str, str]] = {}
    entries = mapping_document.get("users")
    if not isinstance(entries, list):
        raise SeedError("--user-id-map must contain a users array.")
    for entry in entries:
        tenant_id = require_text(entry, "tenantId")
        fixture_user_id = require_text(entry, "fixtureUserId")
        external_id_object_id = require_text(entry, "externalIdObjectId")
        key = (tenant_id, fixture_user_id)
        if key in mappings and mappings[key]["externalIdObjectId"] != external_id_object_id:
            raise SeedError(f"Conflicting External ID mappings for {tenant_id}/{fixture_user_id}.")
        mappings[key] = {
            "externalIdObjectId": external_id_object_id,
            "email": entry.get("email", ""),
        }
    return mappings


def apply_user_id_mapping(fixture: dict[str, Any], mappings: dict[tuple[str, str], dict[str, str]]) -> dict[str, Any]:
    mapped = copy.deepcopy(fixture)
    applied = 0
    for tenant in mapped.get("tenants", []):
        tenant_id = tenant.get("tenantId")
        tenant_mapping: dict[str, str] = {}
        for user in tenant.get("users", []):
            original_user_id = user.get("userId")
            mapping = mappings.get((tenant_id, original_user_id))
            if not mapping:
                continue
            user["fixtureUserId"] = original_user_id
            user["userId"] = mapping["externalIdObjectId"]
            user["externalIdObjectId"] = mapping["externalIdObjectId"]
            tenant_mapping[original_user_id] = mapping["externalIdObjectId"]
            applied += 1
        for approval in tenant.get("transactionApprovals", []):
            requested_by = approval.get("requestedBy")
            if requested_by in tenant_mapping:
                approval["fixtureRequestedBy"] = requested_by
                approval["requestedBy"] = tenant_mapping[requested_by]

    if not applied:
        raise SeedError("--user-id-map did not match any fixture users.")
    return mapped


def load_tenant_identity_mapping(path: str | None) -> dict[str, dict[str, str]]:
    if not path:
        return {}
    with open(path, "r", encoding="utf-8") as handle:
        document = json.load(handle)
    entries = document.get("tenantCosmosIdentities") if isinstance(document, dict) else None
    if entries is None and isinstance(document, list):
        entries = document
    if not isinstance(entries, list):
        raise SeedError("--tenant-identity-map must be a list or contain tenantCosmosIdentities array.")
    mappings: dict[str, dict[str, str]] = {}
    for entry in entries:
        tenant_id = require_text(entry, "tenantId")
        client_id = require_text(entry, "clientId")
        mappings[tenant_id] = {
            "clientId": client_id,
            "principalId": entry.get("principalId", ""),
        }
    return mappings


def validate_fixture(fixture: dict[str, Any]) -> None:
    tenants = fixture.get("tenants")
    if not isinstance(tenants, list) or not tenants:
        raise SeedError("Fixture must contain a non-empty tenants array.")
    tenant_ids = {tenant.get("tenantId") for tenant in tenants}
    required = INITIAL_TENANTS | {ONBOARDING_TENANT}
    missing = sorted(required - tenant_ids)
    if missing:
        raise SeedError(f"Fixture is missing required tenants: {', '.join(missing)}")

    for tenant in tenants:
        tenant_id = require_text(tenant, "tenantId")
        users = tenant.get("users")
        if not isinstance(users, list) or len(users) < 2:
            raise SeedError(f"{tenant_id} must have at least two users.")
        role_sets = {tuple(user.get("roles", [])) for user in users}
        if len(role_sets) < 2:
            raise SeedError(f"{tenant_id} users must have different role assignments.")
        for user in users:
            require_text(user, "userId")
            email = require_text(user, "email")
            if not email.endswith("@example.com"):
                raise SeedError(f"{tenant_id} user email {email} must use reserved example.com demo domain.")
            if not user.get("roles"):
                raise SeedError(f"{tenant_id} user {user['userId']} needs at least one role.")

        portfolios = tenant.get("portfolios")
        if not isinstance(portfolios, list) or len(portfolios) < 2:
            raise SeedError(f"{tenant_id} must have at least two portfolios.")
        portfolio_ids = set()
        for portfolio in portfolios:
            portfolio_id = require_text(portfolio, "id")
            portfolio_ids.add(portfolio_id)
            positions = portfolio.get("positions")
            if not isinstance(positions, list) or len(positions) < 5:
                raise SeedError(f"{tenant_id}/{portfolio_id} must have at least five positions.")
        approvals = tenant.get("transactionApprovals")
        if not isinstance(approvals, list) or len(approvals) < 1:
            raise SeedError(f"{tenant_id} must have at least one pending transaction approval.")
        if not any(approval.get("status") == "Pending" for approval in approvals):
            raise SeedError(f"{tenant_id} must include a Pending transaction approval.")
        for approval in approvals:
            if approval.get("portfolioId") not in portfolio_ids:
                raise SeedError(f"{tenant_id} approval {approval.get('id')} references an unknown portfolio.")


def require_text(document: dict[str, Any], field: str) -> str:
    value = document.get(field)
    if not isinstance(value, str) or not value:
        raise SeedError(f"Document is missing required text field: {field}")
    return value


def select_tenants(fixture: dict[str, Any], args: argparse.Namespace) -> list[dict[str, Any]]:
    tenants = fixture["tenants"]
    if args.tenant:
        wanted = set(args.tenant)
    else:
        wanted = set(INITIAL_TENANTS)
        if args.include_onboarding_tenant:
            wanted.add(ONBOARDING_TENANT)
    selected = [tenant for tenant in tenants if tenant["tenantId"] in wanted]
    missing = sorted(wanted - {tenant["tenantId"] for tenant in selected})
    if missing:
        raise SeedError(f"Requested tenants are not in the fixture: {', '.join(missing)}")
    return selected


def discover_resource_group(args: argparse.Namespace) -> str:
    if args.resource_group:
        return args.resource_group
    if not args.environment_name:
        raise SeedError("Provide --resource-group or set AZURE_RESOURCE_GROUP/AZURE_ENV_NAME. Use --dry-run to validate fixtures without Azure access.")
    query = f"[?tags.application=='contoso-asset-management' && tags.environment=='{args.environment_name}'].name | [0]"
    group = run_az(["group", "list", "--query", query, "-o", "tsv"])
    if not group:
        raise SeedError(f"No resource group found for environment tag {args.environment_name}.")
    return group


def account_from_env(prefix: str, tenant_id: str | None = None) -> str | None:
    if tenant_id:
        normalized = ''.join(ch for ch in tenant_id.upper() if ch.isalnum())
        return os.getenv(f"{prefix}_{normalized}")
    return os.getenv(prefix)


def discover_account(resource_group: str, data_plane: str, tenant_id: str | None = None) -> dict[str, str]:
    env_name = account_from_env("CONTROL_PLANE_COSMOS_ACCOUNT_NAME") if data_plane == "control" else account_from_env("TENANT_COSMOS_ACCOUNT", tenant_id)
    if env_name:
        name = env_name
    else:
        if data_plane == "control":
            query = "[?tags.dataPlane=='control'].name | [0]"
        else:
            query = f"[?tags.dataPlane=='tenant' && tags.tenantId=='{tenant_id}'].name | [0]"
        name = run_az(["cosmosdb", "list", "-g", resource_group, "--query", query, "-o", "tsv"])
    if not name:
        label = "control-plane" if data_plane == "control" else f"tenant {tenant_id}"
        raise SeedError(f"Could not discover {label} Cosmos account in {resource_group}.")
    endpoint = run_az(["cosmosdb", "show", "-g", resource_group, "-n", name, "--query", "documentEndpoint", "-o", "tsv"])
    return {"name": name, "endpoint": endpoint}


def ensure_containers(resource_group: str, account_name: str, database: str, containers: dict[str, str], dry_run: bool) -> None:
    db_cmd = ["cosmosdb", "sql", "database", "create", "-g", resource_group, "-a", account_name, "-n", database]
    if dry_run:
        print_cmd(db_cmd)
    else:
        existing = run_az(["cosmosdb", "sql", "database", "show", "-g", resource_group, "-a", account_name, "-n", database, "--query", "name", "-o", "tsv"], required=False)
        if not existing:
            run_az(db_cmd)
    for container, partition_key in containers.items():
        cmd = [
            "cosmosdb", "sql", "container", "create",
            "-g", resource_group,
            "-a", account_name,
            "-d", database,
            "-n", container,
            "-p", partition_key,
        ]
        if dry_run:
            print_cmd(cmd)
        else:
            existing = run_az(["cosmosdb", "sql", "container", "show", "-g", resource_group, "-a", account_name, "-d", database, "-n", container, "--query", "name", "-o", "tsv"], required=False)
            if not existing:
                run_az(cmd)


def build_documents(
    tenant: dict[str, Any],
    tenant_endpoint: str,
    resource_app_id: str,
    tenant_identity: dict[str, str] | None = None) -> dict[tuple[str, str], list[dict[str, Any]]]:
    tenant_id = tenant["tenantId"]
    fixed_time = tenant.get("seededAt", "2026-01-15T00:00:00Z")
    control: dict[tuple[str, str], list[dict[str, Any]]] = {
        (CONTROL_DATABASE, "tenants"): [{
            "id": f"tenant-{tenant_id}",
            "tenantId": tenant_id,
            "displayName": tenant.get("displayName", tenant_id),
            "status": tenant.get("status", "active"),
            "region": tenant.get("region", "eastus"),
            "cosmosAccountEndpoint": tenant_endpoint,
            "databaseName": TENANT_DATABASE,
            "containerName": "portfolios",
            "containers": TENANT_CONTAINERS,
            "cosmosIdentityClientId": (tenant_identity or {}).get("clientId"),
            "cosmosIdentityPrincipalId": (tenant_identity or {}).get("principalId"),
            "documentType": "TenantDirectoryEntry",
        }],
        (CONTROL_DATABASE, "memberships"): [],
        (CONTROL_DATABASE, "roleAssignments"): [],
        (CONTROL_DATABASE, "tenantOnboardingState"): [{
            "id": f"onboarding-{tenant_id}",
            "tenantId": tenant_id,
            "provisioningStatus": tenant.get("provisioningStatus", "Completed"),
            "createdAt": fixed_time,
            "updatedAt": fixed_time,
            "documentType": "TenantOnboardingState",
        }],
    }
    for user in tenant["users"]:
        user_id = user["userId"]
        control[(CONTROL_DATABASE, "memberships")].append({
            "id": f"membership-{tenant_id}-{user_id}",
            "userId": user_id,
            "email": user["email"],
            "tenantId": tenant_id,
            "status": user.get("status", "active"),
            "displayName": user.get("displayName"),
            "fixtureUserId": user.get("fixtureUserId"),
            "externalIdObjectId": user.get("externalIdObjectId", user_id),
            "documentType": "UserTenantMembership",
        })
        control[(CONTROL_DATABASE, "roleAssignments")].append({
            "id": f"role-{tenant_id}-{user_id}",
            "userId": user_id,
            "tenantId": tenant_id,
            "roles": user["roles"],
            "resourceAppId": resource_app_id,
            "fixtureUserId": user.get("fixtureUserId"),
            "externalIdObjectId": user.get("externalIdObjectId", user_id),
            "documentType": "RoleAssignment",
        })

    tenant_docs: dict[tuple[str, str], list[dict[str, Any]]] = {
        (TENANT_DATABASE, "portfolios"): [],
        (TENANT_DATABASE, "positions"): [],
        (TENANT_DATABASE, "transactionApprovals"): [],
    }
    for portfolio in tenant["portfolios"]:
        portfolio_id = portfolio["id"]
        tenant_docs[(TENANT_DATABASE, "portfolios")].append({
            "id": portfolio_id,
            "tenantId": tenant_id,
            "name": portfolio["name"],
            "currency": portfolio.get("currency", "USD"),
            "marketValue": portfolio["marketValue"],
            "asOfDate": portfolio["asOfDate"],
            "documentType": "Portfolio",
        })
        for position in portfolio["positions"]:
            tenant_docs[(TENANT_DATABASE, "positions")].append({
                "id": position["id"],
                "tenantId": tenant_id,
                "portfolioId": portfolio_id,
                "instrumentName": position["instrumentName"],
                "assetClass": position["assetClass"],
                "quantity": position["quantity"],
                "marketValue": position["marketValue"],
                "documentType": "Position",
            })
    for approval in tenant["transactionApprovals"]:
        doc = dict(approval)
        doc.update({
            "tenantId": tenant_id,
            "approvedBy": approval.get("approvedBy"),
            "approvedAt": approval.get("approvedAt"),
            "documentType": "TransactionApproval",
        })
        tenant_docs[(TENANT_DATABASE, "transactionApprovals")].append(doc)
    control.update(tenant_docs)
    return control


def get_cosmos_token() -> str:
    token = run_az(["account", "get-access-token", "--resource", "https://cosmos.azure.com/", "--query", "accessToken", "-o", "tsv"])
    if not token:
        raise SeedError("Azure CLI did not return a Cosmos access token.")
    return token


def upsert_document(endpoint: str, database: str, container: str, partition_key: str, document: dict[str, Any], token: str) -> None:
    resource_path = f"dbs/{database}/colls/{container}/docs"
    url = endpoint.rstrip("/") + "/" + resource_path
    auth = urllib.parse.quote(f"type=aad&ver=1.0&sig={token}", safe="")
    date = email.utils.format_datetime(datetime.now(timezone.utc), usegmt=True)
    body = json.dumps(document, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST")
    request.add_header("Authorization", auth)
    request.add_header("Content-Type", "application/json")
    request.add_header("x-ms-date", date)
    request.add_header("x-ms-version", "2020-07-15")
    request.add_header("x-ms-documentdb-is-upsert", "True")
    request.add_header("x-ms-documentdb-partitionkey", json.dumps([partition_key]))
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            if response.status not in (200, 201):
                raise SeedError(f"Unexpected Cosmos response {response.status} for {container}/{document['id']}")
    except urllib.error.HTTPError as error:
        details = error.read().decode("utf-8", errors="replace")
        if error.code in (401, 403):
            details += "\nEnsure the current Azure CLI identity has Cosmos DB Built-in Data Contributor on the target accounts."
        raise SeedError(f"Failed to upsert {container}/{document.get('id')} into {database}: HTTP {error.code}\n{details}") from error
    except urllib.error.URLError as error:
        raise SeedError(f"Failed to reach Cosmos endpoint {endpoint}: {error.reason}") from error


def print_cmd(args: list[str]) -> None:
    print("az " + " ".join(urllib.parse.quote(part, safe='/-._:=') for part in args))


def print_dry_run(account_name: str, endpoint: str, database: str, container: str, document: dict[str, Any]) -> None:
    print(f"UPSERT account={account_name} endpoint={endpoint} db={database} container={container} partitionKey={document['tenantId'] if container != 'memberships' else document['userId']} id={document['id']}")


def main() -> int:
    args = parse_args()
    fixture = load_fixture(args.fixture, args.user_id_map)
    tenant_identity_mapping = load_tenant_identity_mapping(args.tenant_identity_map)
    selected = select_tenants(fixture, args)
    if args.dry_run:
        print(f"Validated {len(fixture['tenants'])} tenant fixtures. Selected: {', '.join(t['tenantId'] for t in selected)}")
        resource_group = args.resource_group or "<resource-group>"
        control_account = {"name": os.getenv("CONTROL_PLANE_COSMOS_ACCOUNT_NAME", "<control-plane-cosmos-account>"), "endpoint": "https://<control-plane-cosmos-account>.documents.azure.com:443/"}
    else:
        resource_group = discover_resource_group(args)
        control_account = discover_account(resource_group, "control")

    if not args.skip_container_create:
        ensure_containers(resource_group, control_account["name"], CONTROL_DATABASE, CONTROL_CONTAINERS, args.dry_run)

    token = None if args.dry_run else get_cosmos_token()
    total = 0
    for tenant in selected:
        tenant_id = tenant["tenantId"]
        if args.dry_run:
            tenant_account = {"name": account_from_env("TENANT_COSMOS_ACCOUNT", tenant_id) or f"<cosmos-account-for-{tenant_id}>", "endpoint": f"https://<cosmos-account-for-{tenant_id}>.documents.azure.com:443/"}
        else:
            tenant_account = discover_account(resource_group, "tenant", tenant_id)
        if not args.skip_container_create:
            ensure_containers(resource_group, tenant_account["name"], TENANT_DATABASE, TENANT_CONTAINERS, args.dry_run)

        documents_by_target = build_documents(
            tenant,
            tenant_account["endpoint"],
            args.resource_app_id,
            tenant_identity_mapping.get(tenant_id))
        for (database, container), documents in documents_by_target.items():
            account = control_account if database == CONTROL_DATABASE else tenant_account
            for document in documents:
                partition = document["userId"] if container == "memberships" else document["tenantId"]
                if args.dry_run:
                    print_dry_run(account["name"], account["endpoint"], database, container, document)
                else:
                    upsert_document(account["endpoint"], database, container, partition, document, token or "")
                total += 1
    verb = "Planned" if args.dry_run else "Upserted"
    print(f"{verb} {total} deterministic seed documents across {len(selected)} tenant(s).")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SeedError as error:
        print(f"seed-data: {error}", file=sys.stderr)
        raise SystemExit(1)
