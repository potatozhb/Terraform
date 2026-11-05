"""Build billing prompts using a target order and Azure AI Search evidence."""

import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HISTORY_EMBEDDING_MAX_TOKENS = 8000



def _field(order, *names):
    for name in names:
        if name in order and order[name] is not None:
            return order[name]
    return None


def _literal(value):
    """Escape an OData string literal, including IDs containing apostrophes."""
    return "'" + str(value).replace("'", "''") + "'"


def _json(value):
    return json.dumps(value, ensure_ascii=False, indent=2)


def build_system_prompt():
    """Return the supplied C# billing policy, with evidence-handling safeguards."""
    return Path(__file__).with_name("billing_system_prompt.txt").read_text(encoding="utf-8")


def build_order_search_text(order):
    """Prefer the C# DTO's search content; otherwise retain the supplied JSON."""
    content = _field(order, "OrderSearchContent", "order_search_content", "merged_text")
    return str(content).strip() if content else _json(order)


def build_history_embedding_input(order):
    """Embed service details rather than the full contract-heavy billing prompt.

    text-embedding-3-small uses cl100k_base. Token IDs avoid a decode/re-encode
    boundary changing the token count, and 8,000 leaves room below its limit.
    This limit affects only the similarity query, never the billing input.
    """
    import tiktoken

    content = _field(order, "OrderContentWithoutContract", "order_content_without_contract")
    if not isinstance(content, str) or not content.strip():
        parts = []
        for names in (("WorkOrderInlinesCombined", "work_order_inlines_combined"),
                      ("RemarksCombined", "remarks_combined")):
            value = _field(order, *names)
            if isinstance(value, str) and value.strip() and value.strip() not in parts:
                parts.append(value.strip())
        if not parts:
            for names in (("WorkOrderLines", "work_order_lines"), ("Remarks", "remarks")):
                value = _field(order, *names)
                if value:
                    parts.append(_json(value))
        content = "\n\n".join(parts) if parts else build_order_search_text(order)
    tokens = tiktoken.get_encoding("cl100k_base").encode_ordinary(content.strip())
    if not tokens:
        raise ValueError("The historical similarity query must contain nonempty order content.")
    return tokens[:HISTORY_EMBEDDING_MAX_TOKENS]


def build_user_prompt(order, rules=None, historical_orders=None):
    """Build an evidence-bearing user prompt without inventing missing contracts."""
    if not isinstance(order, dict) or not order:
        raise ValueError("The target work order must be a nonempty JSON object.")
    parts = ["Determine whether this completed work order should be charged to the customer."]
    # An indexed text document may embed contracts in merged_text. Absence of a
    # structured Contracts field does not prove those contracts are missing.
    if "Contracts" in order or "contracts" in order:
        contracts = _field(order, "Contracts", "contracts")
        if contracts:
            parts.append("Use the supplied contracts and contract items as authoritative evidence; validate applicability.")
        else:
            parts.append(
                "No current contract was retrieved. Treat current contract evidence as NONE. "
                "Do not infer a contract from remarks, entitlement descriptions, product IDs, "
                "service type, or history. Apply the deterministic policy in its required order."
            )
    else:
        parts.append(
            "Contract evidence, if present, is embedded in the target order below. "
            "Use only explicit contracts and contract items in that content. Do not infer coverage."
        )
    parts.extend([
        "TARGET WORK ORDER (JSON evidence):\n" + _json(order),
        "SPECIAL RULES (JSON evidence):\n"
        "These are retrieved candidates, not confirmed applicable rules. Validate triggers, "
        "region and all conditions; include all applicable conflicting rules.\n" + _json(list(rules or [])),
        "HISTORICAL ORDERS (JSON evidence, reference only):\n"
        "History may affect confidence only and must never change billing_status. "
        "similarity is cosine similarity (-1 to 1; higher is closer), not a probability. "
        "Use similarity in evidence citations; search_score is the raw Azure vector score.\n"
        + _json(list(historical_orders or [])[:3]),
        "Return JSON only, following the system prompt's output schema.",
    ])
    return "\n\n".join(parts)


def load_order_from_search(search_client, order_id):
    """Read every indexed chunk for the exact target ID, never a nearest match."""
    documents = list(search_client.search(
        search_text="*",
        filter=f"work_order_id eq {_literal(order_id)} and document_type ne 'special_billing_rule'",
        select=["document_id", "work_order_id", "store_id", "merged_text"],
    ))
    if not documents:
        raise ValueError(f"Work order {order_id!r} is not indexed. Supply --order-file or --blob-name.")
    return {
        "work_order_id": str(order_id),
        "store_id": documents[0].get("store_id"),
        "documents": [{k: v for k, v in doc.items() if not k.startswith("@")} for doc in documents],
        "OrderSearchContent": "\n\n".join(doc.get("merged_text") or "" for doc in documents),
    }


