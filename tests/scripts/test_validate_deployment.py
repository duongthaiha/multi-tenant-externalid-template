from __future__ import annotations

import base64
import importlib.util
import json
import sys
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPOSITORY_ROOT / "scripts" / "validate-deployment.py"
SPEC = importlib.util.spec_from_file_location("validate_deployment", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
validation = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = validation
SPEC.loader.exec_module(validation)


def token_with_claims(claims: dict[str, object]) -> str:
    payload = base64.urlsafe_b64encode(
        json.dumps(claims, separators=(",", ":")).encode("utf-8")
    ).decode("ascii").rstrip("=")
    return f"header.{payload}.signature"


class WorkforceFederationTokenTests(unittest.TestCase):
    def setUp(self) -> None:
        self.issuer = (
            "https://11111111-1111-4111-8111-111111111111.ciamlogin.com/"
            "11111111-1111-4111-8111-111111111111/v2.0"
        )
        self.audience = "33333333-3333-4333-8333-333333333333"
        self.idp = (
            "https://login.microsoftonline.com/"
            "66666666-6666-4666-8666-666666666666/v2.0"
        )
        self.claims = {
            "iss": self.issuer,
            "aud": self.audience,
            "idp": self.idp,
            "extension_tenantId": "AlphaCapital",
            "tenant_status": "active",
            "tenant_roles": ["TenantAdmin"],
            "scp": "assets.read assets.write",
        }

    def test_accepts_expected_external_id_and_upstream_idp_claims(self) -> None:
        result = validation.validate_token_claims(
            token_with_claims(self.claims),
            "AlphaCapital",
            ["TenantAdmin"],
            self.issuer,
            self.audience,
            self.idp,
        )
        self.assertIn("tenant=AlphaCapital", result)

    def test_rejects_workforce_issuer_as_application_issuer(self) -> None:
        claims = dict(self.claims, iss=self.idp)
        with self.assertRaisesRegex(validation.ValidationError, "External ID issuer"):
            validation.validate_token_claims(
                token_with_claims(claims),
                "AlphaCapital",
                [],
                self.issuer,
                self.audience,
                self.idp,
            )

    def test_rejects_unexpected_upstream_idp(self) -> None:
        with self.assertRaisesRegex(validation.ValidationError, "upstream identity provider"):
            validation.validate_token_claims(
                token_with_claims(self.claims),
                "AlphaCapital",
                [],
                self.issuer,
                self.audience,
                "https://login.microsoftonline.com/other/v2.0",
            )


if __name__ == "__main__":
    unittest.main()
