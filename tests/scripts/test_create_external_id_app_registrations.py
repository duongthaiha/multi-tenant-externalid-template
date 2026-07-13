from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPOSITORY_ROOT / "scripts" / "create-external-id-app-registrations.py"
SPEC = importlib.util.spec_from_file_location("create_external_id_app_registrations", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
registrations = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(registrations)


class ExternalIdAppRegistrationTests(unittest.TestCase):
    def test_existing_service_principal_is_reused(self) -> None:
        existing = {
            "id": "service-principal-id",
            "appId": "client-id",
            "displayName": "Example",
        }
        with patch.object(registrations, "find_service_principal", return_value=existing):
            with patch.object(registrations, "graph_request") as request:
                result, created = registrations.create_or_get_service_principal(
                    "token",
                    {"appId": "client-id", "displayName": "Example"},
                )

        self.assertEqual(existing, result)
        self.assertFalse(created)
        request.assert_not_called()

    def test_missing_service_principal_is_created_and_reread(self) -> None:
        created_service_principal = {
            "id": "new-service-principal-id",
            "appId": "client-id",
            "displayName": "Example",
        }
        with patch.object(
            registrations,
            "find_service_principal",
            side_effect=[None, created_service_principal],
        ):
            with patch.object(registrations, "graph_request", return_value={}) as request:
                result, created = registrations.create_or_get_service_principal(
                    "token",
                    {"appId": "client-id", "displayName": "Example"},
                )

        self.assertEqual(created_service_principal, result)
        self.assertTrue(created)
        request.assert_called_once_with(
            "token",
            "POST",
            "/servicePrincipals",
            {"appId": "client-id"},
        )


if __name__ == "__main__":
    unittest.main()
