#!/usr/bin/env python3
"""Set non-secret azd auth environment values from auth registration artifacts."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


DEFAULT_ARTIFACT = Path("docs/external-id-app-registrations.local.json")
DEFAULT_SERVICE_AUTH_ARTIFACT = Path("docs/internal-entra-service-auth.local.json")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", default=str(DEFAULT_ARTIFACT), help="Path to the local app registration JSON artifact.")
    parser.add_argument("--service-auth-artifact", default=str(DEFAULT_SERVICE_AUTH_ARTIFACT), help="Path to internal Entra service auth JSON artifact.")
    parser.add_argument("--dry-run", action="store_true", help="Print azd env set commands without executing them.")
    return parser.parse_args()


def require(data: dict[str, Any], path: str) -> str:
    value: Any = data
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            raise KeyError(path)
        value = value[part]
    if not isinstance(value, str) or not value:
        raise ValueError(path)
    return value


def build_values(data: dict[str, Any], service_auth: dict[str, Any] | None) -> dict[str, str]:
    values = {
        "EXTERNAL_ID_TENANT_ID": require(data, "tenant.tenantId"),
        "EXTERNAL_ID_ISSUER": require(data, "tenant.issuer"),
        "EXTERNAL_ID_AUTHORITY": require(data, "tenant.msalAuthority"),
        "API_AUDIENCE": require(data, "appRegistrations.frontendApi.audience"),
        "SPA_CLIENT_ID": require(data, "appRegistrations.spa.clientId"),
        "FRONTEND_API_CLIENT_ID": require(data, "appRegistrations.frontendApi.clientId"),
        "CLAIMS_PROVIDER_APP_ID": require(data, "appRegistrations.customClaimsProvider.clientId"),
        "CLAIMS_PROVIDER_AUDIENCE": require(data, "appRegistrations.customClaimsProvider.audience"),
    }
    if service_auth is not None:
        values.update(
            {
                "BACKEND_SERVICE_AUTHORITY": require(service_auth, "azdEnv.BACKEND_SERVICE_AUTHORITY"),
                "BACKEND_SERVICE_ISSUER": require(service_auth, "azdEnv.BACKEND_SERVICE_ISSUER"),
                "BACKEND_API_AUDIENCE": require(service_auth, "azdEnv.BACKEND_API_AUDIENCE"),
                "BACKEND_API_SERVICE_TOKEN_SCOPE": require(service_auth, "azdEnv.BACKEND_API_SERVICE_TOKEN_SCOPE"),
            }
        )
    return values


def main() -> int:
    args = parse_args()
    artifact = Path(args.artifact)
    try:
        with artifact.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        service_auth_path = Path(args.service_auth_artifact)
        service_auth = None
        if service_auth_path.exists():
            with service_auth_path.open("r", encoding="utf-8") as handle:
                service_auth = json.load(handle)
        values = build_values(data, service_auth)
    except (OSError, json.JSONDecodeError, KeyError, ValueError) as exc:
        print(f"Failed to read non-secret auth values from {artifact}: {exc}", file=sys.stderr)
        return 1

    for key, value in values.items():
        command = ["azd", "env", "set", key, value]
        if args.dry_run:
            print(" ".join(command))
            continue
        subprocess.run(command, check=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
