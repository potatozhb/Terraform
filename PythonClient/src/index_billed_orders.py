"""Index stored billed orders with work-only vectors and full contract evidence."""

import argparse
from contextlib import ExitStack
import json
import math
import os
from pathlib import Path

try:
    from .billing_prompts import ROOT, _field, _literal, build_history_embedding_input, load_order_from_blob
    from .billing_tools import DIMENSIONS
except ImportError:
    from billing_prompts import ROOT, _field, _literal, build_history_embedding_input, load_order_from_blob
    from billing_tools import DIMENSIONS


def billing_status(order):
    """Use the stored outcome, never a generated model prediction."""
    value = _field(order, "BillingStatus", "billing_status", "BillStatus", "bill_status")
    if type(value) not in (int, str):
        return None
    return {"1": "BILLABLE", "BILLABLE": "BILLABLE", "0": "FREE", "FREE": "FREE"}.get(
        str(value).strip().upper())


def build_document(order, source_name=""):
    identifier = _field(order, "work_order_id", "WorkOrderId", "workorderid", "order_id", "OrderId")
    if identifier is None or not str(identifier).strip():
        raise ValueError("An indexed order requires a work_order_id.")
    identifier = str(identifier).strip()
    status = billing_status(order)
    if status is None:
        raise ValueError(f"Order {identifier} has no recognized stored billing status.")
    store = _field(order, "store_id", "StoreId", "StoreID")
    return {
        "document_id": f"{identifier}-1",
        "document_name": source_name or identifier,
        "document_type": "Order",
        "work_order_id": identifier,
        "store_id": str(store) if store is not None else None,
        "billing_status": status,
        "chunk_number": "1",
        # Preserve all supplied contract evidence; only work content is embedded.
        "merged_text": json.dumps(order, ensure_ascii=False, indent=2),
    }


def split_order_chunks(text, max_bytes=30000):
    """Respect the existing filterable string field's UTF-8 term limit."""
    remaining = text.encode("utf-8")
    parts = []
    while remaining:
        part = remaining[:max_bytes].decode("utf-8", errors="ignore")
        parts.append(part)
        remaining = remaining[len(part.encode("utf-8")):]
    return parts


def upload_billed_orders(orders, search_client, embeddings_client, *, deployment=None, dry_run=False):
    """Stream (source_name, order) pairs, upserting evidence chunks per order.

    Document keys use <order ID>-<part number>, starting at 1.
    Legacy or stale chunks are removed after all replacement chunks upload.
    Dry runs validate work content without calling Search or the embedding API.
    """
    counts = {"scanned": 0, "skipped": 0, "validated": 0, "uploaded": 0}
    seen = set()
    for source_name, order in orders:
        counts["scanned"] += 1
        if not isinstance(order, dict) or not order:
            raise ValueError(f"{source_name}: expected a nonempty order object.")
        status = billing_status(order)
        if status != "BILLABLE" and status != "FREE":
            counts["skipped"] += 1
            continue
        document = build_document(order, source_name)
        identifier = document["work_order_id"]
        if identifier in seen:
            raise ValueError(f"Duplicate source order ID: {identifier}")
        seen.add(identifier)
        tokens = build_history_embedding_input(order)
        parts = split_order_chunks(document["merged_text"])
        counts["validated"] += 1
        if dry_run:
            print(f"Would upload order {identifier}: {status}, {len(tokens)} work tokens.")
            continue
        existing = list(search_client.search(
            search_text="*", filter=f"work_order_id eq {_literal(identifier)}",
            select=["document_id", "document_type"],
        ))
        if any(item.get("document_type") == "special_billing_rule" for item in existing):
            raise ValueError(f"Order {identifier} conflicts with a special billing rule.")
        expected_keys = {f"{identifier}-{part_number}" for part_number in range(1, len(parts) + 1)}
        obsolete = [{"document_id": item["document_id"]} for item in existing
                    if item["document_id"] not in expected_keys]
        response = embeddings_client.embeddings.create(
            model=deployment or os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT")
            or "text-embedding-3-small-code-vector",
            input=tokens, dimensions=DIMENSIONS,
        )
        if len(response.data) != 1:
            raise ValueError(f"Order {identifier}: expected one embedding.")
        vector = response.data[0].embedding
        if len(vector) != DIMENSIONS or any(not math.isfinite(value) for value in vector):
            raise ValueError(f"Order {identifier}: invalid {DIMENSIONS}-dimensional embedding.")
        document["content_vector"] = vector
        for index, part in enumerate(parts, 1):
            chunk = dict(document, document_id=f"{identifier}-{index}",
                         merged_text=part, chunk_number=str(index))
            results = search_client.merge_or_upload_documents(documents=[chunk])

            if len(results) != 1 or not results[0].succeeded:
                details = "; ".join(f"{result.key}: {result.error_message}" for result in results)
                raise RuntimeError(f"Order {identifier} upload failed: {details}")
        # Keep the old evidence until every replacement part is safely uploaded.
        for start in range(0, len(obsolete), 1000):
            batch = obsolete[start:start + 1000]
            results = search_client.delete_documents(documents=batch)
            if len(results) != len(batch) or any(not result.succeeded for result in results):
                details = "; ".join(f"{result.key}: {result.error_message}"
                                    for result in results if not result.succeeded)
                raise RuntimeError(f"Order {identifier} obsolete chunk cleanup failed: {details}")
        counts["uploaded"] += 1
        print(f"Uploaded order {identifier}: {status}, {len(parts)} order chunks.", flush=True)
    return counts


