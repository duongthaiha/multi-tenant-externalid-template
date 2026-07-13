#!/usr/bin/env python3
"""Onboard a Contoso Asset Management tenant with Bicep, RBAC, seed data, and validation."""

from __future__ import annotations

import argparse
import email.utils
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DEFAULT_FIXTURE = SCRIPT_DIR / "seed-data.fixtures.json"
TENANT_ONBOARDING_BICEP = REPO_ROOT / "infra" / "tenant-onboarding.bicep"
SEED_SCRIPT = SCRIPT_DIR / "seed-data.py"
DEFAULT_TENANT = "DeltaEquity"
CONTROL_DATABASE = "tenant-directory"
TENANT_DATABASE = "assets"
TENANT_CONTAINERS = ("portfolios", "positions", "transactionApprovals")
CONTROL_CONTAINERS = ("tenants", "memberships", "roleAssignments", "tenantOnboardingState")
COSMOS_DATA_CONTRIBUTOR = "00000000-0000-0000-0000-000000000002"


class OnboardingError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Provision and seed a new Contoso Asset Management business tenant. Defaults to DeltaEquity."
    )
    parser.add_argument("tenant", nargs="?", default=DEFAULT_TENANT, help="Business tenant ID to onboard.")
    parser.add_argument("--fixture", default=str(DEFAULT_FIXTURE), help="Seed fixture containing tenant directory, entitlement, and demo data.")
    parser.add_argument("--resource-group", default=os.getenv("AZURE_RESOURCE_GROUP") or os.getenv("AZURE_RESOURCE_GROUP_NAME"), help="Resource group containing the POC deployment.")
    parser.add_argument("--environment-name", default=os.getenv("AZURE_ENV_NAME"), help="Environment name used by the Bicep deployment.")
    parser.add_argument("--location", default=os.getenv("AZURE_LOCATION"), help="Azure region. Defaults to the resource-group location.")
    parser.add_argument("--resource-app-id", default=os.getenv("API_AUDIENCE", "api://contoso-asset-management"), help="Resource API audience stored on role assignments.")
    parser.add_argument("--seed-principal-id", default=os.getenv("SEED_PRINCIPAL_ID", ""), help="Optional principal object ID to grant tenant Cosmos Data Contributor for seeding.")
    parser.add_argument("--skip-provision", action="store_true", help="Skip Bicep provisioning and only run seed/validation against existing resources.")
    parser.add_argument("--skip-seed", action="store_true", help="Skip TenantDirectory, entitlement, and tenant data seeding.")
    parser.add_argument("--skip-validation", action="store_true", help="Skip post-onboarding validation checks.")
    parser.add_argument("--what-if", action="store_true", help="Run an Azure deployment what-if instead of creating resources.")
    parser.add_argument("--dry-run", action="store_true", help="Validate local inputs and print planned actions without Azure calls.")
    return parser.parse_args()


def run(command: list[str], *, required: bool = True, echo: bool = False) -> str:
    if echo:
        print_shell(command)
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip() or "command failed"
        if required:
            raise OnboardingError(f"{' '.join(command)}\n{message}")
        return ""
    return completed.stdout.strip()


def run_az(args: list[str], *, required: bool = True) -> str:
    return run(["az", *args, "--only-show-errors"], required=required)


def print_shell(command: list[str]) -> None:
    print(" ".join(urllib.parse.quote(part, safe="/-._:=") for part in command))


def validate_tenant_id(tenant: str) -> None:
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{1,39}", tenant):
        raise OnboardingError("Tenant must start with a letter and contain only letters, numbers, or hyphens (2-40 chars).")


