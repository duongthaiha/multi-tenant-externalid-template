from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPOSITORY_ROOT / "scripts" / "create-workforce-federation-app.py"
SPEC = importlib.util.spec_from_file_location("create_workforce_federation_app", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
federation_app = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(federation_app)


class WorkforceFederationAppTests(unittest.TestCase):
    def test_provider_values_use_domain_based_issuer_for_acceleration(self) -> None:
        values = federation_app.provider_values(
            "ContosoWorkforce.onmicrosoft.com".lower(),
            "client-id",
        )

        self.assertEqual(
            "https://login.microsoftonline.com/contosoworkforce.onmicrosoft.com/v2.0",
            values["issuer"],
        )
        self.assertEqual("client-id", values["clientId"])

    def test_validate_domain_normalizes_and_rejects_non_dns_values(self) -> None:
        self.assertEqual(
            "contosoworkforce.onmicrosoft.com",
            federation_app.validate_domain(
                "ContosoWorkforce.onmicrosoft.com",
                "--workforce-tenant-domain",
            ),
        )

        for value in ("https://example.com", "bad domain", "-bad.example.com"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(RuntimeError, "valid DNS name|must be a DNS name"):
                    federation_app.validate_domain(value, "--workforce-tenant-domain")


if __name__ == "__main__":
    unittest.main()
