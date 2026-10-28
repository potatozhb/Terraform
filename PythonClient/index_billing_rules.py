"""Upload the supplied special billing rules to the Terraform-managed Search index."""

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIMENSIONS = 1536


def build_documents(rules):
    documents = []
    seen = set()
    for rule in rules:
        rule_id = rule["rule_id"]
        if rule_id in seen:
            raise ValueError(f"Duplicate rule ID: {rule_id}")
        seen.add(rule_id)
        text = "\n".join([
            f"Rule ID: {rule_id}",
            f"Rule name: {rule['rule_name']}",
            f"Rule type: {rule['rule_type']}",
            f"Region: {rule['region']}",
            "Trigger phrases: " + "; ".join(rule["trigger_phrases"]),
            "Conditions (must be evaluated in context):",
            *[f"- {condition}" for condition in rule["conditions"]],
            f"Action: {rule['action']}",
            f"Reason: {rule['reason']}",
        ])
        documents.append({
            "document_id": rule_id,
            "document_name": rule["rule_name"],
            "document_type": "special_billing_rule",
            "chunk_number": "1",
            "rule_type": rule["rule_type"],
            "rule_region": rule["region"],
            "rule_action": rule["action"],
            "rule_reason": rule["reason"],
            "merged_text": text,
        })
    return documents


def upload(documents):
    from dotenv import load_dotenv
    from azure.core.credentials import AzureKeyCredential
    from azure.search.documents import SearchClient
    from openai import AzureOpenAI

    load_dotenv(ROOT / ".env")
    required = (
        "AZURE_SEARCH_ENDPOINT", "AZURE_SEARCH_ADMIN_KEY",
        "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY",
    )
    missing = [name for name in required if not os.getenv(name, "").strip()]
    if missing:
        raise ValueError("Set these values in PythonClient/.env: " + ", ".join(missing))

    with AzureOpenAI(
        azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
        api_key=os.environ["AZURE_OPENAI_API_KEY"],
        api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-01"),
    ) as embeddings, SearchClient(
        endpoint=os.environ["AZURE_SEARCH_ENDPOINT"],
        index_name=os.getenv("AZURE_SEARCH_INDEX_NAME", "billing-knowledge-index"),
        credential=AzureKeyCredential(os.environ["AZURE_SEARCH_ADMIN_KEY"]),
    ) as search:
        for start in range(0, len(documents), 16):
            batch = documents[start:start + 16]
            response = embeddings.embeddings.create(
                model=os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "text-embedding-3-small-code-vector"),
                input=[document["merged_text"] for document in batch],
                dimensions=DIMENSIONS,
            )
            vectors = {item.index: item.embedding for item in response.data}
            if set(vectors) != set(range(len(batch))):
                raise ValueError("Embedding response did not include every document.")
            for index, document in enumerate(batch):
                vector = vectors[index]
                if len(vector) != DIMENSIONS:
                    raise ValueError(f"Expected {DIMENSIONS} dimensions for {document['document_id']}.")
                document["content_vector"] = vector
            results = search.merge_or_upload_documents(documents=batch)
            failures = [result for result in results if not result.succeeded]
            if failures:
                details = "; ".join(f"{result.key}: {result.error_message}" for result in failures)
                raise RuntimeError("Some documents failed to upload: " + details)
            print(f"Uploaded {len(results)} rules ({start + len(batch)}/{len(documents)}).")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=ROOT / "data" / "special_billing_rules.json")
    parser.add_argument("--dry-run", action="store_true", help="Print documents without embeddings or Azure calls.")
    args = parser.parse_args()
    rules = json.loads(args.data.read_text(encoding="utf-8-sig"))
    if not isinstance(rules, list) or not rules:
        raise ValueError("The data file must contain a nonempty JSON list of rules.")
    documents = build_documents(rules)
    if args.dry_run:
        print(json.dumps(documents, indent=2, ensure_ascii=False))
    else:
        upload(documents)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError, RuntimeError, ImportError) as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)