def load_order_from_blob(blob_client):
    """Read an existing UTF-8 JSON work-order blob; no writes or indexing."""
    order = json.loads(blob_client.download_blob().readall().decode("utf-8-sig"))
    if not isinstance(order, dict) or not order:
        raise ValueError("The order blob must contain a nonempty JSON object.")
    return order

def build_billing_prompts(order, search_client, embeddings_client, *, order_id=None, same_store=False):
    """Retrieve RAG evidence and return system/user strings and API messages.

    Retrieval errors propagate: an unavailable knowledge base must not silently
    look like a successful retrieval that found no rules or history.
    """
    if not isinstance(order, dict) or not order:
        raise ValueError("The target work order must be a nonempty JSON object.")
    embedded_id = _field(order, "work_order_id", "WorkOrderId", "workorderid", "order_id", "OrderId")
    if order_id is not None:
        if embedded_id is not None and str(embedded_id) != str(order_id):
            raise ValueError("The supplied order_id does not match the target work order.")
        order = {**order, "work_order_id": str(order_id)}
    try:
        from .billing_tools import retrieve_special_rules, retrieve_historical_orders
    except ImportError:
        from billing_tools import retrieve_special_rules, retrieve_historical_orders
    rules = retrieve_special_rules(search_client)
    history = retrieve_historical_orders(
        search_client, embeddings_client, order, order_id=order_id, same_store=same_store,
    )
    system_prompt = build_system_prompt()
    user_prompt = build_user_prompt(order, rules, history)
    return {
        "system_prompt": system_prompt, "user_prompt": user_prompt,
        "messages": [{"role": "system", "content": system_prompt},
                     {"role": "user", "content": user_prompt}],
        "special_rules": rules, "historical_orders": history,
    }


def main():
    from dotenv import load_dotenv
    from azure.core.credentials import AzureKeyCredential
    from azure.search.documents import SearchClient
    from openai import AzureOpenAI

    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--order-file", type=Path, help="Local JSON WorkOrderDto.")
    source.add_argument("--blob-name", help="Existing JSON order blob name.")
    parser.add_argument("--order-id", help="Target ID; also loads from Search when no file/blob is supplied.")
    parser.add_argument("--all-stores", action="store_true", help="Allow historical evidence from other stores.")
    parser.add_argument("--output", type=Path, help="Write prompts/evidence as JSON instead of stdout.")
    args = parser.parse_args()
    if not (args.order_file or args.blob_name or args.order_id):
        parser.error("Supply --order-file, --blob-name, or --order-id.")
    load_dotenv(ROOT / ".env")
    key = os.getenv("AZURE_SEARCH_QUERY_KEY") or os.getenv("AZURE_SEARCH_ADMIN_KEY")
    required = ["AZURE_SEARCH_ENDPOINT", "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY"]
    if args.blob_name:
        required.extend(["AZURE_STORAGE_CONNECTION_STRING", "AZURE_STORAGE_CONTAINER_NAME"])
    missing = [name for name in required if not os.getenv(name, "").strip()]
    if not key:
        missing.append("AZURE_SEARCH_QUERY_KEY (or AZURE_SEARCH_ADMIN_KEY)")
    if missing:
        raise ValueError("Set these values in PythonClient/.env: " + ", ".join(missing))
    with ExitStack() as stack:
        search = stack.enter_context(SearchClient(
            endpoint=os.environ["AZURE_SEARCH_ENDPOINT"],
            index_name=os.getenv("AZURE_SEARCH_INDEX_NAME", "billing-knowledge-index"),
            credential=AzureKeyCredential(key),
        ))
        embeddings = stack.enter_context(AzureOpenAI(
            azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
            api_key=os.environ["AZURE_OPENAI_API_KEY"],
            api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-01"),
        ))
        if args.order_file:
            order = json.loads(args.order_file.read_text(encoding="utf-8-sig"))
        elif args.blob_name:
            from azure.storage.blob import BlobClient
            blob = stack.enter_context(BlobClient.from_connection_string(
                os.environ["AZURE_STORAGE_CONNECTION_STRING"],
                container_name=os.environ["AZURE_STORAGE_CONTAINER_NAME"], blob_name=args.blob_name,
            ))
            order = load_order_from_blob(blob)
        else:
            order = load_order_from_search(search, args.order_id)
        result = build_billing_prompts(
            order, search, embeddings, order_id=args.order_id, same_store=not args.all_stores,
        )
    output = _json(result)
    if args.output:
        args.output.write_text(output + "\n", encoding="utf-8")
    else:
        print(output)


if __name__ == "__main__":
    main()
