#!/usr/bin/env python3
"""Contract tests for the official Therefore WebAPI User operations."""

import contextlib
import io
import sys
import unittest

sys.path.insert(0, "src")

from mcp_server import MCPServer, OPERATION_REGISTRY, build_tools  # noqa: E402
from therefore_client import ThereforeClient, ThereforeConfig  # noqa: E402


class RecordingClient(ThereforeClient):
    def __init__(self):
        super().__init__(ThereforeConfig(
            base_url="https://example.invalid/theservice/v0001/restun",
            username="u",
            password="p",
            tenant_name="test",
            auth_method="Basic",
        ))
        self.calls = []

    def _post(self, path, payload, **kwargs):
        self.calls.append((path, payload))
        return {}


class UserOperationContractTests(unittest.TestCase):
    def setUp(self):
        self.client = RecordingClient()

    def test_create_user_uses_nested_user_contract(self):
        self.client.create_user(
            user_name="jane.smith",
            display_name="Jane Smith",
            email="jane@example.com",
            password="initial",
            one_time_password=True,
        )
        self.assertEqual(self.client.calls[0], (
            "CreateUser",
            {
                "Password": "initial",
                "User": {
                    "UserName": "jane.smith",
                    "DisplayName": "Jane Smith",
                    "SMTP": "jane@example.com",
                    "UserType": 1,
                    "Disabled": False,
                    "OneTimePwd": True,
                },
            },
        ))

    def test_user_listing_omits_nullable_query(self):
        self.client.execute_users_query()
        self.assertEqual(self.client.calls[0], ("ExecuteUsersQuery", {"Flags": 4}))

    def test_password_and_license_session_contracts(self):
        self.client.change_user_password("jane.smith", "old", "new", "DOMAIN")
        self.client.reset_user_password("jane.smith")
        self.client.move_user_license()
        self.client.sign_out()
        self.assertEqual(self.client.calls, [
            ("ChangeUserPassword", {
                "UserName": "jane.smith",
                "OldPassword": "old",
                "NewPassword": "new",
                "DomainName": "DOMAIN",
            }),
            ("ResetUserPwd", {"UserInfo": "jane.smith"}),
            ("MoveUserLicense", {}),
            ("SignOut", {}),
        ])

    def test_group_assignment_uses_nested_contract(self):
        self.client.update_user_group_assignment(
            user_id=9,
            assignments=[
                {"group_id": 12},
                {"group_name": "Old Group", "remove": True},
            ],
        )
        self.assertEqual(self.client.calls[0], (
            "UpdateUserGroupAssignment",
            {
                "User": {"Id": 9},
                "Assignments": [
                    {"ThereforeGroup": {"Id": 12}, "Remove": False},
                    {"ThereforeGroup": {"Name": "Old Group"}, "Remove": True},
                ],
            },
        ))

    def test_group_identifiers_are_validated(self):
        with self.assertRaises(ValueError):
            self.client.get_users_from_group()
        with self.assertRaises(ValueError):
            self.client.update_user_group_assignment(user_id=9, assignments=[])
        with self.assertRaises(ValueError):
            self.client.update_user_group_assignment(
                user_id=9, assignments=[{"remove": False}]
            )

    def test_grouped_tool_exposes_and_dispatches_completed_surface(self):
        for operation in ("get_connected", "sign_out", "move_license"):
            self.assertIn(("therefore_users", operation), OPERATION_REGISTRY)
        users_tool = next(t for t in build_tools() if t["name"] == "therefore_users")
        operation_enum = users_tool["inputSchema"]["properties"]["operation"]["enum"]
        self.assertIn("get_connected", operation_enum)
        self.assertIn("sign_out", operation_enum)
        server = MCPServer(
            clients={"test": self.client},
            default_tenant="test",
            tenant_labels={"test": "Test"},
        )
        server._call_tool("therefore_users", {
            "operation": "get_connected", "tenant": "test"
        })
        server._call_tool("therefore_users", {
            "operation": "sign_out", "tenant": "test"
        })
        self.assertEqual(self.client.calls[-2:], [
            ("GetConnectedUser", {"Create": False}),
            ("SignOut", {}),
        ])

    def test_audit_log_redacts_all_password_variants(self):
        server = MCPServer(
            clients={"test": self.client},
            default_tenant="test",
            tenant_labels={"test": "Test"},
        )
        output = io.StringIO()
        with contextlib.redirect_stderr(output):
            server._audit_log("therefore_users", "test", {
                "old_password": "old-secret",
                "new_password": "new-secret",
                "nested": {"Password": "initial-secret"},
            })
        logged = output.getvalue()
        self.assertNotIn("old-secret", logged)
        self.assertNotIn("new-secret", logged)
        self.assertNotIn("initial-secret", logged)
        self.assertGreaterEqual(logged.count("[REDACTED]"), 3)


if __name__ == "__main__":
    unittest.main()
