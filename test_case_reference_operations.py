#!/usr/bin/env python3
"""Contract tests for referenced-table case index operations."""

import json
import sys
import unittest

sys.path.insert(0, "src")

from mcp_server import MCPServer  # noqa: E402
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
        self.responses = []

    def _post(self, path, payload, **kwargs):
        self.calls.append((path, payload))
        return self.responses.pop(0) if self.responses else {}


class ReferencedCaseOperationTests(unittest.TestCase):
    def setUp(self):
        self.client = RecordingClient()

    def test_dependent_query_contract(self):
        self.client.execute_dependent_fields_query(
            field_no=3742,
            index_data_items=[],
            case_definition_no=11,
            max_rows=20,
        )
        self.assertEqual(self.client.calls[0], (
            "ExecuteDependentFieldsQuery",
            {
                "CaseDefinitionNo": 11,
                "FieldNo": 3742,
                "IndexDataItems": [],
                "MaxRows": 20,
                "SaveMode": False,
            },
        ))

    def test_dependent_query_requires_one_definition_context(self):
        with self.assertRaises(ValueError):
            self.client.execute_dependent_fields_query(3742, [])
        with self.assertRaises(ValueError):
            self.client.execute_dependent_fields_query(
                3742, [], category_no=154, case_definition_no=11
            )

    def test_fill_dependent_fields_omits_unused_contexts(self):
        items = [{"StringIndexData": {"FieldNo": 3742, "DataValue": "1"}}]
        self.client.fill_dependent_fields(
            items,
            3742,
            case_definition_no=11,
        )
        payload = self.client.calls[0][1]
        self.assertEqual(payload["CaseDefinitionNo"], 11)
        self.assertNotIn("DocNo", payload)
        self.assertNotIn("CategoryNo", payload)

    def test_fill_dependent_fields_requires_exactly_one_context(self):
        with self.assertRaises(ValueError):
            self.client.fill_dependent_fields([], 3742)
        with self.assertRaises(ValueError):
            self.client.fill_dependent_fields([], 3742, doc_no=1, category_no=2)

    def test_quick_save_contract(self):
        items = [{"StringIndexData": {"FieldNo": 3742, "DataValue": "1"}}]
        self.client.save_case_index_data_quick(100, items, "test")
        self.assertEqual(self.client.calls[0], (
            "SaveCaseIndexDataQuick",
            {
                "CaseNo": 100,
                "CheckInComments": "test",
                "IndexData": {"IndexDataItems": items},
            },
        ))

    def test_full_save_fetches_current_case_timestamps(self):
        items = [{"StringIndexData": {"FieldNo": 3742, "DataValue": "1"}}]
        self.client.responses = [{
            "Case": {"IndexData": {
                "LastChangeTime": "/Date(1)/",
                "LastChangeTimeISO8601": "2026-08-05T00:00:00Z",
            }}
        }, {}]
        self.client.save_case_index_data(100, items)
        self.assertEqual(self.client.calls[0], ("GetCase", {"CaseNo": 100}))
        self.assertEqual(self.client.calls[1][0], "SaveCaseIndexData")
        index_data = self.client.calls[1][1]["IndexData"]
        self.assertEqual(index_data["LastChangeTime"], "/Date(1)/")
        self.assertEqual(index_data["LastChangeTimeISO8601"], "2026-08-05T00:00:00Z")

    def test_grouped_tool_dispatches_new_operations(self):
        server = MCPServer(
            clients={"test": self.client},
            default_tenant="test",
            tenant_labels={"test": "Test"},
        )
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "therefore_workflow",
                "arguments": {
                    "operation": "fill_dependent_fields",
                    "tenant": "test",
                    "primary_field_no": 3742,
                    "case_definition_no": 11,
                    "index_data_items": [
                        {"StringIndexData": {"FieldNo": 3742, "DataValue": "1"}}
                    ],
                },
            },
        }
        response = server.handle(request)
        self.assertNotIn("error", response)
        self.assertFalse(response["result"].get("isError", False))
        self.assertEqual(self.client.calls[-1][0], "FillDependentFields")
        json.loads(response["result"]["content"][0]["text"])

    def test_high_level_resolver_queries_and_fills_selected_category_row(self):
        self.client.responses = [
            {"CategoryFields": [{
                "FieldNo": 3749,
                "TypeNo": 173,
                "IsForeignDatatype": True,
            }]},
            {"IndexData": {"IndexDataItems": [
                {"IntIndexData": {"FieldNo": 3749, "DataValue": None}}
            ]}},
            {
                "IndexColumn": "CaseNo",
                "TypeNo": 173,
                "Columns": [{"ColumnName": "CaseNo", "Type": 2}],
            },
            {
                "QueryResult": {
                    "Columns": [{"FieldNo": 3749}],
                    "ResultRows": [{"FieldValues": ["66"]}],
                },
                "AllRowsReturned": True,
            },
            {"UpdatedIndexDataItems": [
                {"IntIndexData": {"FieldNo": 3749, "DataValue": 66}}
            ]},
        ]
        server = MCPServer(
            clients={"test": self.client},
            default_tenant="test",
            tenant_labels={"test": "Test"},
        )
        result = server._call_tool("therefore_categories", {
            "operation": "resolve_referenced_field",
            "tenant": "test",
            "category_no": 154,
            "field_no": 3749,
            "selected_row_index": 0,
        })
        self.assertEqual(result["selected"]["index_data_item"], {
            "IntIndexData": {"FieldNo": 3749, "DataValue": 66}
        })
        self.assertEqual(self.client.calls[-1][0], "FillDependentFields")
        self.assertEqual(self.client.calls[-1][1]["CategoryNo"], 154)


if __name__ == "__main__":
    unittest.main()