def load_fixture(path: str, tenant: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        fixture = json.load(handle)
    tenants = fixture.get("tenants")
    if not isinstance(tenants, list):
        raise OnboardingError("Fixture must contain a tenants array.")
    for record in tenants:
        if record.get("tenantId") == tenant:
            return record
    raise OnboardingError(f"Fixture {path} does not contain tenant {tenant}.")


def discover_resource_group(args: argparse.Namespace) -> str:
    if args.resource_group:
        return args.resource_group
    if not args.environment_name:
        raise OnboardingError("Provide --resource-group or set AZURE_RESOURCE_GROUP/AZURE_ENV_NAME.")
    query = f"[?tags.application=='contoso-asset-management' && tags.environment=='{args.environment_name}'].name | [0]"
    group = run_az(["group", "list", "--query", query, "-o", "tsv"])
    if not group:
        raise OnboardingError(f"No resource group found for environment {args.environment_name}.")
    return group


def discover_location(resource_group: str, requested: str | None) -> str:
    if requested:
        return requested
    location = run_az(["group", "show", "-n", resource_group, "--query", "location", "-o", "tsv"])
    if not location:
        raise OnboardingError(f"Could not determine location for {resource_group}.")
    return location


def require_environment_name(args: argparse.Namespace) -> str:
    if args.environment_name:
        return args.environment_name
    raise OnboardingError("Set AZURE_ENV_NAME or pass --environment-name so tenant resources match the deployed foundation.")


def ensure_az_login() -> None:
    run_az(["account", "show", "--query", "id", "-o", "tsv"])


def deploy_tenant(args: argparse.Namespace, resource_group: str, location: str, environment_name: str) -> None:
    if args.skip_provision:
        print("Skipping Bicep provisioning (--skip-provision).")
        return
    if not TENANT_ONBOARDING_BICEP.exists():
        raise OnboardingError(f"Missing {TENANT_ONBOARDING_BICEP}")
    deployment_name = f"tenant-onboarding-{args.tenant.lower()}"
    command = [
        "az", "deployment", "group", "what-if" if args.what_if else "create",
        "--resource-group", resource_group,
        "--name", deployment_name,
        "--template-file", str(TENANT_ONBOARDING_BICEP),
        "--parameters",
        f"environmentName={environment_name}",
        f"location={location}",
        f"tenantName={args.tenant}",
    ]
    if args.seed_principal_id:
        command.append(f"seedPrincipalId={args.seed_principal_id}")
    if not args.what_if:
        command.extend(["--query", "properties.outputs", "-o", "json"])
    print(f"Provisioning tenant infrastructure for {args.tenant}...")
    print(run(command, echo=True))


def seed_tenant(args: argparse.Namespace, resource_group: str) -> None:
    if args.skip_seed:
        print("Skipping seed data (--skip-seed).")
        return
    command = [
        sys.executable, str(SEED_SCRIPT),
        "--fixture", args.fixture,
        "--tenant", args.tenant,
        "--resource-group", resource_group,
        "--resource-app-id", args.resource_app_id,
    ]
    print(f"Seeding TenantDirectory, entitlements, and demo data for {args.tenant}...")
    print(run(command, echo=True))


def discover_account(resource_group: str, data_plane: str, tenant: str | None = None) -> dict[str, Any]:
    if data_plane == "control":
        query = "[?tags.dataPlane=='control'].name | [0]"
    else:
        query = f"[?tags.dataPlane=='tenant' && tags.tenantId=='{tenant}'].name | [0]"
    name = run_az(["cosmosdb", "list", "-g", resource_group, "--query", query, "-o", "tsv"])
    if not name:
        label = "control-plane" if data_plane == "control" else f"tenant {tenant}"
        raise OnboardingError(f"Could not discover {label} Cosmos account in {resource_group}.")
    details = json.loads(run_az([
        "cosmosdb", "show", "-g", resource_group, "-n", name,
        "--query", "{id:id,name:name,endpoint:documentEndpoint,disableLocalAuth:disableLocalAuth,publicNetworkAccess:publicNetworkAccess}",
        "-o", "json",
    ]))
    return details


def assert_secure_cosmos(account: dict[str, Any]) -> None:
    if account.get("disableLocalAuth") is not True:
        raise OnboardingError(f"Cosmos account {account['name']} must have disableLocalAuth=true.")
    if account.get("publicNetworkAccess") != "Disabled":
        raise OnboardingError(f"Cosmos account {account['name']} must have publicNetworkAccess Disabled.")


def wait_for_tenant_account(resource_group: str, tenant: str) -> dict[str, Any]:
    deadline = time.time() + 300
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            return discover_account(resource_group, "tenant", tenant)
        except Exception as error:  # retry Azure eventual consistency
            last_error = error
            time.sleep(10)
    raise OnboardingError(f"Tenant Cosmos account was not discoverable after provisioning: {last_error}")


def validate_private_endpoint(resource_group: str, account: dict[str, Any]) -> None:
    endpoints = json.loads(run_az(["network", "private-endpoint", "list", "-g", resource_group, "-o", "json"]))
    matched = None
    for endpoint in endpoints:
        for connection in endpoint.get("privateLinkServiceConnections", []):
            if connection.get("privateLinkServiceId") == account["id"]:
                matched = endpoint
                break
        if matched:
            break
    if not matched:
        raise OnboardingError(f"No private endpoint found for Cosmos account {account['name']}.")
    groups = json.loads(run_az([
        "network", "private-endpoint", "dns-zone-group", "list",
        "-g", resource_group,
        "--endpoint-name", matched["name"],
        "-o", "json",
    ]))
    if not groups:
        raise OnboardingError(f"Private endpoint {matched['name']} has no private DNS zone group.")


def validate_cosmos_role(resource_group: str, environment_name: str, account_name: str) -> None:
    principal_id = run_az([
        "containerapp", "show", "-g", resource_group, "-n", f"ca-{environment_name}-backend-api",
        "--query", "identity.principalId", "-o", "tsv",
    ])
    assignments = json.loads(run_az(["cosmosdb", "sql", "role", "assignment", "list", "-g", resource_group, "-a", account_name, "-o", "json"]))
    for assignment in assignments:
        if assignment.get("principalId") == principal_id and assignment.get("roleDefinitionId", "").endswith(COSMOS_DATA_CONTRIBUTOR):
            return
    raise OnboardingError(f"Backend API managed identity lacks Cosmos DB SQL Data Contributor on {account_name}.")


def validate_containers(resource_group: str, account_name: str, database: str, containers: tuple[str, ...]) -> None:
    run_az(["cosmosdb", "sql", "database", "show", "-g", resource_group, "-a", account_name, "-n", database, "--query", "name", "-o", "tsv"])
    for container in containers:
        run_az(["cosmosdb", "sql", "container", "show", "-g", resource_group, "-a", account_name, "-d", database, "-n", container, "--query", "name", "-o", "tsv"])


def get_cosmos_token() -> str:
    token = run_az(["account", "get-access-token", "--resource", "https://cosmos.azure.com/", "--query", "accessToken", "-o", "tsv"])
    if not token:
        raise OnboardingError("Azure CLI did not return a Cosmos access token.")
    return token


def read_document(endpoint: str, database: str, container: str, doc_id: str, partition_key: str, token: str) -> dict[str, Any]:
    resource_path = f"dbs/{database}/colls/{container}/docs/{doc_id}"
    url = endpoint.rstrip("/") + "/" + resource_path
    auth = urllib.parse.quote(f"type=aad&ver=1.0&sig={token}", safe="")
    date = email.utils.format_datetime(datetime.now(timezone.utc), usegmt=True)
    request = urllib.request.Request(url, method="GET")
    request.add_header("Authorization", auth)
    request.add_header("x-ms-date", date)
    request.add_header("x-ms-version", "2020-07-15")
    request.add_header("x-ms-documentdb-partitionkey", json.dumps([partition_key]))
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        details = error.read().decode("utf-8", errors="replace")
        raise OnboardingError(f"Failed to read {container}/{doc_id}: HTTP {error.code}\n{details}") from error
    except urllib.error.URLError as error:
        raise OnboardingError(f"Failed to reach Cosmos endpoint {endpoint}: {error.reason}") from error


def validate_seed_records(control: dict[str, Any], tenant_account: dict[str, Any], tenant_fixture: dict[str, Any]) -> None:
    tenant = tenant_fixture["tenantId"]
    admin_user = tenant_fixture["users"][0]["userId"]
    portfolio_id = tenant_fixture["portfolios"][0]["id"]
    token = get_cosmos_token()
    directory = read_document(control["endpoint"], CONTROL_DATABASE, "tenants", f"tenant-{tenant}", tenant, token)
    if directory.get("cosmosAccountEndpoint") != tenant_account["endpoint"]:
        raise OnboardingError("TenantDirectory endpoint does not match the onboarded tenant Cosmos endpoint.")
    role = read_document(control["endpoint"], CONTROL_DATABASE, "roleAssignments", f"role-{tenant}-{admin_user}", tenant, token)
    if not role.get("roles"):
        raise OnboardingError(f"Role assignment for {admin_user} has no roles.")
    portfolio = read_document(tenant_account["endpoint"], TENANT_DATABASE, "portfolios", portfolio_id, tenant, token)
    if portfolio.get("tenantId") != tenant:
        raise OnboardingError(f"Seeded portfolio {portfolio_id} is not bound to {tenant}.")


def validate_onboarding(args: argparse.Namespace, resource_group: str, environment_name: str, tenant_fixture: dict[str, Any]) -> None:
    if args.skip_validation:
        print("Skipping validation (--skip-validation).")
        return
    print(f"Validating onboarding for {args.tenant}...")
    tenant_account = wait_for_tenant_account(resource_group, args.tenant)
    control_account = discover_account(resource_group, "control")
    assert_secure_cosmos(tenant_account)
    validate_private_endpoint(resource_group, tenant_account)
    validate_cosmos_role(resource_group, environment_name, tenant_account["name"])
    validate_containers(resource_group, tenant_account["name"], TENANT_DATABASE, TENANT_CONTAINERS)
    validate_containers(resource_group, control_account["name"], CONTROL_DATABASE, CONTROL_CONTAINERS)
    if not args.skip_seed:
        validate_seed_records(control_account, tenant_account, tenant_fixture)
    print(f"Validated {args.tenant}: secure Cosmos, private endpoint/DNS, backend RBAC, containers, directory, entitlements, and seed data.")


def dry_run(args: argparse.Namespace, tenant_fixture: dict[str, Any]) -> int:
    print(f"Validated fixture for {tenant_fixture['tenantId']} from {args.fixture}.")
    print(f"Would deploy {TENANT_ONBOARDING_BICEP} for tenant {args.tenant}.")
    seed_command = [sys.executable, str(SEED_SCRIPT), "--fixture", args.fixture, "--tenant", args.tenant, "--dry-run"]
    print(run(seed_command, echo=True))
    return 0


def main() -> int:
    args = parse_args()
    validate_tenant_id(args.tenant)
    tenant_fixture = load_fixture(args.fixture, args.tenant)
    if args.dry_run:
        return dry_run(args, tenant_fixture)
    ensure_az_login()
    environment_name = require_environment_name(args)
    resource_group = discover_resource_group(args)
    location = discover_location(resource_group, args.location)
    deploy_tenant(args, resource_group, location, environment_name)
    if args.what_if:
        return 0
    seed_tenant(args, resource_group)
    validate_onboarding(args, resource_group, environment_name, tenant_fixture)
    print(f"Tenant onboarding complete for {args.tenant}.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except OnboardingError as error:
        print(f"new-tenant: {error}", file=sys.stderr)
        raise SystemExit(1)
