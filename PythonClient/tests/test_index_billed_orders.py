import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from index_billed_orders import billing_status, split_evidence, upload_billed_orders


class IndexBilledOrdersTests(unittest.TestCase):
    def setUp(self):
        self.order = {"WorkOrderId": "123", "BillingStatus": 1,
                      "OrderContentWithoutContract": "Printer repair",
                      "Contracts": [{"coverage": "Labor covered"}]}
        self.search, self.embeddings = Mock(), Mock()
        self.search.search.return_value = [{"document_id": "existing-key", "document_type": "Order"}]
        self.search.merge_or_upload_documents.return_value = [SimpleNamespace(succeeded=True)]
        self.embeddings.embeddings.create.return_value = SimpleNamespace(
            data=[SimpleNamespace(embedding=[0.1] * 1536)])

    def test_upsert_preserves_contracts_and_embeds_work_only(self):
        import tiktoken
        counts = upload_billed_orders([("order.json", self.order)], self.search, self.embeddings)
        document = self.search.merge_or_upload_documents.call_args.kwargs["documents"][0]
        self.assertEqual(document["document_id"], "existing-key")
        self.assertEqual(document["billing_status"], "BILLABLE")
        self.assertEqual(json.loads(document["merged_text"])["Contracts"], self.order["Contracts"])
        tokens = self.embeddings.embeddings.create.call_args.kwargs["input"]
        self.assertEqual(tiktoken.get_encoding("cl100k_base").decode(tokens), "Printer repair")
        self.assertEqual(counts["uploaded"], 1)

    def test_only_stored_billable_status_is_selected_by_default(self):
        orders = [(str(value), dict(self.order, BillingStatus=value)) for value in (0, "Free", None, True, "REVIEW_REQUIRED")]
        counts = upload_billed_orders(orders, self.search, self.embeddings)
        self.assertEqual(counts["skipped"], 5)
        self.search.search.assert_not_called()
        self.assertEqual(billing_status({"BillingStatus": "Billable"}), "BILLABLE")

    def test_dry_run_and_explicit_free_option(self):
        counts = upload_billed_orders([("free.json", dict(self.order, BillingStatus=0))],
                                     self.search, self.embeddings, include_free=True, dry_run=True)
        self.assertEqual(counts["validated"], 1)
        self.search.search.assert_not_called()
        self.embeddings.embeddings.create.assert_not_called()

    def test_multiple_existing_chunks_are_not_overwritten(self):
        self.search.search.return_value *= 2
        with self.assertRaisesRegex(ValueError, "multiple indexed chunks"):
            upload_billed_orders([("order.json", self.order)], self.search, self.embeddings)
        self.search.merge_or_upload_documents.assert_not_called()

    def test_upload_failure_is_reported(self):
        self.search.merge_or_upload_documents.return_value = [
            SimpleNamespace(succeeded=False, key="existing-key", error_message="Rejected")]
        with self.assertRaisesRegex(RuntimeError, "Rejected"):
            upload_billed_orders([("order.json", self.order)], self.search, self.embeddings)

    def test_large_unicode_evidence_is_preserved_in_bounded_chunks(self):
        text = "Printer repair. 故障 🔧 " * 6000
        parts = split_evidence(text)
        self.assertEqual("".join(parts), text)
        self.assertTrue(all(len(part.encode("utf-8")) <= 30000 for part in parts))
        self.search.search.return_value = []
        self.order["Contracts"] = [{"coverage": text}]
        upload_billed_orders([("order.json", self.order)], self.search, self.embeddings)
        chunks = [call.kwargs["documents"][0]
                  for call in self.search.merge_or_upload_documents.call_args_list]
        self.assertEqual(json.loads("".join(chunk["merged_text"] for chunk in chunks)), self.order)
        self.assertEqual(len({chunk["document_id"] for chunk in chunks}), len(chunks))
        self.embeddings.embeddings.create.assert_called_once()


if __name__ == "__main__":
    unittest.main()
