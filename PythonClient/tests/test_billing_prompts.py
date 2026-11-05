"""Offline checks for evidence isolation and prompt construction."""

import json
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from billing_prompts import (build_billing_prompts, build_user_prompt,
                             build_history_embedding_input, HISTORY_EMBEDDING_MAX_TOKENS,
                             load_order_from_blob, load_order_from_search)
from billing_tools import retrieve_historical_orders


class BillingPromptTests(unittest.TestCase):
    def setUp(self):
        self.order = {"WorkOrderId": "current'1", "StoreId": "store'2",
                      "OrderSearchContent": "Replaced printer battery", "Contracts": []}
        self.search = Mock()
        self.embeddings = Mock()
        self.embeddings.embeddings.create.return_value = SimpleNamespace(
            data=[SimpleNamespace(embedding=[0.1] * 1536)])

    def test_history_excludes_target_deduplicates_and_preserves_scores(self):
        self.search.search.return_value = [
            {"work_order_id": identifier, "document_id": str(index), "@search.score": 0.8}
            for index, identifier in enumerate(["current'1", "old1", "old1", "old2", "old3", "old4"])
        ]
        history = retrieve_historical_orders(self.search, self.embeddings, self.order)
        self.assertEqual([h["work_order_id"] for h in history], ["old1", "old2", "old3"])
        self.assertEqual(history[0]["search_score"], 0.8)
        self.assertAlmostEqual(history[0]["similarity"], 0.75)
        self.assertEqual(history[0]["similarity_metric"], "cosine")
        query = self.search.search.call_args.kwargs
        self.assertIn("work_order_id ne 'current''1'", query["filter"])
        self.assertIn("store_id eq 'store''2'", query["filter"])
        self.assertIn("billing_status eq 'FREE'", query["filter"])
        self.assertEqual(query["vector_filter_mode"], "preFilter")
        self.assertEqual(query["vector_queries"][0].fields, "content_vector")
        self.assertTrue(query["vector_queries"][0].exhaustive)
        self.assertIsNone(query["search_text"])
        self.assertEqual(query["top"], 3)

    def test_history_embedding_is_token_bounded_for_long_unicode_input(self):
        order = {"WorkOrderId": "1", "OrderSearchContent": "Printer 故障 🔧 " * 10000}
        original = order.copy()
        tokens = build_history_embedding_input(order)
        self.assertEqual(len(tokens), HISTORY_EMBEDDING_MAX_TOKENS)
        self.assertTrue(all(isinstance(token, int) for token in tokens))
        self.assertEqual(order, original)
        self.search.search.return_value = []
        retrieve_historical_orders(self.search, self.embeddings, order)
        self.assertEqual(self.embeddings.embeddings.create.call_args.kwargs["input"], tokens)

    def test_history_embedding_prefers_work_details_over_contracts(self):
        import tiktoken
        encoding = tiktoken.get_encoding("cl100k_base")
        order = {"OrderContentWithoutContract": "Printer troubleshooting",
                 "OrderSearchContent": "Contract conditions " * 20000, "Contracts": [{"text": "contract"}]}
        tokens = build_history_embedding_input(order)
        self.assertEqual(encoding.decode(tokens), "Printer troubleshooting")
        del order["OrderContentWithoutContract"]
        order.update(WorkOrderInlinesCombined="Printer fixed", RemarksCombined="Reconfigured routing")
        decoded = encoding.decode(build_history_embedding_input(order))
        self.assertIn("Reconfigured routing", decoded)
        self.assertNotIn("Contract conditions", decoded)

    def test_closest_distinct_orders_when_nearest_chunks_belong_to_one_order(self):
        self.search.search.side_effect = [
            [{"work_order_id": "a", "document_id": "a2", "@search.score": 0.8},
             {"work_order_id": "a", "document_id": "a1", "@search.score": 0.95},
             {"work_order_id": "a", "document_id": "a3", "@search.score": 0.9}],
            [{"work_order_id": "c", "document_id": "c1", "@search.score": 0.7},
             {"work_order_id": "b", "document_id": "b1", "@search.score": 0.75}],
        ]
        history = retrieve_historical_orders(self.search, self.embeddings, self.order)
        self.assertEqual([h["work_order_id"] for h in history], ["a", "b", "c"])
        self.assertEqual(history[0]["document_id"], "a1")
        second = self.search.search.call_args_list[1].kwargs
        self.assertIn("work_order_id ne 'a'", second["filter"])
        self.assertEqual(second["top"], 2)

    def test_fewer_than_three_orders_and_invalid_score(self):
        self.search.search.side_effect = [
            [{"work_order_id": "a", "@search.score": 0.9}], [],
        ]
        self.assertEqual(len(retrieve_historical_orders(self.search, self.embeddings, self.order)), 1)
        self.search.search.side_effect = None
        self.search.search.return_value = [{"work_order_id": "a", "@search.score": 0.02}]
        with self.assertRaisesRegex(ValueError, "valid cosine search score"):
            retrieve_historical_orders(self.search, self.embeddings, self.order)

    def test_all_stores_and_bad_embedding(self):
        self.search.search.return_value = []
        retrieve_historical_orders(self.search, self.embeddings, self.order, same_store=False)
        self.assertNotIn("store_id eq", self.search.search.call_args.kwargs["filter"])
        self.embeddings.embeddings.create.return_value.data[0].embedding = [0.1]
        with self.assertRaisesRegex(ValueError, "1536"):
            retrieve_historical_orders(self.search, self.embeddings, self.order)

    def test_missing_id_does_not_retrieve_self_as_history(self):
        with self.assertRaisesRegex(ValueError, "target work_order_id"):
            retrieve_historical_orders(self.search, self.embeddings, {"merged_text": "text"})
        self.embeddings.embeddings.create.assert_not_called()

    def test_contract_absence_differs_from_embedded_contracts(self):
        self.assertIn("contract evidence as NONE", build_user_prompt(self.order))
        self.assertNotIn("contract evidence as NONE", build_user_prompt({
            "merged_text": "Contract start: 2026-01-01; Contract end: 2026-12-31"}))

    def test_all_rules_are_included_and_errors_propagate(self):
        rules = [{"document_id": "rule1", "rule_action": "FreeOverride"},
                 {"document_id": "rule2", "rule_action": "BillableOverride"}]
        self.search.search.side_effect = [rules, []]
        prompts = build_billing_prompts(self.order, self.search, self.embeddings)
        self.assertEqual(prompts["special_rules"], rules)
        self.assertIn("rule2", prompts["user_prompt"])
        self.assertEqual([m["role"] for m in prompts["messages"]], ["system", "user"])
        self.assertNotIn("top", self.search.search.call_args_list[0].kwargs)
        self.assertIn("Project ID > 0", prompts["system_prompt"])
        self.assertIn("BillableOverride and FreeOverride", prompts["system_prompt"])
        self.search.search.side_effect = RuntimeError("Search unavailable")
        with self.assertRaisesRegex(RuntimeError, "Search unavailable"):
            build_billing_prompts(self.order, self.search, self.embeddings)

    def test_exact_order_load_preserves_all_chunks(self):
        self.search.search.return_value = [
            {"document_id": "a", "merged_text": "order", "store_id": "1"},
            {"document_id": "b", "merged_text": "contract", "store_id": "1"},
        ]
        order = load_order_from_search(self.search, "123")
        self.assertEqual(order["OrderSearchContent"], "order\n\ncontract")
        self.assertEqual(len(order["documents"]), 2)
        self.assertIn("contract", build_user_prompt(order))
        self.search.search.return_value = []
        with self.assertRaisesRegex(ValueError, "not indexed"):
            load_order_from_search(self.search, "123")

    def test_explicit_id_is_in_prompt_and_mismatched_id_is_rejected(self):
        self.search.search.side_effect = [[], []]
        result = build_billing_prompts({"Contracts": []}, self.search, self.embeddings, order_id="123")
        self.assertIn('"work_order_id": "123"', result["user_prompt"])
        with self.assertRaisesRegex(ValueError, "does not match"):
            build_billing_prompts(self.order, self.search, self.embeddings, order_id="different")

    def test_blob_load_retains_contracts_and_rejects_array(self):
        blob = Mock()
        blob.download_blob.return_value.readall.return_value = json.dumps(self.order).encode()
        self.assertEqual(load_order_from_blob(blob), self.order)
        blob.download_blob.return_value.readall.return_value = b"[]"
        with self.assertRaisesRegex(ValueError, "JSON object"):
            load_order_from_blob(blob)


if __name__ == "__main__":
    unittest.main()
