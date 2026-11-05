"""Missing-selection routing and authoritative contract candidate filtering."""

from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from billing_line_items import line_item_context, needs_line_item_resolution
from billing_tools import BillingTools


def unselected_order():
    return {
        "WorkOrderId": "123", "RemarksCombined": "Troubleshot POS software printer routing",
        "WorkOrderLines": [{"LineNumber": 1, "ContractId": 10, "ContractItemId": 0,
                            "ArrivalTime": "2026-01-26T17:15:00"}],
        "Contracts": [{"Id": 10, "Items": [
            {"Id": 101, "ProductName": "POS software support", "ContractStartDate": "2026-01-01",
             "ContractEndDate": "2026-12-31"},
            {"Id": 102, "ProductName": "Expired support", "ContractStartDate": "2025-01-01",
             "ContractEndDate": "2025-12-31"},
        ]}],
    }


class LineItemTests(unittest.TestCase):
    def test_required_call_uses_correct_schemas_for_both_apis(self):
        tools = BillingTools(unselected_order(), None, None, order_id="123")
        self.assertTrue(tools.requires_line_item)
        self.assertEqual(tools.tool_choice(0, 4, completed_tools=set()),
                         {"type": "function", "function": {"name": "get_line_item"}})
        self.assertEqual(tools.tool_choice(0, 4, completed_tools=set(), responses=True),
                         {"type": "function", "name": "get_line_item"})
        self.assertEqual(tools.tool_choice(1, 4, completed_tools={"get_line_item"}),
                         {"type": "function", "function": {"name": "get_special_rules"}})
        complete = {"get_line_item", "get_special_rules"}
        self.assertEqual(tools.tool_choice(2, 4, completed_tools=complete), "auto")
        self.assertEqual(tools.tool_choice(4, 4, completed_tools=complete), "none")
        with self.assertRaisesRegex(ValueError, "At least 2 tool rounds"):
            tools.tool_choice(0, 0, completed_tools=set())

    def test_real_handler_returns_candidates_and_remarks_without_mutating_order(self):
        order = unselected_order()
        original = deepcopy(order)
        tools = BillingTools(order, None, None, order_id="123")
        result, cached = tools.execute("get_line_item", "{}")
        self.assertFalse(cached)
        line = result["line_item"]["line_items"][0]
        self.assertEqual([c["contract_item_id"] for c in line["candidates"]], [101])
        self.assertIn("printer routing", result["line_item"]["remarks"])
        self.assertEqual(line["original_contract_item_id"], 0)
        self.assertEqual(order, original)
        self.assertTrue(tools.execute("get_line_item", "{}")[1])

    def test_missing_selection_variants_and_selected_items(self):
        for value in (None, 0, "0", "", -1):
            order = unselected_order()
            order["WorkOrderLines"][0]["ContractItemId"] = value
            self.assertTrue(needs_line_item_resolution(order))
        order["WorkOrderLines"][0]["ContractItemId"] = 101
        self.assertFalse(needs_line_item_resolution(order))
        self.assertEqual(line_item_context(order)["line_items"][0]["status"], "already_selected")

    def test_project_and_major_account_early_stops(self):
        order = unselected_order()
        order["ProjectId"] = 10
        self.assertFalse(needs_line_item_resolution(order))
        order["ProjectId"] = 0
        order.update(IsMajorAccount=True, MajorAccountName="FCL")
        self.assertFalse(needs_line_item_resolution(order))

    def test_no_date_no_contract_and_unrelated_contract_keep_original_ids(self):
        order = unselected_order()
        order["WorkOrderLines"][0]["ArrivalTime"] = None
        entry = line_item_context(order)["line_items"][0]
        self.assertEqual(entry["status"], "missing_service_date")
        order = unselected_order()
        order["Contracts"][0]["Id"] = 99
        self.assertEqual(line_item_context(order)["line_items"][0]["candidates"], [])
        order["Contracts"] = []
        self.assertFalse(needs_line_item_resolution(order))

    def test_contract_term_fallback_and_missing_end_date(self):
        order = unselected_order()
        item = order["Contracts"][0]["Items"][0]
        del item["ContractStartDate"], item["ContractEndDate"]
        order["Contracts"][0]["ContractTerm"] = {
            "SWContractStartDate": "2026-01-01", "SWContractEndDate": "2026-12-31",
        }
        entry = line_item_context(order)["line_items"][0]
        self.assertEqual(entry["candidates"][0]["contract_item_id"], 101)
        item["ContractStartDate"] = "2026-01-01"
        self.assertEqual(line_item_context(order)["line_items"][0]["candidates"], [])
