"""Compare deployments without Azure calls or model charges."""

import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from compare_model import build_argument_parser, compare_all_orders, compare_models, write_reports
from compare_model import _wait_for_round
from billing_tools import BillingTools, build_tool_messages
from test_line_items import unselected_order


def completion(status="FREE", confidence=0.95, **overrides):
    decision = {
        "order_id": "123", "billing_status": status, "confidence": confidence,
        "summary": "Covered service", "usedEvidence": {"contracts": [], "specialRules": [], "history": []},
        "reasoning": {"contractReason": "Covered", "specialRuleReason": "None", "historyReason": "None"},
    }
    message = SimpleNamespace(content=json.dumps(decision), refusal=None, tool_calls=None)
    choice = SimpleNamespace(message=message, finish_reason="stop")
    usage = Mock()
    usage.model_dump.return_value = {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
    return SimpleNamespace(model="actual-model-version", usage=usage, choices=[choice], **overrides)


def tool_completion(*names, arguments="{}"):
    response = completion()
    response.choices[0].finish_reason = "tool_calls"
    response.choices[0].message.content = None
    response.choices[0].message.tool_calls = [
        SimpleNamespace(id=f"call_{i}", type="function",
                        function=SimpleNamespace(name=name, arguments=arguments))
        for i, name in enumerate(names)
    ]
    return response


class ConversationFixtureTools(BillingTools):
    """Isolate response/report tests from the mandatory-rule policy.

    Real required-tool sequencing is tested in test_mandatory_tools.py.
    """

    def required_tools(self):
        return ["get_line_item"] if self.requires_line_item else []


class CompareModelTests(unittest.TestCase):
    def setUp(self):
        self.order = {"WorkOrderId": "123", "Contracts": []}
        self.client = Mock()
        self.tool_policy_patch = patch("compare_model.BillingTools", ConversationFixtureTools)
        self.tool_policy_patch.start()
        self.addCleanup(self.tool_policy_patch.stop)
        self.prompt_patch = patch("compare_model.build_tool_messages", wraps=build_tool_messages)
        self.builder = self.prompt_patch.start()
        self.addCleanup(self.prompt_patch.stop)
        self.pacing_patch = patch("compare_model._wait_for_round", return_value=0)
        self.pacing = self.pacing_patch.start()
        self.addCleanup(self.pacing_patch.stop)

    def run_comparison(self, models=None):
        return compare_models(self.order, Mock(), Mock(), self.client, models or ["model-a", "model-b"])

    @patch.dict(os.environ, {}, clear=True)
    def test_terraform_defaults(self):
        args = build_argument_parser().parse_args(["--blob-name", "orders/123.json"])
        self.assertEqual(args.models, ["ai-billing-foundry-6.1-sol", "ai-billing-foundry-5.6-sol"])
        self.assertEqual(args.round_interval, 60)
        self.assertEqual(args.storage_account, "aibillingstorage69fe")
        self.assertEqual(args.storage_container, "foundry")
        self.assertEqual(args.search_endpoint, "https://ai-billing-search-69fe.search.windows.net")
        self.assertEqual(args.search_index, "billing-knowledge-index")
        self.assertEqual(args.openai_endpoint, "https://ai-billing-foundry-69fe.cognitiveservices.azure.com")

    @patch.dict(os.environ, {"AZURE_STORAGE_CONTAINER_NAME": "custom",
                            "AZURE_OPENAI_COMPARE_DEPLOYMENTS": "model-x,model-y",
                            "AZURE_STORAGE_BLOB_NAME": "existing.json"}, clear=True)
    def test_environment_and_cli_overrides(self):
        parser = build_argument_parser()
        args = parser.parse_args([])
        self.assertEqual(args.storage_container, "custom")
        self.assertEqual(args.models, ["model-x", "model-y"])
        self.assertEqual(args.blob_name, "existing.json")
        args = parser.parse_args(["--storage-container", "cli-container", "--models", "a", "b"])
        self.assertEqual(args.storage_container, "cli-container")
        self.assertEqual(args.models, ["a", "b"])

    @patch.dict(os.environ, {"AZURE_STORAGE_CONTAINER_NAME": "", "AZURE_OPENAI_COMPARE_DEPLOYMENTS": " "}, clear=True)
    def test_empty_env_template_values_keep_defaults(self):
        args = build_argument_parser().parse_args([])
        self.assertEqual(args.storage_container, "foundry")
        self.assertEqual(len(args.models), 2)

    def test_prompts_built_once_identical_requests_and_disagreement(self):
        self.client.chat.completions.create.side_effect = [completion(), completion("BILLABLE")]
        report = self.run_comparison()
        self.builder.assert_called_once()
        calls = self.client.chat.completions.create.call_args_list
        self.assertEqual(calls[0].kwargs["messages"], calls[1].kwargs["messages"])
        self.assertIsNot(calls[0].kwargs["messages"], calls[1].kwargs["messages"])
        self.assertNotIn("temperature", calls[0].kwargs)
        self.assertEqual(calls[0].kwargs["response_format"], {"type": "json_object"})
        self.assertFalse(report["comparison"]["all_models_agree"])
        self.assertTrue(report["comparison"]["disagreement_among_successful_models"])
        self.assertEqual(report["results"][0]["usage"]["total_tokens"], 150)
        self.assertEqual(len(report["prompt_sha256"]), 64)

    def test_api_failure_does_not_stop_next_model_or_claim_agreement(self):
        from openai import OpenAIError
        self.client.chat.completions.create.side_effect = [OpenAIError("Unavailable"), completion()]
        report = self.run_comparison()
        self.assertEqual([row["status"] for row in report["results"]], ["error", "success"])
        self.assertIsNone(report["comparison"]["all_models_agree"])
        self.assertEqual(report["comparison"]["failed_models"], 1)

    def test_61_uses_responses_and_replays_reasoning_and_tool_outputs(self):
        from openai.types.responses import ResponseFunctionToolCall, ResponseReasoningItem
        reasoning = ResponseReasoningItem(id="rs1", summary=[], type="reasoning", encrypted_content="opaque")
        tool_call = ResponseFunctionToolCall(id="fc1", call_id="call_r", name="get_special_rules",
                                             arguments="{}", type="function_call")
        usage = Mock()
        usage.model_dump.return_value = {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150}
        responses = Mock()
        responses.responses.create.side_effect = [
            SimpleNamespace(model="gpt-6.1-sol", output_text="", output=[reasoning, tool_call],
                            usage=usage, status="completed"),
            SimpleNamespace(model="gpt-6.1-sol", output_text=completion().choices[0].message.content,
                            output=[], usage=usage, status="completed"),
        ]
        self.client.chat.completions.create.return_value = completion()
        with patch("billing_tools.retrieve_special_rules", return_value=[]):
            report = compare_models(self.order, Mock(), Mock(), self.client,
                                    ["ai-billing-foundry-6.1-sol", "ai-billing-foundry-5.6-sol"],
                                    responses_client=responses)
        requests = responses.responses.create.call_args_list
        self.assertEqual(requests[0].kwargs["reasoning"], {"effort": "low"})
        self.assertNotIn("reasoning_effort", requests[0].kwargs)
        self.assertFalse(requests[0].kwargs["store"])
        self.assertIn("name", requests[0].kwargs["tools"][0])
        replay = requests[1].kwargs["input"]
        self.assertEqual(replay[2]["encrypted_content"], "opaque")
        self.assertEqual(replay[3]["type"], "function_call")
        self.assertEqual(replay[4]["type"], "function_call_output")
        self.assertEqual(replay[4]["call_id"], "call_r")
        self.client.chat.completions.create.assert_called_once()
        self.assertNotIn("reasoning_effort", self.client.chat.completions.create.call_args.kwargs)
        self.assertEqual(report["results"][0]["usage"]["total_tokens"], 300)
        self.assertEqual(report["results"][0]["api"], "responses")
        self.assertEqual([row["status"] for row in report["results"]], ["success", "success"])

    def test_responses_incomplete_result_is_failure_and_56_continues(self):
        responses = Mock()
        responses.responses.create.return_value = SimpleNamespace(
            model="gpt-6.1-sol", output_text="", output=[], usage=None,
            status="incomplete", incomplete_details={"reason": "max_output_tokens"},
        )
        self.client.chat.completions.create.return_value = completion()
        report = compare_models(self.order, Mock(), Mock(), self.client,
                                ["ai-billing-foundry-6.1-sol", "ai-billing-foundry-5.6-sol"],
                                responses_client=responses)
        self.assertEqual(report["results"][0]["status"], "invalid_response")
        self.assertIn("max_output_tokens", report["results"][0]["error"])
        self.assertEqual(report["results"][1]["status"], "success")

    def test_order_only_input_and_lazy_tools_share_cached_evidence(self):
        self.client.chat.completions.create.side_effect = [
            tool_completion("get_special_rules", "get_historical_orders"), completion(),
            tool_completion("get_historical_orders", "get_special_rules"), completion(),
        ]
        rules, history = [{"document_id": "rule-1"}], [{"document_id": "old-1"}]
        with patch("billing_tools.retrieve_special_rules", return_value=rules) as rule_search, \
             patch("billing_tools.retrieve_historical_orders", return_value=history) as history_search:
            report = self.run_comparison()
        rule_search.assert_called_once()
        history_search.assert_called_once()
        calls = self.client.chat.completions.create.call_args_list
        initial = calls[0].kwargs["messages"]
        self.assertEqual(len(initial), 2)
        self.assertEqual(json.loads(initial[1]["content"]), {**self.order, "work_order_id": "123"})
        self.assertNotIn("rule-1", initial[1]["content"])
        self.assertEqual(initial, calls[2].kwargs["messages"])
        second_request = calls[1].kwargs["messages"]
        self.assertEqual([m["role"] for m in second_request], ["system", "user", "assistant", "tool", "tool"])
        self.assertEqual(second_request[3]["tool_call_id"], "call_0")
        self.assertEqual(json.loads(second_request[3]["content"]), {"special_rules": rules})
        self.assertEqual(report["results"][0]["usage"]["total_tokens"], 300)
        self.assertTrue(all(call["cached"] for call in report["results"][1]["tool_calls"]))
        self.assertTrue(report["comparison"]["all_models_agree"])

    def test_no_retrieval_until_a_tool_is_called(self):
        self.client.chat.completions.create.side_effect = [completion(), completion()]
        with patch("billing_tools.retrieve_special_rules") as rules, \
             patch("billing_tools.retrieve_historical_orders") as history:
            report = self.run_comparison()
        rules.assert_not_called()
        history.assert_not_called()
        self.assertEqual(report["results"][0]["tool_calls"], [])

    def test_missing_contract_item_forces_first_chat_call_and_then_final_decision(self):
        self.order = unselected_order()
        self.client.chat.completions.create.side_effect = [
            tool_completion("get_line_item"), completion(), tool_completion("get_line_item"), completion(),
        ]
        report = self.run_comparison()
        requests = self.client.chat.completions.create.call_args_list
        self.assertEqual(requests[0].kwargs["tool_choice"],
                         {"type": "function", "function": {"name": "get_line_item"}})
        self.assertEqual(requests[1].kwargs["tool_choice"], "auto")
        tool_output = json.loads(requests[1].kwargs["messages"][-1]["content"])
        self.assertEqual(tool_output["line_item"]["line_items"][0]["candidates"][0]["contract_item_id"], 101)
        self.assertEqual([r["status"] for r in report["results"]], ["success", "success"])

    def test_missing_contract_item_forces_first_responses_call(self):
        from openai.types.responses import ResponseFunctionToolCall
        responses = Mock()
        responses.responses.create.side_effect = [
            SimpleNamespace(model="gpt-6.1-sol", output_text="", usage=None, status="completed", output=[
                ResponseFunctionToolCall(call_id="line1", name="get_line_item", arguments="{}", type="function_call")]),
            SimpleNamespace(model="gpt-6.1-sol", output_text=completion().choices[0].message.content,
                            usage=None, status="completed", output=[]),
        ]
        self.client.chat.completions.create.side_effect = [tool_completion("get_line_item"), completion()]
        report = compare_models(unselected_order(), Mock(), Mock(), self.client,
                                ["ai-billing-foundry-6.1-sol", "model-b"], responses_client=responses)
        self.assertEqual(responses.responses.create.call_args_list[0].kwargs["tool_choice"],
                         {"type": "function", "name": "get_line_item"})
        self.assertEqual(report["results"][0]["status"], "success")

    def test_final_answer_without_required_line_item_call_is_not_accepted(self):
        self.order = unselected_order()
        self.client.chat.completions.create.side_effect = [completion(), completion()]
        report = self.run_comparison()
        self.assertEqual(report["results"][0]["status"], "invalid_response")
        self.assertIn("required get_line_item", report["results"][0]["error"])

    def test_tool_failure_is_recorded_and_other_model_continues(self):
        self.client.chat.completions.create.side_effect = [tool_completion("get_special_rules"), completion()]
        with patch("billing_tools.retrieve_special_rules", side_effect=RuntimeError("Search unavailable")):
            report = self.run_comparison()
        self.assertEqual([r["status"] for r in report["results"]], ["tool_error", "success"])
        self.assertIn("Search unavailable", report["results"][0]["tool_calls"][0]["error"])
        self.assertIsNone(report["results"][0]["decision"])

    def test_tool_cannot_override_target_or_store_filter(self):
        self.client.chat.completions.create.side_effect = [
            tool_completion("get_historical_orders", arguments='{"order_id": "another-order"}'), completion(),
        ]
        with patch("billing_tools.retrieve_historical_orders") as retrieve:
            report = self.run_comparison()
        retrieve.assert_not_called()
        self.assertEqual(report["results"][0]["status"], "tool_error")

    def test_bounded_tool_loop(self):
        self.client.chat.completions.create.side_effect = [
            tool_completion("get_special_rules"), tool_completion("get_special_rules"), completion(),
        ]
        with patch("billing_tools.retrieve_special_rules", return_value=[]):
            report = compare_models(self.order, Mock(), Mock(), self.client, ["a", "b"], max_tool_rounds=1)
        self.assertIn("maximum tool-call rounds", report["results"][0]["error"])
        self.assertEqual(self.client.chat.completions.create.call_args_list[1].kwargs["tool_choice"], "none")
        self.assertEqual(report["results"][1]["status"], "success")

    def test_invalid_and_truncated_responses_preserve_raw_content_and_usage(self):
        invalid = completion(confidence=2)
        truncated = completion()
        truncated.choices[0].finish_reason = "length"
        self.client.chat.completions.create.side_effect = [invalid, truncated]
        report = self.run_comparison()
        for row in report["results"]:
            self.assertEqual(row["status"], "invalid_response")
            self.assertIsNotNone(row["raw_response"])
            self.assertEqual(row["usage"]["total_tokens"], 150)
            self.assertIsNone(row["decision"])

    def test_non_json_refusal_and_wrong_order_are_not_success(self):
        for case in ("non_json", "refusal", "wrong_order"):
            with self.subTest(case=case):
                bad = completion()
                if case == "non_json":
                    bad.choices[0].message.content = "Not JSON"
                elif case == "refusal":
                    bad.choices[0].message.refusal = "Cannot answer"
                else:
                    payload = json.loads(bad.choices[0].message.content)
                    payload["order_id"] = "another-order"
                    bad.choices[0].message.content = json.dumps(payload)
                self.client.chat.completions.create.side_effect = [bad, completion()]
                report = self.run_comparison()
                self.assertEqual(report["results"][0]["status"], "invalid_response")

    def test_deduplicates_models_and_requires_two_distinct_deployments(self):
        with self.assertRaisesRegex(ValueError, "at least two"):
            self.run_comparison(["model-a", "model-a"])
        self.builder.assert_not_called()
        self.client.chat.completions.create.side_effect = [completion(), completion()]
        report = self.run_comparison(["model-a", "model-a", "model-b"])
        self.assertEqual(len(report["results"]), 2)
        self.assertTrue(report["comparison"]["all_models_agree"])

    def test_prompt_construction_failure_prevents_all_model_calls(self):
        self.builder.side_effect = RuntimeError("Search unavailable")
        with self.assertRaisesRegex(RuntimeError, "Search unavailable"):
            self.run_comparison()
        self.client.chat.completions.create.assert_not_called()

    def test_json_and_markdown_reports(self):
        self.client.chat.completions.create.side_effect = [completion(), completion()]
        report = self.run_comparison()
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "nested" / "comparison.json"
            write_reports(report, output)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), report)
            table = output.with_suffix(".md").read_text(encoding="utf-8")
            self.assertIn("| model-a | success | FREE |", table)
            self.assertIn("All models agree: True", table)

    def make_container(self, names):
        container = Mock()
        container.url = "https://example.blob.core.windows.net/foundry?secret=sas"
        container.list_blobs.return_value = (SimpleNamespace(name=name) for name in names)
        blob = Mock()
        blob.__enter__ = Mock(return_value=blob)
        blob.__exit__ = Mock(return_value=False)
        blob.download_blob.return_value.readall.return_value = json.dumps(self.order).encode()
        container.get_blob_client.return_value = blob
        return container

    def test_batch_processes_blobs_sequentially_and_writes_reports(self):
        container = self.make_container(["orders/a.json", "notes.txt", "orders/b.JSON"])
        self.client.chat.completions.create.side_effect = [completion() for _ in range(4)]
        events = []
        container.get_blob_client.side_effect = lambda name: (
            events.append(name) or container.get_blob_client.return_value)
        self.builder.side_effect = lambda *a, **kw: events.append("prompts") or build_tool_messages(*a, **kw)
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "batch.json"
            batch = compare_all_orders(container, Mock(), Mock(), self.client, ["a", "b"],
                                       output=output, prefix="orders/")
            self.assertEqual(events, ["orders/a.json", "prompts", "orders/b.JSON", "prompts"])
            self.assertEqual(batch["processed_orders"], 2)
            self.assertEqual(batch["skipped_non_json_blobs"], 1)
            self.assertEqual(batch["status"], "completed")
            self.assertEqual(batch["round_interval_seconds"], 60)
            self.assertEqual(self.pacing.call_count, 2)
            self.assertEqual(self.pacing.call_args_list[0].args, (None, 60))
            self.assertEqual(self.pacing.call_args_list[1].args, (0, 60))
            self.assertNotIn("secret", batch["source"]["container_url"])
            container.list_blobs.assert_called_once_with(name_starts_with="orders/")
            for order in batch["orders"]:
                self.assertTrue((output.parent / order["report_file"]).exists())
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), batch)

    def test_bad_blob_does_not_stop_next_order_and_limit_is_respected(self):
        container = self.make_container(["bad.json", "good.json", "not-processed.json"])
        self.client.chat.completions.create.side_effect = [completion(), completion()]
        with patch("compare_model.load_order_from_blob", side_effect=[ValueError("Bad JSON"), self.order]):
            with tempfile.TemporaryDirectory() as folder:
                batch = compare_all_orders(container, Mock(), Mock(), self.client, ["a", "b"],
                                           output=Path(folder) / "batch.json", limit=2)
        self.assertEqual(batch["processed_orders"], 2)
        self.assertEqual(batch["failed_orders"], 1)
        self.assertEqual([o["status"] for o in batch["orders"]], ["error", "success"])
        self.assertIn("Bad JSON", batch["orders"][0]["error"])

    def test_listing_failure_checkpoints_completed_orders(self):
        from azure.core.exceptions import AzureError
        container = self.make_container([])
        def pages():
            yield SimpleNamespace(name="first.json")
            raise AzureError("Listing failed")
        container.list_blobs.return_value = pages()
        self.client.chat.completions.create.side_effect = [completion(), completion()]
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "batch.json"
            with self.assertRaisesRegex(AzureError, "Listing failed"):
                compare_all_orders(container, Mock(), Mock(), self.client, ["a", "b"], output=output)
            checkpoint = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(checkpoint["status"], "interrupted")
            self.assertEqual(checkpoint["processed_orders"], 1)

    def test_empty_container_still_creates_summary(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "batch.json"
            batch = compare_all_orders(self.make_container([]), Mock(), Mock(), self.client,
                                       ["a", "b"], output=output)
            self.assertEqual(batch["processed_orders"], 0)
            self.assertTrue(output.exists())
        self.client.chat.completions.create.assert_not_called()
        self.pacing.assert_not_called()

    def test_round_pacing_waits_only_remaining_time(self):
        with patch("compare_model.time.monotonic", side_effect=[120, 160]), \
             patch("compare_model.time.sleep") as sleep:
            self.assertEqual(_wait_for_round(100, 60), 160)
        sleep.assert_called_once_with(40)
        with patch("compare_model.time.monotonic", return_value=180), \
             patch("compare_model.time.sleep") as sleep:
            self.assertEqual(_wait_for_round(100, 60), 180)
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
