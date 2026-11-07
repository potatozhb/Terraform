"""Compare Azure OpenAI chat deployments on the same stored work order and RAG evidence."""

import argparse
from contextlib import ExitStack
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import time

try:
    from .billing_prompts import ROOT, _field, load_order_from_blob
except ImportError:
    from billing_prompts import ROOT, _field, load_order_from_blob


try:
    from .billing_tools import BillingTools, TOOL_DEFINITIONS, build_tool_messages
    from .billing_responses import run_responses
except ImportError:
    from billing_tools import BillingTools, TOOL_DEFINITIONS, build_tool_messages
    from billing_responses import run_responses


# Snapshot of AiBillingDecision/environments/dev/main.tf,
# shared/variables.tf and aiDecision/{main,variables}.tf. No credentials.
TERRAFORM_DEFAULTS = {
    "storage_account": "aibillingstorage69fe",
    "storage_container": "foundry",
    "search_endpoint": "https://ai-billing-search-69fe.search.windows.net",
    "search_index": "billing-knowledge-index",
    "openai_endpoint": "https://ai-billing-foundry-69fe.cognitiveservices.azure.com",
    "models": ["ai-billing-foundry-6.1-sol", "ai-billing-foundry-5.6-sol"],
}


# This deployment requires Responses for reasoning with function tools.
DEPLOYMENT_REQUEST_OPTIONS = {
    "ai-billing-foundry-6.1-sol": {"reasoning": {"effort": "low"}},
}
RESPONSES_DEPLOYMENTS = {"ai-billing-foundry-6.1-sol"}


def _setting(name, fallback=None):
    """Empty .env template values should not erase usable defaults."""
    return os.getenv(name, "").strip() or fallback


