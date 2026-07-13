from __future__ import annotations

import argparse
import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPOSITORY_ROOT / "scripts" / "seed-federated-entitlements.py"
SPEC = importlib.util.spec_from_file_location("seed_federated_entitlements", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
entitlements = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(entitlements)


class FederatedEntitlementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.temp_path = Path(self.temporary_directory.name)
        self.issuer = entitlements.expected_federated_issuer(
            entitlements.DEFAULT_WORKFORCE_TENANT_DOMAIN,
            entitlements.DEFAULT_EXTERNAL_TENANT_ID,
        )
        self.user = {
            "sourceTenantId": entitlements.DEFAULT_WORKFORCE_TENANT_ID,
            "sourceObjectId": "00000000-0000-4000-8000-000000000001",
            "externalIdObjectId": "00000000-0000-4000-8000-000000000101",
            "email": "person@example.com",
            "displayName": "Example Person",
            "businessTenantId": "AlphaCapital",
            "roles": ["PortfolioViewer"],
            "status": "active",
            "created": True,
            "federatedIssuer": self.issuer,
        }

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def write_map(self, users: list[dict[str, object]]) -> Path:
        path = self.temp_path / "users.json"
        path.write_text(
            json.dumps(
                {
                    "workforceTenantId": entitlements.DEFAULT_WORKFORCE_TENANT_ID,
                    "externalTenantId": entitlements.DEFAULT_EXTERNAL_TENANT_ID,
                    "users": users,
                }
            ),
            encoding="utf-8",
        )
        return path

    def test_validation_normalizes_uuid_and_rejects_invalid_contract_values(self) -> None:
        loaded = entitlements.load_user_map(self.write_map([self.user]))
        self.assertEqual([self.user], loaded)

        cases = [
            (dict(self.user, email="Person@example.com"), "normalized lowercase"),
            (dict(self.user, status="inactive"), "status must be 'active'"),
            (dict(self.user, roles=["Owner"]), "unsupported roles"),
            (dict(self.user, businessTenantId="Unknown"), "businessTenantId.*invalid"),
            (dict(self.user, federatedIssuer=self.issuer + "/"), "must exactly equal"),
            (dict(self.user, externalIdObjectId="not-a-uuid"), "must be a UUID"),
        ]
        for user, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(entitlements.SeedError, message):
                    entitlements.load_user_map(self.write_map([user]))

    def test_validation_rejects_duplicate_source_and_external_ids(self) -> None:
        second = dict(
            self.user,
            sourceObjectId="00000000-0000-4000-8000-000000000002",
            externalIdObjectId="00000000-0000-4000-8000-000000000102",
        )
        for duplicate, message in [
            (dict(second, sourceObjectId=self.user["sourceObjectId"]), "Duplicate sourceObjectId"),
            (
                dict(second, externalIdObjectId=self.user["externalIdObjectId"]),
                "Duplicate externalIdObjectId",
            ),
        ]:
            with self.subTest(message=message):
                with self.assertRaisesRegex(entitlements.SeedError, message):
                    entitlements.load_user_map(self.write_map([self.user, duplicate]))

    def test_document_shape_uses_external_id_object_id_as_user_id(self) -> None:
        membership, role = entitlements.build_documents(
            self.user, "api://frontend"
        )
        external_id = self.user["externalIdObjectId"]
        self.assertEqual(f"membership-AlphaCapital-{external_id}", membership["id"])
        self.assertEqual(external_id, membership["userId"])
        self.assertEqual("workforceFederation", membership["identityProvider"])
        self.assertEqual("UserTenantMembership", membership["documentType"])
        self.assertEqual(f"role-AlphaCapital-{external_id}", role["id"])
        self.assertEqual("AlphaCapital", role["tenantId"])
        self.assertEqual("api://frontend", role["resourceAppId"])
        self.assertEqual(["PortfolioViewer"], role["roles"])
        for field in ("sourceTenantId", "sourceObjectId", "externalIdObjectId"):
            self.assertEqual(self.user[field], membership[field])
            self.assertEqual(self.user[field], role[field])

    def test_disable_selects_only_explicit_mapped_users_and_empties_roles(self) -> None:
        second = dict(
            self.user,
            sourceObjectId="00000000-0000-4000-8000-000000000002",
            externalIdObjectId="00000000-0000-4000-8000-000000000102",
            businessTenantId="BetaWealth",
        )
        selected, disabled = entitlements.select_users(
            [self.user, second], [second["sourceObjectId"]]
        )
        self.assertTrue(disabled)
        self.assertEqual([second], selected)
        membership, role = entitlements.build_documents(
            selected[0], "api://frontend", disabled=True
        )
        self.assertEqual("inactive", membership["status"])
        self.assertEqual("inactive", role["status"])
        self.assertEqual([], role["roles"])
        with self.assertRaisesRegex(entitlements.SeedError, "must exist"):
            entitlements.select_users(
                [self.user], ["00000000-0000-4000-8000-000000000999"]
            )

    def test_dry_run_validates_and_prints_documents_without_azure_access(self) -> None:
        user_map = self.write_map([self.user])
        args = argparse.Namespace(
            user_map=str(user_map),
            resource_group=None,
            environment_name=None,
            resource_app_id="api://frontend",
            workforce_tenant_id=entitlements.DEFAULT_WORKFORCE_TENANT_ID,
            workforce_tenant_domain=entitlements.DEFAULT_WORKFORCE_TENANT_DOMAIN,
            external_tenant_id=entitlements.DEFAULT_EXTERNAL_TENANT_ID,
            disable_source_object_id=[],
            dry_run=True,
        )
        output = io.StringIO()
        with (
            patch.object(entitlements, "parse_args", return_value=args),
            patch.object(
                entitlements._SEED,
                "discover_resource_group",
                side_effect=AssertionError("must not discover Azure resources"),
            ),
            patch.object(
                entitlements._SEED,
                "get_cosmos_token",
                side_effect=AssertionError("must not authenticate"),
            ),
            patch.object(
                entitlements._SEED,
                "upsert_document",
                side_effect=AssertionError("must not use the network"),
            ),
            redirect_stdout(output),
        ):
            self.assertEqual(0, entitlements.main())

        lines = output.getvalue().splitlines()
        operations = [
            json.loads(line.removeprefix("UPSERT "))
            for line in lines
            if line.startswith("UPSERT ")
        ]
        self.assertEqual(
            ["memberships", "roleAssignments"],
            [op["container"] for op in operations],
        )
        self.assertIn("Planned 2 federated entitlement document(s).", lines)


if __name__ == "__main__":
    unittest.main()
