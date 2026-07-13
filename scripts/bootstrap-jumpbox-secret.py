#!/usr/bin/env python3
"""Create the jumpbox emergency administrator secret without exposing its value."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import string
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
TEMPLATE = SCRIPT_DIR.parent / "infra" / "bootstrap-jumpbox-secret.bicep"
SECRET_NAME = "jumpbox-local-admin-password"


class BootstrapError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create the Key Vault secret required to provision the Windows jumpbox."
    )
    parser.add_argument("--resource-group", required=True)
    parser.add_argument("--key-vault", required=True)
    parser.add_argument(
        "--rotate",
        action="store_true",
        help="Create a new secret version when the secret already exists.",
    )
    return parser.parse_args()


def run_az(args: list[str], *, required: bool = True) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        ["az", *args, "--only-show-errors"],
        check=False,
        capture_output=True,
        text=True,
    )
    if required and completed.returncode != 0:
        raise BootstrapError(completed.stderr.strip() or completed.stdout.strip())
    return completed


def secret_resource_exists(resource_group: str, key_vault: str) -> bool:
    vault = run_az(
        [
            "keyvault",
            "show",
            "--resource-group",
            resource_group,
            "--name",
            key_vault,
            "--query",
            "id",
            "-o",
            "tsv",
        ]
    ).stdout.strip()
    secret_id = f"{vault}/secrets/{SECRET_NAME}"
    result = run_az(
        [
            "resource",
            "show",
            "--ids",
            secret_id,
            "--api-version",
            "2023-07-01",
            "-o",
            "none",
        ],
        required=False,
    )
    return result.returncode == 0


def generate_password(length: int = 40) -> str:
    alphabet = string.ascii_letters + string.digits + "!#%+-_=."
    while True:
        password = "".join(secrets.choice(alphabet) for _ in range(length))
        if (
            any(character.islower() for character in password)
            and any(character.isupper() for character in password)
            and any(character.isdigit() for character in password)
            and any(character in "!#%+-_=." for character in password)
        ):
            return password


def deploy_secret(resource_group: str, key_vault: str, password: str) -> None:
    parameters = {
        "$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#",
        "contentVersion": "1.0.0.0",
        "parameters": {
            "keyVaultName": {"value": key_vault},
            "adminPassword": {"value": password},
        },
    }
    descriptor, path = tempfile.mkstemp(prefix="jumpbox-secret-", suffix=".json")
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(parameters, handle)
        run_az(
            [
                "deployment",
                "group",
                "create",
                "--name",
                "bootstrap-jumpbox-secret",
                "--resource-group",
                resource_group,
                "--template-file",
                str(TEMPLATE),
                "--parameters",
                f"@{path}",
                "-o",
                "none",
            ]
        )
    finally:
        Path(path).unlink(missing_ok=True)


def main() -> int:
    args = parse_args()
    try:
        if secret_resource_exists(args.resource_group, args.key_vault) and not args.rotate:
            print(f"Secret {SECRET_NAME} already exists; no new version was created.")
            return 0
        run_az(
            [
                "keyvault",
                "update",
                "--resource-group",
                args.resource_group,
                "--name",
                args.key_vault,
                "--enabled-for-template-deployment",
                "true",
                "-o",
                "none",
            ]
        )
        deploy_secret(args.resource_group, args.key_vault, generate_password())
        print(f"Created a new version of {SECRET_NAME} in {args.key_vault}.")
        return 0
    except BootstrapError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