def build_argument_parser():
    """Command line overrides environment settings, which override Terraform defaults."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--blob-name", default=_setting("AZURE_STORAGE_BLOB_NAME"),
                        help="Compare just this JSON blob; otherwise process all JSON blobs.")
    source.add_argument("--all-orders", action="store_true",
                        help="Process all JSON blobs, overriding AZURE_STORAGE_BLOB_NAME.")
    parser.add_argument("--prefix", default=_setting("AZURE_STORAGE_BLOB_PREFIX", ""),
                        help="Only list blobs whose names start with this prefix.")
    parser.add_argument("--limit", type=int, help="Optional maximum number of JSON blobs to process.")
    parser.add_argument("--order-id", help="Target ID when it is absent from the blob JSON.")
    parser.add_argument("--models", nargs="+",
                        default=_setting("AZURE_OPENAI_COMPARE_DEPLOYMENTS", ",".join(TERRAFORM_DEFAULTS["models"])).split(","),
                        help="Azure chat deployment names (at least two).")
    for argument, variable in (
        ("storage_account", "AZURE_STORAGE_ACCOUNT_NAME"),
        ("storage_container", "AZURE_STORAGE_CONTAINER_NAME"),
        ("search_endpoint", "AZURE_SEARCH_ENDPOINT"),
        ("search_index", "AZURE_SEARCH_INDEX_NAME"),
        ("openai_endpoint", "AZURE_OPENAI_ENDPOINT"),
    ):
        parser.add_argument("--" + argument.replace("_", "-"),
                            default=_setting(variable, TERRAFORM_DEFAULTS[argument]),
                            help=f"Overrides {variable}.")
    parser.add_argument("--chat-endpoint", default=_setting("AZURE_OPENAI_CHAT_ENDPOINT"),
                        help="Chat endpoint; defaults to --openai-endpoint.")
    parser.add_argument("--all-stores", action="store_true", help="Retrieve history from any store.")
    parser.add_argument("--max-completion-tokens", type=int, default=4096)
    parser.add_argument("--max-tool-rounds", type=int, default=4,
                        help="Maximum retrieval turns per model, followed by a final JSON-only turn.")
    parser.add_argument("--timeout", type=float, default=120, help="Timeout in seconds for each chat request.")
    parser.add_argument("--round-interval", type=float, default=60,
                        help="Minimum seconds between batch order round starts; one round compares all models.")
    parser.add_argument("--output", type=Path, default=ROOT / "compare_results.json")
    return parser


def _validate_decision(decision, order_id):
    """Validate comparison fields; JSON mode alone does not enforce the policy schema."""
    if not isinstance(decision, dict):
        raise ValueError("The response must be a JSON object.")
    if str(decision.get("order_id", "")) != str(order_id):
        raise ValueError("Response order_id does not match the target order.")
    if decision.get("billing_status") not in {"BILLABLE", "FREE", "REVIEW_REQUIRED"}:
        raise ValueError("Response billing_status is missing or invalid.")
    confidence = decision.get("confidence")
    if (isinstance(confidence, bool) or not isinstance(confidence, (int, float))
            or not math.isfinite(confidence) or not 0 <= confidence <= 1):
        raise ValueError("Response confidence must be a number between 0 and 1.")
    if not isinstance(decision.get("summary"), str):
        raise ValueError("Response summary must be a string.")
    evidence = decision.get("usedEvidence")
    if not isinstance(evidence, dict) or any(
        not isinstance(evidence.get(key), list) for key in ("contracts", "specialRules", "history")
    ):
        raise ValueError("Response usedEvidence must include contracts, specialRules, and history lists.")
    reasoning = decision.get("reasoning")
    if not isinstance(reasoning, dict) or any(
        not isinstance(reasoning.get(key), str)
        for key in ("contractReason", "specialRuleReason", "historyReason")
    ):
        raise ValueError("Response reasoning must include contractReason, specialRuleReason, and historyReason strings.")


def _run_model(chat_client, model, row, retrieval_tools, *, max_completion_tokens, max_tool_rounds):
    """Execute bounded assistant/tool turns, retaining conversation and total usage."""
    completed_tools = set()
    row["completed_tools"] = []
    for turn in range(max_tool_rounds + 1):
        row["status"] = "error"
        tool_choice = retrieval_tools.tool_choice(
            turn, max_tool_rounds, completed_tools=completed_tools,
        )
        response = chat_client.chat.completions.create(
            model=model, messages=deepcopy(row["messages"]),
            tools=deepcopy(TOOL_DEFINITIONS),
            tool_choice=tool_choice,
            response_format={"type": "json_object"}, max_completion_tokens=max_completion_tokens,
            **row["request_options"],
        )
        row["response_model"] = response.model
        usage = response.usage.model_dump(mode="json") if response.usage else None
        row["chat_calls"].append({"model": response.model, "usage": usage})
        if usage is not None:
            if row["usage"] is None:
                row["usage"] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
            for key in row["usage"]:
                row["usage"][key] += usage.get(key) or 0
        row["status"] = "invalid_response"
        if not response.choices:
            raise ValueError("The deployment returned no choices.")
        choice = response.choices[0]
        message = choice.message
        row["finish_reason"] = choice.finish_reason
        row["raw_response"] = message.content
        if message.refusal:
            raise ValueError("Model refusal: " + message.refusal)
        calls = message.tool_calls or []
        if isinstance(tool_choice, dict) and not any(
            call.function.name == tool_choice["function"]["name"] for call in calls
        ):
            raise ValueError(f"Model did not call the required {tool_choice['function']['name']} tool.")
        if calls:
            if turn == max_tool_rounds:
                raise ValueError("Model exceeded the maximum tool-call rounds.")
            if choice.finish_reason != "tool_calls":
                raise ValueError("Incomplete tool-call response: " + str(choice.finish_reason))
            row["messages"].append({
                "role": "assistant", "content": message.content,
                "tool_calls": [{"id": call.id, "type": "function", "function": {
                    "name": call.function.name, "arguments": call.function.arguments,
                }} for call in calls],
            })
            for call in calls:
                trace = {"id": call.id, "name": call.function.name,
                         "arguments": call.function.arguments, "result": None, "error": None}
                row["tool_calls"].append(trace)
                start = time.perf_counter()
                try:
                    result, cached = retrieval_tools.execute(call.function.name, call.function.arguments)
                    trace.update(result=result, cached=cached)
                except Exception as error:
                    # Never substitute an empty successful retrieval for a failed tool.
                    trace["error"] = f"{type(error).__name__}: {error}"
                    row["status"] = "tool_error"
                    raise
                finally:
                    trace["elapsed_seconds"] = round(time.perf_counter() - start, 3)
                row["messages"].append({"role": "tool", "tool_call_id": call.id,
                                        "content": json.dumps(result, ensure_ascii=False)})
                completed_tools.add(call.function.name)
                row["completed_tools"] = sorted(completed_tools)
            continue
        if choice.finish_reason != "stop":
            raise ValueError(f"Incomplete response: finish_reason={choice.finish_reason!r}.")
        retrieval_tools.validate_required_tools(completed_tools)
        row["messages"].append({"role": "assistant", "content": message.content})
        return


def compare_models(order, search_client, embeddings_client, chat_client, models, *,
                   order_id=None, same_store=False, max_completion_tokens=4096, max_tool_rounds=4,
                   responses_client=None):
    """Compare models using the same order-only input and lazy retrieval tools."""
    from azure.core.exceptions import AzureError
    from openai import OpenAIError

    models = list(dict.fromkeys(name.strip() for name in models if name.strip()))
    if len(models) < 2:
        raise ValueError("Supply at least two distinct model deployment names.")
    if max_completion_tokens <= 0:
        raise ValueError("max_completion_tokens must be greater than zero.")
    if max_tool_rounds < 0:
        raise ValueError("max_tool_rounds must be zero or greater.")
    if not isinstance(order, dict) or not order:
        raise ValueError("The target work order must be a nonempty JSON object.")
    embedded_id = _field(order, "work_order_id", "WorkOrderId", "workorderid", "order_id", "OrderId")
    if order_id is not None and embedded_id is not None and str(order_id) != str(embedded_id):
        raise ValueError("The supplied order_id does not match the target work order.")
    order_id = order_id if order_id is not None else embedded_id
    if order_id is None or not str(order_id).strip():
        raise ValueError("The target work_order_id is required.")
    order = {**order, "work_order_id": str(order_id)}
    messages = build_tool_messages(order)
    prompts = {"system_prompt": messages[0]["content"], "user_prompt": messages[1]["content"],
               "messages": messages}
    retrieval_tools = BillingTools(order, search_client, embeddings_client,
                                   order_id=order_id, same_store=same_store)
    fingerprint = hashlib.sha256(
        json.dumps(messages, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    report = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "order_id": str(order_id), "prompt_sha256": fingerprint,
        "work_order_billing_status": stored_billing_status(order),
        "request_settings": {"max_completion_tokens": max_completion_tokens,
                             "response_format": {"type": "json_object"},
                             "max_tool_rounds": max_tool_rounds},
        "tools": deepcopy(TOOL_DEFINITIONS), "prompts": prompts, "results": [],
    }
    for model in models:
        row = {
            "deployment": model, "status": "error", "response_model": None,
            "api": "responses" if model in RESPONSES_DEPLOYMENTS else "chat_completions",
            "request_options": deepcopy(DEPLOYMENT_REQUEST_OPTIONS.get(model, {})),
            "elapsed_seconds": None, "usage": None, "finish_reason": None,
            "decision": None, "raw_response": None, "error": None,
            "tool_calls": [], "chat_calls": [], "messages": deepcopy(messages), "completed_tools": [],
        }
        start = time.perf_counter()
        try:
            if model in RESPONSES_DEPLOYMENTS:
                if responses_client is None:
                    raise ValueError("This deployment requires a Responses client using the Azure /openai/v1/ endpoint.")
                run_responses(responses_client, model, row, retrieval_tools,
                              max_completion_tokens=max_completion_tokens, max_tool_rounds=max_tool_rounds)
            else:
                _run_model(chat_client, model, row, retrieval_tools,
                           max_completion_tokens=max_completion_tokens, max_tool_rounds=max_tool_rounds)
            decision = json.loads(row["raw_response"] or "")
            _validate_decision(decision, order_id)
            row["decision"] = decision
            row["status"] = "success"
        except (AzureError, OpenAIError, ValueError, TypeError, RuntimeError) as error:
            row["error"] = f"{type(error).__name__}: {error}"
        finally:
            row["elapsed_seconds"] = round(time.perf_counter() - start, 3)
        report["results"].append(row)

    successful = [row for row in report["results"] if row["status"] == "success"]
    decisions = {}
    for row in successful:
        decisions.setdefault(row["decision"]["billing_status"], []).append(row["deployment"])
    report["comparison"] = {
        "successful_models": len(successful), "failed_models": len(models) - len(successful),
        "decisions": decisions,
        "all_models_agree": len(decisions) == 1 if len(successful) == len(models) else None,
        "disagreement_among_successful_models": len(decisions) > 1,
        "note": "Agreement does not establish correctness. No approved ground-truth label was supplied.",
    }
    return report


def stored_billing_status(order):
    """Preserve the source value and label the work-order billing enum."""
    value = _field(order, "BillingStatus", "billing_status", "BillStatus", "bill_status")
    code = str(value).strip() if type(value) in (int, str) else None
    return {"value": value, "label": {"0": "FREE", "1": "BILLABLE"}.get(code, "UNKNOWN")}


def _stored_status_text(status):
    if status is None or status.get("value") is None:
        return "UNKNOWN"
    return f"{status['value']} ({status['label']})"


def _max_history_similarity(row):
    """Use retrieved cosine similarities, including evidence not cited by the model."""
    similarities = [
        document.get("similarity")
        for call in row.get("tool_calls", [])
        if call.get("name") == "get_historical_orders" and not call.get("error")
        for document in (call.get("result") or {}).get("historical_orders", [])
    ]
    return max((value for value in similarities
                if type(value) in (int, float) and math.isfinite(value)), default=None)


def _model_metrics(report):
    return {
        row["deployment"]: {
            "confidence": (row.get("decision") or {}).get("confidence"),
            "max_history_similarity": _max_history_similarity(row),
        }
        for row in report["results"]
    }


def _metric_text(value):
    return "N/A" if value is None else f"{value:.2f}"


def format_comparison(report):
    """Readable table for the terminal and companion Markdown report."""
    def cell(value):
        return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ").replace("\r", " ")

    lines = [
        f"Order: {cell(report['order_id'])} | Bill status: {cell(_stored_status_text(report.get('work_order_billing_status')))}", "",
        "| Deployment | Result | Billing status | Confidence | Max history similarity | Seconds | Total tokens | Chat turns | Tool calls | Summary / error |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in report["results"]:
        decision, usage = row["decision"] or {}, row["usage"] or {}
        values = [row["deployment"], row["status"], decision.get("billing_status"),
                  _metric_text(decision.get("confidence")), _metric_text(_max_history_similarity(row)),
                  row["elapsed_seconds"], usage.get("total_tokens"),
                  len(row["chat_calls"]), len(row["tool_calls"]),
                  row["error"] or decision.get("summary")]
        lines.append("| " + " | ".join(cell(value) for value in values) + " |")
    agreement = report["comparison"]["all_models_agree"]
    lines.extend(["", "All models agree: " + ("incomplete comparison" if agreement is None else str(agreement)),
                  report["comparison"]["note"]])
    return "\n".join(lines) + "\n"


def write_reports(report, output):
    """Save full evidence/results as JSON and a companion comparison table."""
    output = Path(output)
    if output.suffix.lower() != ".json":
        raise ValueError("The output path must end in .json.")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    output.with_suffix(".md").write_text(format_comparison(report), encoding="utf-8")


def _save_batch(batch, output):
    """Checkpoint the manifest and summary after each order, without copying full prompts."""
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(batch, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(output)
    lines = [f"Batch status: {batch['status']}", ""]
    model_order = list(dict.fromkeys(
        model for item in batch["orders"] for model in item["model_decisions"]
    ))
    rows = [["Order", "status", "Model decisions", "Result", "agree", "Error"]]
    for item in batch["orders"]:
        metrics = item.get("model_metrics", {})
        decisions = "; ".join(
            f"{model.removeprefix('ai-billing-foundry-')}: {item['model_decisions'][model]} "
            f"(conf: {_metric_text(metrics.get(model, {}).get('confidence'))}, "
            f"sim: {_metric_text(metrics.get(model, {}).get('max_history_similarity'))})"
            for model in model_order
            if model in item["model_decisions"]
        )
        values = [item["order_id"],
                  _stored_status_text(item.get("work_order_billing_status")), decisions, item["status"],
                  item["all_models_agree"], item["error"]]
        cells = [str(value if value is not None else "").replace("|", "\\|")
                 .replace("\n", " ").replace("\r", " ") for value in values]
        rows.append(cells)
    widths = [max(3, max(len(row[index]) for row in rows)) for index in range(len(rows[0]))]
    for index, row in enumerate(rows):
        lines.append("| " + " | ".join(cell.ljust(width) for cell, width in zip(row, widths)) + " |")
        if index == 0:
            lines.append("| " + " | ".join("-" * width for width in widths) + " |")
    lines.extend(["", f"Processed: {batch['processed_orders']}; failed or partial: {batch['failed_orders']}; "
                  f"skipped non-JSON blobs: {batch['skipped_non_json_blobs']}.",
                  "Full per-order reports are in the companion orders directory."])
    output.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _wait_for_round(previous_start, interval):
    """Space round starts using elapsed time, without sleeping after the final order."""
    now = time.monotonic()
    if previous_start is not None:
        remaining = previous_start + interval - now
        if remaining > 0:
            print(f"Waiting {remaining:.1f}s before the next comparison round...", flush=True)
        while remaining > 0:
            time.sleep(min(remaining, 40))
            now = time.monotonic()
            remaining = previous_start + interval - now
    return now


def compare_all_orders(container_client, search_client, embeddings_client, chat_client, models, *,
                       output, prefix="", limit=None, same_store=False, max_completion_tokens=4096,
                       max_tool_rounds=4, responses_client=None, round_interval_seconds=60):
    """Stream all JSON blobs and finish one order's model comparisons before the next.

    Each blob must hold one JSON order object with its work-order ID. Malformed
    orders and retrieval failures are recorded without blocking other blobs.
    """
    from azure.core.exceptions import AzureError
    from openai import OpenAIError

    output = Path(output)
    if output.suffix.lower() != ".json":
        raise ValueError("The output path must end in .json.")
    models = list(dict.fromkeys(name.strip() for name in models if name.strip()))
    if len(models) < 2 or max_completion_tokens <= 0 or max_tool_rounds < 0:
        raise ValueError("Supply two distinct models and a positive max_completion_tokens.")
    if limit is not None and limit <= 0:
        raise ValueError("limit must be greater than zero.")
    if not math.isfinite(round_interval_seconds) or round_interval_seconds < 0:
        raise ValueError("round_interval_seconds must be finite and zero or greater.")
    # A new directory per run keeps older per-order reports intact.
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    reports_dir = output.parent / (output.stem + "_orders") / run_id
    batch = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(), "status": "running",
        "source": {"container_url": container_client.url.split("?")[0], "prefix": prefix},
        "models": models, "limit": limit, "processed_orders": 0, "failed_orders": 0,
        "skipped_non_json_blobs": 0, "orders": [],
        "round_interval_seconds": round_interval_seconds,
    }
    _save_batch(batch, output)
    completed = False
    previous_round_start = None
    try:
        # ItemPaged follows all Azure listing pages; orders are not loaded together.
        for item in container_client.list_blobs(name_starts_with=prefix or None):
            if not item.name.lower().endswith(".json"):
                batch["skipped_non_json_blobs"] += 1
                continue
            previous_round_start = _wait_for_round(previous_round_start, round_interval_seconds)
            entry = {"blob_name": item.name, "order_id": None, "status": "error",
                     "started_at_utc": datetime.now(timezone.utc).isoformat(),
                     "work_order_billing_status": None,
                     "report_file": None, "model_decisions": {}, "all_models_agree": None, "error": None}
            index = batch["processed_orders"] + 1
            print(f"[{index}] Comparing {item.name}", flush=True)
            report = None
            try:
                with container_client.get_blob_client(item.name) as blob:
                    order = load_order_from_blob(blob)
                entry["work_order_billing_status"] = stored_billing_status(order)
                entry["order_id"] = _field(
                    order, "work_order_id", "WorkOrderId", "workorderid", "order_id", "OrderId",
                )
                report = compare_models(
                    order, search_client, embeddings_client, chat_client, models,
                    same_store=same_store, max_completion_tokens=max_completion_tokens,
                    max_tool_rounds=max_tool_rounds,
                    responses_client=responses_client,
                )
                report["source"] = {"blob_name": item.name,
                                    "container_url": batch["source"]["container_url"]}
                entry["status"] = "partial" if report["comparison"]["failed_models"] else "success"
                entry["all_models_agree"] = report["comparison"]["all_models_agree"]
                entry["model_decisions"] = {
                    row["deployment"]: row["decision"]["billing_status"] if row["status"] == "success" else row["status"]
                    for row in report["results"]
                }
                entry["model_metrics"] = _model_metrics(report)
            except (AzureError, OpenAIError, ValueError, TypeError, RuntimeError) as error:
                entry["error"] = f"{type(error).__name__}: {error}"
                print(f"  Failed: {entry['error']}", flush=True)
            # Disk failures stop the run rather than silently discarding results.
            if report is not None:
                report_path = reports_dir / f"{index:06d}.json"
                write_reports(report, report_path)
                entry["report_file"] = report_path.relative_to(output.parent).as_posix()
                print(format_comparison(report), flush=True)
            batch["orders"].append(entry)
            batch["processed_orders"] += 1
            if entry["status"] != "success":
                batch["failed_orders"] += 1
            _save_batch(batch, output)
            if limit is not None and batch["processed_orders"] >= limit:
                break
        completed = True
    finally:
        batch["status"] = "completed" if completed else "interrupted"
        batch["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        _save_batch(batch, output)
    return batch


def main():
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    parser = build_argument_parser()
    args = parser.parse_args()
    batch_mode = args.all_orders or not args.blob_name
    if batch_mode and args.order_id:
        parser.error("--order-id applies only to a single --blob-name, not to all orders.")
    if args.limit is not None and (args.limit <= 0 or not batch_mode):
        parser.error("--limit must be positive and applies only to batch mode.")
    models = args.models
    models = list(dict.fromkeys(name.strip() for name in models if name.strip()))
    if len(models) < 2:
        parser.error("Supply at least two distinct --models, or set AZURE_OPENAI_COMPARE_DEPLOYMENTS.")
    if args.max_completion_tokens <= 0 or not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--max-completion-tokens and --timeout must be positive.")
    if args.max_tool_rounds < 0:
        parser.error("--max-tool-rounds must be zero or greater.")
    if not math.isfinite(args.round_interval) or args.round_interval < 0:
        parser.error("--round-interval must be finite and zero or greater.")
    if args.output.suffix.lower() != ".json":
        parser.error("--output must end in .json.")
    required = ["AZURE_OPENAI_API_KEY"]
    missing = [name for name in required if not os.getenv(name, "").strip()]
    storage_connection = _setting("AZURE_STORAGE_CONNECTION_STRING")
    storage_key = _setting("AZURE_STORAGE_ACCOUNT_KEY")
    if not storage_connection and not storage_key:
        missing.append("AZURE_STORAGE_CONNECTION_STRING (or AZURE_STORAGE_ACCOUNT_KEY)")
    search_key = _setting("AZURE_SEARCH_QUERY_KEY") or _setting("AZURE_SEARCH_ADMIN_KEY")
    if not search_key:
        missing.append("AZURE_SEARCH_QUERY_KEY (or AZURE_SEARCH_ADMIN_KEY)")
    if missing:
        parser.error("Set these values in PythonClient/.env: " + ", ".join(missing))

    from azure.core.credentials import AzureKeyCredential
    from azure.search.documents import SearchClient
    from azure.storage.blob import ContainerClient
    from openai import AzureOpenAI, OpenAI

    with ExitStack() as stack:
        # A connection string contains its own account endpoint. Otherwise use
        # the Terraform account default (or --storage-account) with an env key.
        if storage_connection:
            container = ContainerClient.from_connection_string(
                storage_connection, container_name=args.storage_container,
            )
        else:
            container = ContainerClient(
                account_url=f"https://{args.storage_account}.blob.core.windows.net",
                container_name=args.storage_container, credential=storage_key,
            )
        container = stack.enter_context(container)
        search = stack.enter_context(SearchClient(
            endpoint=args.search_endpoint,
            index_name=args.search_index,
            credential=AzureKeyCredential(search_key),
        ))
        embeddings = stack.enter_context(AzureOpenAI(
            azure_endpoint=args.openai_endpoint,
            api_key=os.environ["AZURE_OPENAI_API_KEY"],
            api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-01"),
        ))
        chat_client = stack.enter_context(AzureOpenAI(
            azure_endpoint=args.chat_endpoint or args.openai_endpoint,
            api_key=_setting("AZURE_OPENAI_CHAT_API_KEY") or os.environ["AZURE_OPENAI_API_KEY"],
            api_version=os.getenv("AZURE_OPENAI_CHAT_API_VERSION", "2024-10-21"),
            timeout=args.timeout, max_retries=0,
        ))
        responses_client = stack.enter_context(OpenAI(
            base_url=(args.chat_endpoint or args.openai_endpoint).rstrip("/") + "/openai/v1/",
            api_key=_setting("AZURE_OPENAI_CHAT_API_KEY") or os.environ["AZURE_OPENAI_API_KEY"],
            timeout=args.timeout, max_retries=0,
        ))
        if batch_mode:
            batch = compare_all_orders(
                container, search, embeddings, chat_client, models, output=args.output,
                prefix=args.prefix, limit=args.limit, same_store=args.all_stores,
                max_completion_tokens=args.max_completion_tokens,
                max_tool_rounds=args.max_tool_rounds,
                responses_client=responses_client,
                round_interval_seconds=args.round_interval,
            )
            print(f"Processed {batch['processed_orders']} orders; {batch['failed_orders']} failed or partial.")
            print(f"Saved {args.output} and {args.output.with_suffix('.md')}")
            return 1 if batch["failed_orders"] else 0
        blob = stack.enter_context(container.get_blob_client(args.blob_name))
        order = load_order_from_blob(blob)
        report = compare_models(
            order, search, embeddings, chat_client, models, order_id=args.order_id,
            same_store=not args.all_stores, max_completion_tokens=args.max_completion_tokens,
            max_tool_rounds=args.max_tool_rounds,
            responses_client=responses_client,
        )
    report["source"] = {"container": args.storage_container, "blob_name": args.blob_name,
                        "blob_url": blob.url.split("?")[0]}
    report["chat_endpoint"] = args.chat_endpoint or args.openai_endpoint
    report["chat_api_version"] = os.getenv("AZURE_OPENAI_CHAT_API_VERSION", "2024-10-21")
    write_reports(report, args.output)
    print(format_comparison(report))
    print(f"Saved {args.output} and {args.output.with_suffix('.md')}")
    return 1 if report["comparison"]["failed_models"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
