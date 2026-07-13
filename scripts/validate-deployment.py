#!/usr/bin/env python3
"""Read-only deployment validation for the Contoso Asset Management POC."""

from __future__ import annotations

import argparse
import base64
import ipaddress
import json
import os
import re
import socket
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_FIXTURE = SCRIPT_DIR / "seed-data.fixtures.json"
DEFAULT_TENANT = "AlphaCapital"
DEFAULT_CROSS_TENANT = "BetaWealth"
CORRELATION_HEADER = "X-Correlation-ID"


class ValidationError(RuntimeError):
    pass


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate health, token claims, tenant isolation, backend auth boundaries, Cosmos private networking, "
            "Cosmos local-auth posture, and tenant-mismatch alerting. Live checks are read-only and require --live."
        )
    )
    parser.add_argument("--live", action="store_true", help="Run live Azure and HTTP checks. Without this flag, only local configuration is validated and planned checks are printed.")
    parser.add_argument("--fixture", default=str(DEFAULT_FIXTURE), help="Seed fixture used to choose default tenant, portfolio, position, and approval IDs.")
    parser.add_argument("--resource-group", default=os.getenv("AZURE_RESOURCE_GROUP") or os.getenv("AZURE_RESOURCE_GROUP_NAME"), help="Resource group containing the POC deployment.")
    parser.add_argument("--environment-name", default=os.getenv("AZURE_ENV_NAME"), help="Environment tag/name used to discover resources when explicit names are omitted.")
    parser.add_argument("--apim-url", default=os.getenv("APIM_GATEWAY_URL"), help="APIM gateway URL, for example https://apim-name.azure-api.net.")
    parser.add_argument("--frontend-url", default=os.getenv("FRONTEND_API_URL"), help="External frontend API URL.")
    parser.add_argument("--backend-fqdn", default=os.getenv("BACKEND_API_FQDN"), help="Internal backend Container App FQDN.")
    parser.add_argument("--frontend-container-app", default=os.getenv("FRONTEND_CONTAINER_APP_NAME"), help="Frontend Container App name used for app-host DNS/backend checks.")
    parser.add_argument("--tenant", default=os.getenv("VALIDATION_TENANT_ID", DEFAULT_TENANT), help="Tenant expected in the validation token.")
    parser.add_argument("--cross-tenant", default=os.getenv("VALIDATION_CROSS_TENANT_ID", DEFAULT_CROSS_TENANT), help="Different tenant used to prove cross-tenant 403 behavior.")
    parser.add_argument("--access-token", default=os.getenv("VALIDATION_ACCESS_TOKEN"), help="User access token for the tenant. The script never prints or stores it.")
    parser.add_argument("--expected-role", action="append", default=[], help="Expected role claim. Repeat to require multiple roles.")
    parser.add_argument("--expected-issuer", default=os.getenv("EXTERNAL_ID_ISSUER"), help="Exact External ID issuer expected in the user access token.")
    parser.add_argument("--expected-audience", default=os.getenv("API_AUDIENCE"), help="Exact frontend API audience expected in the user access token.")
    parser.add_argument("--expected-idp", help="Optional exact upstream identity-provider claim expected for a workforce-federated user.")
    parser.add_argument("--skip-app-host-checks", action="store_true", help="Skip checks that require az containerapp exec from the frontend app host.")
    parser.add_argument("--skip-public-cosmos-check", action="store_true", help="Skip public-route Cosmos network failure probes.")
    parser.add_argument(
        "--validate-jumpbox",
        action="store_true",
        default=os.getenv("ENABLE_JUMPBOX", "").lower() == "true",
        help="Validate the optional Bastion jumpbox, its RBAC, and Cosmos private connectivity.",
    )
    parser.add_argument(
        "--jumpbox-only",
        action="store_true",
        help="Run only live jumpbox and Cosmos posture checks; does not require an API access token.",
    )
    parser.add_argument(
        "--jumpbox-user-principal-id",
        default=os.getenv("JUMPBOX_USER_PRINCIPAL_ID"),
        help="Operator object ID expected to have VM login and Cosmos Data Reader.",
    )
    parser.add_argument(
        "--jumpbox-vm-name",
        default=os.getenv("JUMPBOX_VM_NAME"),
        help="Jumpbox VM name. Defaults to vm-<environment-name>-jumpbox.",
    )
    return parser.parse_args()


