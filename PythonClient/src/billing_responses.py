"""Responses API loop for deployments requiring reasoning with function tools."""

from copy import deepcopy
import json
import time

try:
    from .billing_tools import TOOL_DEFINITIONS
except ImportError:
    from billing_tools import TOOL_DEFINITIONS


def run_responses(client, model, row, retrieval_tools, *, max_completion_tokens, max_tool_rounds):
    tools = [{"type": "function", **definition["function"]} for definition in TOOL_DEFINITIONS]
    completed_tools = set()
    row["completed_tools"] = []
    for turn in range(max_tool_rounds + 1):
        row["status"] = "error"
        tool_choice = retrieval_tools.tool_choice(
            turn, max_tool_rounds, completed_tools=completed_tools, responses=True,
        )
        response = client.responses.create(
            model=model, input=deepcopy(row["messages"]), tools=tools,
            tool_choice=tool_choice,
            reasoning=row["request_options"]["reasoning"],
            text={"format": {"type": "json_object"}},
            max_output_tokens=max_completion_tokens, store=False,
            include=["reasoning.encrypted_content"],
        )
        row["response_model"] = response.model
        usage = response.usage.model_dump(mode="json") if response.usage else None
        row["chat_calls"].append({"api": "responses", "model": response.model, "usage": usage})
        if usage:
            if row["usage"] is None:
                row["usage"] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
            row["usage"]["prompt_tokens"] += usage.get("input_tokens") or 0
            row["usage"]["completion_tokens"] += usage.get("output_tokens") or 0
            row["usage"]["total_tokens"] += usage.get("total_tokens") or 0
        row["status"] = "invalid_response"
        row["finish_reason"] = response.status
        row["raw_response"] = response.output_text
        # Replay all output items, including encrypted reasoning, on subsequent
        # turns. store=False avoids relying on server-side conversation storage.
        row["messages"].extend(item.model_dump(mode="json", exclude_none=True) for item in response.output)
        if response.status != "completed":
            raise ValueError(f"Incomplete Responses result: {response.status}; {response.incomplete_details}")
        for item in response.output:
            if item.type == "message":
                for content in item.content:
                    if content.type == "refusal":
                        raise ValueError("Model refusal: " + content.refusal)
        calls = [item for item in response.output if item.type == "function_call"]
        if isinstance(tool_choice, dict) and not any(
            call.name == tool_choice["name"] for call in calls
        ):
            raise ValueError(f"Model did not call the required {tool_choice['name']} tool.")
        if not calls:
            retrieval_tools.validate_required_tools(completed_tools)
            return
        if turn == max_tool_rounds:
            raise ValueError("Model exceeded the maximum tool-call rounds.")
        for call in calls:
            trace = {"id": call.call_id, "name": call.name, "arguments": call.arguments,
                     "result": None, "error": None}
            row["tool_calls"].append(trace)
            start = time.perf_counter()
            try:
                result, cached = retrieval_tools.execute(call.name, call.arguments)
                trace.update(result=result, cached=cached)
            except Exception as error:
                trace["error"] = f"{type(error).__name__}: {error}"
                row["status"] = "tool_error"
                raise
            finally:
                trace["elapsed_seconds"] = round(time.perf_counter() - start, 3)
            row["messages"].append({"type": "function_call_output", "call_id": call.call_id,
                                    "output": json.dumps(result, ensure_ascii=False)})
            completed_tools.add(call.name)
            row["completed_tools"] = sorted(completed_tools)