def main():
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--order-file", type=Path, help="A JSON order or list of orders.")
    source.add_argument("--blob-name", help="One JSON blob; otherwise scan the configured container.")
    parser.add_argument("--prefix", default=os.getenv("AZURE_STORAGE_BLOB_PREFIX", ""))
    parser.add_argument("--include-free", action="store_true", help="Also index stored FREE / 0 orders.")
    parser.add_argument("--dry-run", action="store_true", help="Validate inputs without embedding or index writes.")
    args = parser.parse_args()
    with ExitStack() as stack:
        if args.order_file:
            data = json.loads(args.order_file.read_text(encoding="utf-8-sig"))
            orders = ((args.order_file.name, order) for order in (data if isinstance(data, list) else [data]))
        else:
            from azure.storage.blob import ContainerClient
            container_name = required("AZURE_STORAGE_CONTAINER_NAME")
            connection = os.getenv("AZURE_STORAGE_CONNECTION_STRING", "").strip()
            if connection:
                container = ContainerClient.from_connection_string(connection, container_name)
            else:
                container = ContainerClient(
                    f"https://{required('AZURE_STORAGE_ACCOUNT_NAME')}.blob.core.windows.net",
                    container_name, credential=required("AZURE_STORAGE_ACCOUNT_KEY"),
                )
            container = stack.enter_context(container)
            def blob_orders():
                names = [args.blob_name] if args.blob_name else (
                    item.name for item in container.list_blobs(name_starts_with=args.prefix or None)
                    if item.name.lower().endswith(".json"))
                for name in names:
                    with container.get_blob_client(name) as blob:
                        yield name, load_order_from_blob(blob)
            orders = blob_orders()
        search = embeddings = None
        if not args.dry_run:
            from azure.core.credentials import AzureKeyCredential
            from azure.search.documents import SearchClient
            from openai import AzureOpenAI
            search = stack.enter_context(SearchClient(
                required("AZURE_SEARCH_ENDPOINT"), required("AZURE_SEARCH_INDEX_NAME"),
                AzureKeyCredential(required("AZURE_SEARCH_ADMIN_KEY")),
            ))
            embeddings = stack.enter_context(AzureOpenAI(
                azure_endpoint=required("AZURE_OPENAI_ENDPOINT"),
                api_key=required("AZURE_OPENAI_API_KEY"),
                api_version=os.getenv("AZURE_OPENAI_API_VERSION") or "2024-02-01",
            ))
        print(json.dumps(upload_billed_orders(
            orders, search, embeddings, dry_run=args.dry_run,
        )))


def required(name):
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"Set {name} in PythonClient/.env.")
    return value


if __name__ == "__main__":
    main()