def run(command: list[str], *, required: bool = True) -> str:
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip() or "command failed"
        if required:
            raise ValidationError(f"{' '.join(redact_command(command))}\n{message}")
        return ""
    return completed.stdout.strip()


def redact_command(command: list[str]) -> list[str]:
    return ["<redacted-token>" if looks_like_jwt(part) else part for part in command]


def run_az(args: list[str], *, required: bool = True) -> str:
    return run(["az", *args, "--only-show-errors"], required=required)


def add_result(results: list[Check], name: str, detail: str = "ok") -> None:
    results.append(Check(name, True, detail))
    print(f"PASS {name}: {detail}")


def fail(name: str, detail: str) -> None:
    raise ValidationError(f"{name}: {detail}")


def discover_resource_group(args: argparse.Namespace) -> str:
    if args.resource_group:
        return args.resource_group
    if not args.environment_name:
        fail("config", "Provide --resource-group or set AZURE_RESOURCE_GROUP/AZURE_ENV_NAME.")
    query = f"[?tags.application=='contoso-asset-management' && tags.environment=='{args.environment_name}'].name | [0]"
    group = run_az(["group", "list", "--query", query, "-o", "tsv"])
    if not group:
        fail("resource-group", f"No resource group found for environment {args.environment_name}.")
    return group


def discover_urls(args: argparse.Namespace, resource_group: str) -> tuple[str, str, str, str]:
    apim_url = args.apim_url
    if not apim_url:
        apim_url = run_az(["apim", "list", "-g", resource_group, "--query", "[0].gatewayUrl", "-o", "tsv"], required=False)
    frontend_app = args.frontend_container_app or (f"ca-{args.environment_name}-frontend-api" if args.environment_name else "")
    frontend_url = args.frontend_url
    if not frontend_url and frontend_app:
        fqdn = run_az(["containerapp", "show", "-g", resource_group, "-n", frontend_app, "--query", "properties.configuration.ingress.fqdn", "-o", "tsv"], required=False)
        frontend_url = f"https://{fqdn}" if fqdn else ""
    backend_fqdn = args.backend_fqdn
    if not backend_fqdn and args.environment_name:
        backend_app = f"ca-{args.environment_name}-backend-api"
        backend_fqdn = run_az(["containerapp", "show", "-g", resource_group, "-n", backend_app, "--query", "properties.configuration.ingress.fqdn", "-o", "tsv"], required=False)
    return require_url(apim_url, "APIM gateway URL"), require_url(frontend_url, "frontend API URL"), backend_fqdn, frontend_app


def require_url(value: str | None, label: str) -> str:
    if not value:
        fail("config", f"Missing {label}.")
    return value.rstrip("/")


