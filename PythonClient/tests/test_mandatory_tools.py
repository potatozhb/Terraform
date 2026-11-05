"""Exercise the actual required-tool policy across independent model conversations."""

from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from billing_tools import BillingTools
from compare_model import compare_models
from test_compare_model import completion, tool_completion
from test_line_items import unselected_order


def responses_reply(tool_name=None):
    from openai.types.responses import ResponseFunctionToolCall
    output = [] if tool_name is None else [ResponseFunctionToolCall(
        type="function_call", call_id="call_" + tool_name, name=tool_name, arguments="{}",
    )]
    return SimpleNamespace(model="gpt-6.1-sol", status="completed", usage=None, output=output,
                           output_text="" if tool_name else completion().choices[0].message.content)


class RequiredToolTests(unittest.TestCase):
    def setUp(self):
        self.order = {"WorkOrderId": "123", "Contracts": []}
        self.chat = Mock()
        self.search = Mock()
        self.search.search.return_value = []

    def compare(self, **kwargs):
        return compare_models(self.order, self.search, Mock(), self.chat,
                              kwargs.pop("models", ["model-a", "model-b"]), **kwargs)

    def test_rules_required_for_each_model_even_when_shared_cache_is_populated(self):
        self.chat.chat.completions.create.side_effect = [
            tool_completion("get_special_rules"), completion(),
            tool_completion("get_special_rules"), completion(),
        ]
        report = self.compare()
        self.search.search.assert_called_once()
        requests = self.chat.chat.completions.create.call_args_list
        for index in (0, 2):
            self.assertEqual(requests[index].kwargs["tool_choice"],
                             {"type": "function", "function": {"name": "get_special_rules"}})
            self.assertEqual(requests[index + 1].kwargs["tool_choice"], "auto")
        self.assertEqual(report["results"][0]["completed_tools"], ["get_special_rules"])
        self.assertTrue(report["results"][1]["tool_calls"][0]["cached"])
        self.assertEqual([row["status"] for row in report["results"]], ["success", "success"])

    def test_missing_line_item_then_rules_then_final_for_both_apis(self):
        self.order = unselected_order()
        responses = Mock()
        responses.responses.create.side_effect = [
            responses_reply("get_line_item"), responses_reply("get_special_rules"), responses_reply(),
        ]
        self.chat.chat.completions.create.side_effect = [
            tool_completion("get_line_item"), tool_completion("get_special_rules"), completion(),
        ]
        report = self.compare(models=["ai-billing-foundry-6.1-sol", "model-b"], responses_client=responses)
        choices = [c.kwargs["tool_choice"] for c in responses.responses.create.call_args_list]
        self.assertEqual(choices, [{"type": "function", "name": "get_line_item"},
                                   {"type": "function", "name": "get_special_rules"}, "auto"])
        self.assertEqual([r["status"] for r in report["results"]], ["success", "success"])
        for row in report["results"]:
            self.assertEqual(row["completed_tools"], ["get_line_item", "get_special_rules"])

    def test_failed_rule_retrieval_is_not_marked_completed(self):
        self.chat.chat.completions.create.side_effect = [
            tool_completion("get_special_rules"), tool_completion("get_special_rules"), completion(),
        ]
        self.search.search.side_effect = [RuntimeError("Search failed"), []]
        report = self.compare()
        self.assertEqual(report["results"][0]["status"], "tool_error")
        self.assertEqual(report["results"][0]["completed_tools"], [])
        self.assertEqual(report["results"][1]["status"], "success")

    def test_premature_final_is_rejected_on_both_apis(self):
        self.chat.chat.completions.create.return_value = completion()
        responses = Mock()
        responses.responses.create.return_value = responses_reply()
        report = self.compare(models=["ai-billing-foundry-6.1-sol", "model-b"], responses_client=responses)
        for row in report["results"]:
            self.assertEqual(row["status"], "invalid_response")
            self.assertIn("required get_special_rules", row["error"])

    def test_insufficient_budget_fails_before_any_request(self):
        self.order = unselected_order()
        report = self.compare(max_tool_rounds=1)
        self.chat.chat.completions.create.assert_not_called()
        for row in report["results"]:
            self.assertIn("At least 2 tool rounds", row["error"])

    def test_early_stop_orders_still_require_rules_but_not_line_item(self):
        for additions in ({"ProjectId": 12}, {"IsMajorAccount": True, "MajorAccountName": "FCL"}):
            order = {**unselected_order(), **additions}
            tools = BillingTools(order, self.search, Mock(), order_id="123")
            self.assertEqual(tools.required_tools(), ["get_special_rules"])
            with self.assertRaisesRegex(ValueError, "get_special_rules"):
                tools.validate_required_tools(set())
            tools.validate_required_tools({"get_special_rules"})
