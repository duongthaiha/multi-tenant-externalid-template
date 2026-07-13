from __future__ import annotations

import argparse
import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPOSITORY_ROOT / "scripts" / "create-workforce-federated-users.py"
SPEC = importlib.util.spec_from_file_location("create_workforce_federated_users", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
federated_users = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(federated_users)


class FederatedUserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.temp_path = Path(self.temporary_directory.name)
        self.manifest_user = {
            "sourceTenantId": federated_users.DEFAULT_WORKFORCE_TENANT_ID,
            "sourceObjectId": "00000000-0000-4000-8000-000000000001",
            "email": "person@example.com",
            "displayName": "Example Person",
            "businessTenantId": "AlphaCapital",
            "roles": ["PortfolioViewer"],
            "status": "active",
        }
        self.issuer = federated_users.federated_issuer(
            federated_users.DEFAULT_WORKFORCE_TENANT_DOMAIN,
            federated_users.DEFAULT_EXTERNAL_TENANT_ID,
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def write_manifest(self, users: list[dict[str, object]]) -> Path:
        path = self.temp_path / "manifest.json"
        path.write_text(
            json.dumps(
                {
                    "sourceTenantId": federated_users.DEFAULT_WORKFORCE_TENANT_ID,
                    "users": users,
                }
            ),
            encoding="utf-8",
        )
        return path

    def external_user(self, object_id: str = "external-object-id") -> dict[str, object]:
        return {
            "id": object_id,
            "identities": [
                {
                    "signInType": "federated",
                    "issuer": self.issuer,
                    "issuerAssignedId": self.manifest_user["sourceObjectId"],
                }
            ],
        }

    def test_sample_manifest_is_valid_and_covers_allowed_business_tenants(self) -> None:
        sample = REPOSITORY_ROOT / "docs" / "workforce-federation-authorized-users.sample.json"
        users = federated_users.load_manifest(
            sample,
            federated_users.DEFAULT_WORKFORCE_TENANT_ID,
        )
        self.assertEqual(
            federated_users.ALLOWED_BUSINESS_TENANTS,
            {user["businessTenantId"] for user in users},
        )

    def test_manifest_rejects_invalid_business_tenant(self) -> None:
        user = dict(self.manifest_user, businessTenantId="UnknownTenant")
        with self.assertRaisesRegex(RuntimeError, "businessTenantId.*invalid"):
            federated_users.load_manifest(
                self.write_manifest([user]),
                federated_users.DEFAULT_WORKFORCE_TENANT_ID,
            )

    def test_shared_validator_rejects_duplicates_roles_status_and_cross_tenant(self) -> None:
        cases = [
            ([self.manifest_user, self.manifest_user], "Duplicate sourceObjectId"),
            ([dict(self.manifest_user, roles=["Owner"])], "unsupported roles"),
            ([dict(self.manifest_user, status="inactive")], "status must be 'active'"),
            (
                [
                    dict(
                        self.manifest_user,
                        sourceTenantId="11111111-1111-4111-8111-111111111111",
                    )
                ],
                "does not match workforce tenant",
            ),
        ]
        for users, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(RuntimeError, message):
                    federated_users.load_manifest(
                        self.write_manifest(users),
                        federated_users.DEFAULT_WORKFORCE_TENANT_ID,
                    )

    def test_source_user_accepts_case_insensitive_mail_or_upn(self) -> None:
        for mail, upn in [
            ("PERSON@EXAMPLE.COM", "other@example.com"),
            (None, "Person@Example.com"),
        ]:
            with self.subTest(mail=mail, upn=upn):
                federated_users.validate_source_user(
                    self.manifest_user,
                    {
                        "id": self.manifest_user["sourceObjectId"].upper(),
                        "accountEnabled": True,
                        "mail": mail,
                        "userPrincipalName": upn,
                    },
                )

    def test_source_user_rejects_disabled_and_email_mismatch(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "disabled"):
            federated_users.validate_source_user(
                self.manifest_user,
                {
                    "id": self.manifest_user["sourceObjectId"],
                    "accountEnabled": False,
                    "mail": self.manifest_user["email"],
                },
            )
        with self.assertRaisesRegex(RuntimeError, "Manifest email mismatch"):
            federated_users.validate_source_user(
                self.manifest_user,
                {
                    "id": self.manifest_user["sourceObjectId"],
                    "accountEnabled": True,
                    "mail": "someone-else@example.com",
                    "userPrincipalName": "another@example.com",
                },
            )

    def test_find_external_user_requires_exact_federated_identity(self) -> None:
        wrong_identity = self.external_user()
        wrong_identity["identities"][0]["signInType"] = "emailAddress"
        with patch.object(
            federated_users,
            "graph_request",
            return_value={"value": [wrong_identity]},
        ):
            with self.assertRaisesRegex(RuntimeError, "exact federated identity"):
                federated_users.find_external_user(
                    "external-token",
                    self.issuer,
                    self.manifest_user["sourceObjectId"],
                )

    def test_find_external_user_rejects_ambiguous_results(self) -> None:
        with patch.object(
            federated_users,
            "graph_request",
            return_value={"value": [self.external_user("one"), self.external_user("two")]},
        ):
            with self.assertRaisesRegex(RuntimeError, "Multiple External ID users"):
                federated_users.find_external_user(
                    "external-token",
                    self.issuer,
                    self.manifest_user["sourceObjectId"],
                )

    def test_create_uses_documented_identity_and_rereads_user(self) -> None:
        reread = self.external_user("new-external-id")
        calls: list[tuple[str, str, dict[str, object] | None]] = []

        def request(
            token: str,
            method: str,
            path: str,
            body: dict[str, object] | None = None,
        ) -> dict[str, object]:
            self.assertEqual("external-token", token)
            calls.append((method, path, body))
            return {"id": "new-external-id"} if method == "POST" else reread

        with patch.object(federated_users, "graph_request", side_effect=request):
            result = federated_users.create_external_user(
                "external-token",
                self.manifest_user,
                self.issuer,
            )

        self.assertEqual("new-external-id", result["id"])
        self.assertEqual(["POST", "GET"], [call[0] for call in calls])
        self.assertEqual(
            [
                {
                    "signInType": "federated",
                    "issuer": self.issuer,
                    "issuerAssignedId": self.manifest_user["sourceObjectId"],
                }
            ],
            calls[0][2]["identities"],
        )
        self.assertEqual([self.manifest_user["email"]], calls[0][2]["otherMails"])
        self.assertEqual(self.manifest_user["email"], calls[0][2]["mail"])

    def test_update_federated_identity_preserves_other_identities(self) -> None:
        legacy_issuer = federated_users.legacy_federated_issuer(
            federated_users.DEFAULT_WORKFORCE_TENANT_ID,
            federated_users.DEFAULT_EXTERNAL_TENANT_ID,
        )
        user = self.external_user()
        user["identities"][0]["issuer"] = legacy_issuer
        user["identities"].append(
            {
                "signInType": "userPrincipalName",
                "issuer": "contosoexternalid.onmicrosoft.com",
                "issuerAssignedId": "external-object-id@contosoexternalid.onmicrosoft.com",
            }
        )
        reread = self.external_user()
        reread["identities"].append(user["identities"][1])
        calls: list[tuple[str, str, dict[str, object] | None]] = []

        def request(
            token: str,
            method: str,
            path: str,
            body: dict[str, object] | None = None,
        ) -> dict[str, object]:
            calls.append((method, path, body))
            return {} if method == "PATCH" else reread

        with patch.object(federated_users, "graph_request", side_effect=request):
            result = federated_users.update_federated_identity(
                "external-token",
                user,
                self.manifest_user["sourceObjectId"],
                legacy_issuer,
                self.issuer,
            )

        self.assertEqual("external-object-id", result["id"])
        self.assertEqual(["PATCH", "GET"], [call[0] for call in calls])
        self.assertEqual(self.issuer, calls[0][2]["identities"][0]["issuer"])
        self.assertEqual(user["identities"][1], calls[0][2]["identities"][1])

    def test_dry_run_has_no_auth_network_or_output_write(self) -> None:
        manifest = self.write_manifest([self.manifest_user])
        output = self.temp_path / "must-not-exist.json"
        args = argparse.Namespace(
            manifest=str(manifest),
            workforce_tenant_id=federated_users.DEFAULT_WORKFORCE_TENANT_ID,
            workforce_tenant_domain=federated_users.DEFAULT_WORKFORCE_TENANT_DOMAIN,
            external_tenant_id=federated_users.DEFAULT_EXTERNAL_TENANT_ID,
            output=str(output),
            dry_run=True,
        )
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            patch.object(federated_users, "parse_args", return_value=args),
            patch.object(
                federated_users,
                "get_graph_token",
                side_effect=AssertionError("must not authenticate"),
            ),
            patch.object(
                federated_users,
                "graph_request",
                side_effect=AssertionError("must not call Graph"),
            ),
            patch.object(
                federated_users,
                "write_json",
                side_effect=AssertionError("must not write"),
            ),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            self.assertEqual(0, federated_users.main())
        plan = json.loads(stdout.getvalue())
        self.assertTrue(plan["dryRun"])
        self.assertEqual(self.issuer, plan["federatedIssuer"])
        self.assertEqual([self.manifest_user], plan["users"])
        self.assertFalse(output.exists())
        self.assertEqual("", stderr.getvalue())

    def test_live_flow_acquires_distinct_tenant_tokens(self) -> None:
        manifest = self.write_manifest([self.manifest_user])
        output = self.temp_path / "output.json"
        args = argparse.Namespace(
            manifest=str(manifest),
            workforce_tenant_id=federated_users.DEFAULT_WORKFORCE_TENANT_ID,
            workforce_tenant_domain=federated_users.DEFAULT_WORKFORCE_TENANT_DOMAIN,
            external_tenant_id=federated_users.DEFAULT_EXTERNAL_TENANT_ID,
            output=str(output),
            dry_run=False,
        )
        token_tenants: list[str] = []

        def token(tenant_id: str) -> str:
            token_tenants.append(tenant_id)
            return f"token-{tenant_id}"

        with (
            patch.object(federated_users, "parse_args", return_value=args),
            patch.object(federated_users, "get_graph_token", side_effect=token),
            patch.object(
                federated_users,
                "resolve_source_users",
                return_value=[(self.manifest_user, {"id": self.manifest_user["sourceObjectId"]})],
            ),
            patch.object(
                federated_users,
                "provision_users",
                return_value=[
                    {
                        **self.manifest_user,
                        "externalIdObjectId": "external-id",
                        "created": False,
                        "federatedIssuer": self.issuer,
                    }
                ],
            ),
            redirect_stdout(io.StringIO()),
            redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(0, federated_users.main())

        self.assertEqual(
            [
                federated_users.DEFAULT_WORKFORCE_TENANT_ID,
                federated_users.DEFAULT_EXTERNAL_TENANT_ID,
            ],
            token_tenants,
        )
        self.assertTrue(output.exists())


if __name__ == "__main__":
    unittest.main()