def load_fixture(path: str, tenant: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        fixture = json.load(handle)
    for record in fixture.get("tenants", []):
        if record.get("tenantId") == tenant:
            return record
    fail("fixture", f"Tenant {tenant} not found in {path}.")


def fixture_paths(tenant: dict[str, Any]) -> tuple[str, str, str]:
    portfolio = tenant["portfolios"][0]
    position = portfolio["positions"][0]
    approval = tenant["transactionApprovals"][0]
    return portfolio["id"], position["id"], approval["id"]


def http_status(url: str, *, token: str | None = None, method: str = "GET", timeout: int = 20) -> tuple[int | None, str]:
    headers = {CORRELATION_HEADER: "validate-deployment"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(4096).decode("utf-8", errors="replace")
            return response.status, body
    except urllib.error.HTTPError as error:
        return error.code, error.read(4096).decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, socket.timeout) as error:
        return None, str(error)


def assert_http(name: str, url: str, expected: set[int], *, token: str | None = None, method: str = "GET") -> str:
    status, body = http_status(url, token=token, method=method)
    if status not in expected:
        fail(name, f"Expected HTTP {sorted(expected)}, got {status}; body={body[:200]}")
    return f"HTTP {status}"


def looks_like_jwt(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", value or ""))


def decode_jwt_payload(token: str) -> dict[str, Any]:
    if not looks_like_jwt(token):
        fail("token-claims", "Validation token is not a compact JWT.")
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
    except Exception as error:
        fail("token-claims", f"Could not decode JWT payload: {error}")


def claim_values(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str):
        return value.split()
    return []


def validate_token_claims(
    token: str,
    tenant: str,
    expected_roles: list[str],
    expected_issuer: str | None = None,
    expected_audience: str | None = None,
    expected_idp: str | None = None,
) -> str:
    claims = decode_jwt_payload(token)
    if expected_issuer and claims.get("iss") != expected_issuer:
        fail("token-claims", "iss did not match the expected External ID issuer.")
    audience = claims.get("aud")
    if expected_audience and (
        expected_audience not in audience if isinstance(audience, list) else audience != expected_audience
    ):
        fail("token-claims", "aud did not match the expected frontend API audience.")
    if expected_idp and claims.get("idp") != expected_idp:
        fail("token-claims", "idp did not match the expected upstream identity provider.")
    if claims.get("extension_tenantId") != tenant:
        fail("token-claims", f"extension_tenantId did not match {tenant}.")
    if claims.get("tenant_status") != "active":
        fail("token-claims", "tenant_status must be active.")
    roles = claim_values(claims.get("tenant_roles"))
    if not roles:
        fail("token-claims", "tenant_roles claim is missing or empty.")
    missing_roles = sorted(set(expected_roles) - set(roles))
    if missing_roles:
        fail("token-claims", f"Missing expected roles: {', '.join(missing_roles)}")
    if not claim_values(claims.get("scp")):
        fail("token-claims", "scp claim is missing or empty.")
    return f"tenant={tenant}, roles={len(roles)}, scopes={len(claim_values(claims.get('scp')))}"


def list_cosmos_accounts(resource_group: str) -> list[dict[str, Any]]:
    output = run_az([
        "cosmosdb", "list", "-g", resource_group,
        "--query", "[].{name:name,id:id,endpoint:documentEndpoint,disableLocalAuth:disableLocalAuth,publicNetworkAccess:publicNetworkAccess,tags:tags}",
        "-o", "json",
    ])
    accounts = json.loads(output or "[]")
    if not accounts:
        fail("cosmos-discovery", f"No Cosmos DB accounts found in {resource_group}.")
    return accounts


def validate_cosmos_security(accounts: list[dict[str, Any]]) -> str:
    failures: list[str] = []
    for account in accounts:
        if account.get("disableLocalAuth") is not True:
            failures.append(f"{account['name']}:disableLocalAuth={account.get('disableLocalAuth')}")
        if account.get("publicNetworkAccess") != "Disabled":
            failures.append(f"{account['name']}:publicNetworkAccess={account.get('publicNetworkAccess')}")
    if failures:
        fail("cosmos-security", "; ".join(failures))
    return f"{len(accounts)} account(s) disableLocalAuth=true and publicNetworkAccess=Disabled"


def host_from_endpoint(endpoint: str) -> str:
    parsed = urllib.parse.urlparse(endpoint)
    return parsed.hostname or endpoint.replace("https://", "").strip("/").split(":", 1)[0]


def exec_in_frontend(resource_group: str, app_name: str, command: str, *, required: bool = True) -> str:
    if not app_name:
        fail("app-host", "Missing frontend Container App name.")
    return run_az(["containerapp", "exec", "-g", resource_group, "-n", app_name, "--command", command], required=required)


def validate_backend_from_app_host(resource_group: str, app_name: str, backend_fqdn: str, tenant: str) -> str:
    if not backend_fqdn:
        fail("backend-health", "Missing backend FQDN.")
    health_cmd = shell_http_status(f"https://{backend_fqdn}/health")
    health = exec_in_frontend(resource_group, app_name, health_cmd)
    if "HTTP_STATUS=200" not in health:
        fail("backend-health", f"Expected backend /health HTTP 200 from app host, got: {health[-300:]}")
    unauth_cmd = shell_http_status(f"https://{backend_fqdn}/internal/tenants/{urllib.parse.quote(tenant)}/portfolios")
    unauth = exec_in_frontend(resource_group, app_name, unauth_cmd)
    if "HTTP_STATUS=401" not in unauth and "HTTP_STATUS=403" not in unauth:
        fail("backend-unauthorized", f"Expected unauthorized direct backend call to fail with 401/403, got: {unauth[-300:]}")
    return "backend health=200 and unauthenticated internal call rejected"


def shell_http_status(url: str) -> str:
    safe_url = urllib.parse.quote(url, safe=":/-._~?=&%")
    return (
        "sh -c 'if command -v curl >/dev/null 2>&1; then "
        f"code=$(curl -k -sS -o /dev/null -w %{{http_code}} {safe_url}); "
        "elif command -v wget >/dev/null 2>&1; then "
        f"wget --no-check-certificate -q --server-response -O - {safe_url} 2>&1 | awk '\''/HTTP\\//{{code=$2}} END{{printf \"%s\", code}}'\''; "
        "else echo MISSING_HTTP_CLIENT; exit 3; fi; echo HTTP_STATUS=${code:-000}'"
    )


def validate_private_dns(resource_group: str, app_name: str, accounts: list[dict[str, Any]]) -> str:
    for account in accounts:
        host = host_from_endpoint(account["endpoint"])
        command = (
            "sh -c 'if command -v getent >/dev/null 2>&1; then getent hosts " + host +
            "; elif command -v nslookup >/dev/null 2>&1; then nslookup " + host +
            "; else echo MISSING_DNS_TOOL; exit 3; fi'"
        )
        output = exec_in_frontend(resource_group, app_name, command)
        ips = re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", output)
        private_ips = [ip for ip in ips if ipaddress.ip_address(ip).is_private]
        if not private_ips:
            fail("cosmos-private-dns", f"{account['name']} did not resolve to a private IP from app host. Output: {output[-300:]}")
    return f"{len(accounts)} account hostnames resolve to private IPs from frontend app host"


def validate_public_cosmos_failure(accounts: list[dict[str, Any]]) -> str:
    for account in accounts:
        status, body = http_status(account["endpoint"], timeout=10)
        if status == 401:
            fail("cosmos-public-access", f"{account['name']} reached public Cosmos data plane and returned 401 instead of network/public-access denial.")
        if status not in {None, 403, 404}:
            fail("cosmos-public-access", f"{account['name']} expected public-route failure/403, got HTTP {status}: {body[:160]}")
    return f"{len(accounts)} account(s) rejected or blocked public-route probe"


def validate_alert(resource_group: str) -> str:
    output = run_az([
        "resource", "list", "-g", resource_group,
        "--resource-type", "Microsoft.Insights/scheduledQueryRules",
        "--query", "[?contains(name, 'tenant-mismatch')].[name,properties]",
        "-o", "json",
    ])
    rules = json.loads(output or "[]")
    if not rules:
        fail("tenant-mismatch-alert", "No scheduled query rule containing tenant-mismatch was found.")
    for name, properties in rules:
        criteria = properties.get("criteria", {}).get("allOf", [])
        for item in criteria:
            query = item.get("query", "")
            if item.get("operator") == "GreaterThan" and int(item.get("threshold", -1)) == 5 and "tenant-mismatch" in query and properties.get("windowSize") == "PT5M":
                return f"{name} threshold >5 in PT5M"
    fail("tenant-mismatch-alert", "Rule exists but does not match >5 tenant-mismatch 403s in 5 minutes.")


def validate_jumpbox_resources(resource_group: str, vm_name: str, principal_id: str) -> str:
    bastions = json.loads(
        run_az(
            [
                "network",
                "bastion",
                "list",
                "-g",
                resource_group,
                "--query",
                "[].{name:name,sku:sku.name,state:provisioningState,id:id,subnetId:ipConfigurations[0].subnet.id}",
                "-o",
                "json",
            ]
        )
        or "[]"
    )
    if not bastions or bastions[0].get("sku") not in {"Basic", "Standard", "Premium"}:
        fail("jumpbox-bastion", f"Expected a Basic-or-higher Bastion host, found {bastions}.")
    if bastions[0].get("state") != "Succeeded":
        fail("jumpbox-bastion", f"Bastion provisioning state is {bastions[0].get('state')}.")
    bastion_subnet_prefix = run_az(
        [
            "network",
            "vnet",
            "subnet",
            "show",
            "--ids",
            bastions[0]["subnetId"],
            "--query",
            "addressPrefix",
            "-o",
            "tsv",
        ]
    )

    vm = json.loads(
        run_az(
            [
                "vm",
                "show",
                "-g",
                resource_group,
                "-n",
                vm_name,
                "--query",
                "{id:id,identity:identity.type,nicId:networkProfile.networkInterfaces[0].id}",
                "-o",
                "json",
            ]
        )
    )
    if "SystemAssigned" not in (vm.get("identity") or ""):
        fail("jumpbox-identity", "Jumpbox VM does not have a system-assigned managed identity.")

    extension_state = run_az(
        [
            "vm",
            "extension",
            "show",
            "-g",
            resource_group,
            "--vm-name",
            vm_name,
            "-n",
            "AADLoginForWindows",
            "--query",
            "provisioningState",
            "-o",
            "tsv",
        ]
    )
    if extension_state != "Succeeded":
        fail("jumpbox-entra-login", f"AADLoginForWindows state is {extension_state or 'missing'}.")

    login_role = run_az(
        [
            "role",
            "assignment",
            "list",
            "--assignee-object-id",
            principal_id,
            "--scope",
            vm["id"],
            "--query",
            "[?roleDefinitionName=='Virtual Machine User Login'].id | [0]",
            "-o",
            "tsv",
        ]
    )
    if not login_role:
        fail("jumpbox-vm-login-rbac", f"{principal_id} lacks Virtual Machine User Login on {vm_name}.")

    schedule_state = run_az(
        [
            "resource",
            "show",
            "-g",
            resource_group,
            "-n",
            f"shutdown-computevm-{vm_name}",
            "--resource-type",
            "Microsoft.DevTestLab/schedules",
            "--api-version",
            "2018-09-15",
            "--query",
            "properties.status",
            "-o",
            "tsv",
        ]
    )
    if schedule_state != "Enabled":
        fail("jumpbox-auto-shutdown", f"Auto-shutdown status is {schedule_state or 'missing'}.")

    nic = json.loads(
        run_az(
            [
                "network",
                "nic",
                "show",
                "--ids",
                vm["nicId"],
                "--query",
                "{nsgId:networkSecurityGroup.id,publicIpId:ipConfigurations[0].publicIPAddress.id}",
                "-o",
                "json",
            ]
        )
    )
    if not nic.get("publicIpId"):
        fail("jumpbox-egress", "Jumpbox NIC does not have the selected outbound public IP.")

    reader_scopes = {
        "Bastion": bastions[0]["id"],
        "VM": vm["id"],
        "NIC": vm["nicId"],
    }
    missing_reader_scopes: list[str] = []
    for label, scope in reader_scopes.items():
        assignment = run_az(
            [
                "role",
                "assignment",
                "list",
                "--assignee-object-id",
                principal_id,
                "--scope",
                scope,
                "--query",
                "[?roleDefinitionName=='Reader'].id | [0]",
                "-o",
                "tsv",
            ]
        )
        if not assignment:
            missing_reader_scopes.append(label)
    if missing_reader_scopes:
        fail(
            "jumpbox-reader-rbac",
            f"{principal_id} lacks explicit Reader on: {', '.join(missing_reader_scopes)}.",
        )

    rules = json.loads(
        run_az(
            [
                "network",
                "nsg",
                "show",
                "--ids",
                nic["nsgId"],
                "--query",
                "securityRules",
                "-o",
                "json",
            ]
        )
        or "[]"
    )
    has_bastion_rdp = False
    has_deny_all_other_inbound = False
    for rule in rules:
        properties = rule.get("properties", rule)
        if properties.get("direction") != "Inbound":
            continue
        sources = [properties.get("sourceAddressPrefix", "")]
        sources.extend(properties.get("sourceAddressPrefixes") or [])
        ports = [properties.get("destinationPortRange", "")]
        ports.extend(properties.get("destinationPortRanges") or [])
        if (
            properties.get("access") == "Deny"
            and "*" in sources
            and "*" in ports
            and int(properties.get("priority", 65535)) > 100
        ):
            has_deny_all_other_inbound = True
        if properties.get("access") != "Allow":
            continue
        if bastion_subnet_prefix in sources and ("3389" in ports or "*" in ports):
            has_bastion_rdp = True
        if any(port in {"*", "3389"} for port in ports):
            for source in sources:
                if source in {"*", "Internet"}:
                    fail("jumpbox-nsg", f"Public inbound RDP is allowed by NSG rule {rule.get('name')}.")
                try:
                    network = ipaddress.ip_network(source, strict=False)
                except ValueError:
                    continue
                if not network.is_private:
                    fail(
                        "jumpbox-nsg",
                        f"Public inbound RDP from {source} is allowed by NSG rule {rule.get('name')}.",
                    )
    if not has_bastion_rdp:
        fail("jumpbox-nsg", f"No {bastion_subnet_prefix}-only RDP rule exists for Bastion.")
    if not has_deny_all_other_inbound:
        fail("jumpbox-nsg", "No explicit deny-all rule overrides the default AllowVnetInBound rule.")
    return f"{bastions[0]['name']}, {vm_name}, Entra login, shutdown, and deny-public-inbound controls"


def validate_jumpbox_cosmos_rbac(
    resource_group: str, accounts: list[dict[str, Any]], principal_id: str
) -> str:
    reader_role_id = "00000000-0000-0000-0000-000000000001"
    missing_data_reader: list[str] = []
    missing_account_reader: list[str] = []
    for account in accounts:
        assignment = run_az(
            [
                "cosmosdb",
                "sql",
                "role",
                "assignment",
                "list",
                "-g",
                resource_group,
                "-a",
                account["name"],
                "--query",
                (
                    f"[?principalId=='{principal_id}' && "
                    f"ends_with(roleDefinitionId, '{reader_role_id}')].id | [0]"
                ),
                "-o",
                "tsv",
            ]
        )
        if not assignment:
            missing_data_reader.append(account["name"])
        account_reader = run_az(
            [
                "role",
                "assignment",
                "list",
                "--assignee-object-id",
                principal_id,
                "--scope",
                account["id"],
                "--query",
                "[?roleDefinitionName=='Reader'].id | [0]",
                "-o",
                "tsv",
            ]
        )
        if not account_reader:
            missing_account_reader.append(account["name"])
    if missing_data_reader:
        fail(
            "jumpbox-cosmos-rbac",
            f"Cosmos Data Reader is missing on: {', '.join(missing_data_reader)}.",
        )
    if missing_account_reader:
        fail(
            "jumpbox-cosmos-account-rbac",
            f"ARM Reader is missing on: {', '.join(missing_account_reader)}.",
        )
    return f"{principal_id} has Cosmos Data Reader and ARM Reader on {len(accounts)} account(s)"


def validate_jumpbox_private_connectivity(
    resource_group: str, vm_name: str, accounts: list[dict[str, Any]]
) -> str:
    hosts = [host_from_endpoint(account["endpoint"]) for account in accounts]
    quoted_hosts = ",".join(f"'{host}'" for host in hosts)
    script = (
        f"$ErrorActionPreference='Stop'; $hosts=@({quoted_hosts}); "
        "foreach ($hostName in $hosts) { "
        "$addresses=(Resolve-DnsName $hostName -Type A).IPAddress; "
        "foreach ($address in $addresses) { Write-Output \"DNS|$hostName|$address\" }; "
        "$tcp=Test-NetConnection $hostName -Port 443 -InformationLevel Quiet; "
        "Write-Output \"TCP|$hostName|$tcp\" }"
    )
    response = json.loads(
        run_az(
            [
                "vm",
                "run-command",
                "invoke",
                "-g",
                resource_group,
                "-n",
                vm_name,
                "--command-id",
                "RunPowerShellScript",
                "--scripts",
                script,
                "--query",
                "value[0].message",
                "-o",
                "json",
            ]
        )
    )
    for host in hosts:
        addresses = re.findall(rf"DNS\|{re.escape(host)}\|(\d+\.\d+\.\d+\.\d+)", response)
        if not addresses or not all(ipaddress.ip_address(address).is_private for address in addresses):
            fail("jumpbox-private-dns", f"{host} did not resolve exclusively to private IPv4 addresses: {addresses}.")
        if f"TCP|{host}|True" not in response:
            fail("jumpbox-private-connectivity", f"{host}:443 was not reachable from {vm_name}.")
    return f"{len(hosts)} Cosmos endpoints resolve privately and accept TCP 443 from {vm_name}"


def validate_jumpbox_portal_egress(resource_group: str, vm_name: str) -> str:
    hosts = ["portal.azure.com", "login.microsoftonline.com"]
    quoted_hosts = ",".join(f"'{host}'" for host in hosts)
    script = (
        f"$hosts=@({quoted_hosts}); foreach ($hostName in $hosts) {{ "
        "$tcp=Test-NetConnection $hostName -Port 443 -InformationLevel Quiet; "
        "Write-Output \"TCP|$hostName|$tcp\" }"
    )
    response = json.loads(
        run_az(
            [
                "vm",
                "run-command",
                "invoke",
                "-g",
                resource_group,
                "-n",
                vm_name,
                "--command-id",
                "RunPowerShellScript",
                "--scripts",
                script,
                "--query",
                "value[0].message",
                "-o",
                "json",
            ]
        )
    )
    failed = [host for host in hosts if f"TCP|{host}|True" not in response]
    if failed:
        fail("jumpbox-portal-egress", f"TCP 443 failed from {vm_name} to: {', '.join(failed)}.")
    return f"TCP 443 reaches Azure Portal and Microsoft Entra sign-in from {vm_name}"


def print_dry_run(args: argparse.Namespace) -> None:
    print("DRY-RUN: pass --live to run read-only Azure, HTTP, and app-host checks.")
    print("Planned live checks:")
    for item in [
        "frontend and backend /health",
        "validation JWT issuer, audience, optional upstream IdP, tenant, roles, status, and scopes",
        "APIM same-tenant portfolio GET succeeds",
        "APIM cross-tenant portfolio GET returns 403",
        "APIM malformed token returns 401",
        "direct backend internal call without service auth is rejected",
        "Cosmos private DNS resolves to private IPs from frontend app host",
        "public-route Cosmos probes fail",
        "all Cosmos accounts have disableLocalAuth=true and publicNetworkAccess=Disabled",
        "tenant-mismatch scheduled-query alert is configured for >5 events in 5 minutes",
    ]:
        print(f"- {item}")
    if args.validate_jumpbox:
        print("- Bastion, Entra VM login, auto-shutdown, deny-public-inbound NSG, Cosmos reader RBAC, VM private connectivity, and portal egress")
    if not args.access_token:
        print("Token checks will require VALIDATION_ACCESS_TOKEN or --access-token.")


def main() -> int:
    args = parse_args()
    if args.jumpbox_only:
        args.validate_jumpbox = True
    results: list[Check] = []
    tenant_record = load_fixture(args.fixture, args.tenant)
    portfolio_id, position_id, _ = fixture_paths(tenant_record)
    add_result(results, "fixture", f"tenant={args.tenant}, portfolio={portfolio_id}, position={position_id}")

    if not args.live:
        print_dry_run(args)
        return 0

    if not args.access_token and not args.jumpbox_only:
        fail("config", "Live API and token checks require VALIDATION_ACCESS_TOKEN or --access-token. The token is never printed or stored.")

    run_az(["account", "show", "--query", "id", "-o", "tsv"])
    resource_group = discover_resource_group(args)
    add_result(results, "azure-login", f"resourceGroup={resource_group}")

    if args.jumpbox_only:
        principal_id = args.jumpbox_user_principal_id
        if not principal_id:
            fail("jumpbox-config", "Provide --jumpbox-user-principal-id or set JUMPBOX_USER_PRINCIPAL_ID.")
        vm_name = args.jumpbox_vm_name or (
            f"vm-{args.environment_name}-jumpbox" if args.environment_name else ""
        )
        if not vm_name:
            fail("jumpbox-config", "Provide --jumpbox-vm-name or --environment-name.")
        accounts = list_cosmos_accounts(resource_group)
        add_result(results, "cosmos-security", validate_cosmos_security(accounts))
        add_result(
            results,
            "jumpbox-resources",
            validate_jumpbox_resources(resource_group, vm_name, principal_id),
        )
        add_result(
            results,
            "jumpbox-cosmos-rbac",
            validate_jumpbox_cosmos_rbac(resource_group, accounts, principal_id),
        )
        add_result(
            results,
            "jumpbox-private-connectivity",
            validate_jumpbox_private_connectivity(resource_group, vm_name, accounts),
        )
        add_result(
            results,
            "jumpbox-portal-egress",
            validate_jumpbox_portal_egress(resource_group, vm_name),
        )
        print(f"Validation completed: {len(results)} checks passed.")
        return 0

    apim_url, frontend_url, backend_fqdn, frontend_app = discover_urls(args, resource_group)
    add_result(results, "resource-discovery", f"apim={apim_url}, frontend={frontend_url}, backendFqdn={backend_fqdn or 'not-set'}")

    add_result(results, "frontend-health", assert_http("frontend-health", f"{frontend_url}/health", {200}))
    add_result(
        results,
        "token-claims",
        validate_token_claims(
            args.access_token,
            args.tenant,
            args.expected_role,
            args.expected_issuer,
            args.expected_audience,
            args.expected_idp,
        ),
    )

    same_tenant_url = f"{apim_url}/api/tenants/{urllib.parse.quote(args.tenant)}/portfolios"
    cross_tenant_url = f"{apim_url}/api/tenants/{urllib.parse.quote(args.cross_tenant)}/portfolios"
    add_result(results, "same-tenant-access", assert_http("same-tenant-access", same_tenant_url, {200}, token=args.access_token))
    add_result(results, "cross-tenant-isolation", assert_http("cross-tenant-isolation", cross_tenant_url, {403}, token=args.access_token))
    add_result(results, "bad-token-rejection", assert_http("bad-token-rejection", same_tenant_url, {401}, token="invalid.validation.token"))

    accounts = list_cosmos_accounts(resource_group)
    add_result(results, "cosmos-security", validate_cosmos_security(accounts))

    if not args.skip_app_host_checks:
        add_result(results, "backend-app-host", validate_backend_from_app_host(resource_group, frontend_app, backend_fqdn, args.tenant))
        add_result(results, "cosmos-private-dns", validate_private_dns(resource_group, frontend_app, accounts))
    else:
        print("SKIP app-host backend and private DNS checks (--skip-app-host-checks).")

    if not args.skip_public_cosmos_check:
        add_result(results, "cosmos-public-access", validate_public_cosmos_failure(accounts))
    else:
        print("SKIP public Cosmos access probe (--skip-public-cosmos-check).")

    add_result(results, "tenant-mismatch-alert", validate_alert(resource_group))
    if args.validate_jumpbox:
        principal_id = args.jumpbox_user_principal_id
        if not principal_id:
            fail("jumpbox-config", "Provide --jumpbox-user-principal-id or set JUMPBOX_USER_PRINCIPAL_ID.")
        vm_name = args.jumpbox_vm_name or (
            f"vm-{args.environment_name}-jumpbox" if args.environment_name else ""
        )
        if not vm_name:
            fail("jumpbox-config", "Provide --jumpbox-vm-name or --environment-name.")
        add_result(
            results,
            "jumpbox-resources",
            validate_jumpbox_resources(resource_group, vm_name, principal_id),
        )
        add_result(
            results,
            "jumpbox-cosmos-rbac",
            validate_jumpbox_cosmos_rbac(resource_group, accounts, principal_id),
        )
        add_result(
            results,
            "jumpbox-private-connectivity",
            validate_jumpbox_private_connectivity(resource_group, vm_name, accounts),
        )
        add_result(
            results,
            "jumpbox-portal-egress",
            validate_jumpbox_portal_egress(resource_group, vm_name),
        )
    print(f"Validation completed: {len(results)} checks passed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ValidationError as error:
        print(f"FAIL {error}", file=sys.stderr)
        raise SystemExit(1)
