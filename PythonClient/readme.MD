
# Publish Foundry Agent to Microsoft 365

1. Regist Microsoft.BotService for your Azure subscription
2. Build an agent with knowledge base.
3. Publish to Teams & Microsoft 365 Copilot

## Index special billing rules

`data/special_billing_rules.json` preserves all 21 rules from the supplied C#
`SpecialBillingRules` list, including the original trigger phrases and conditions.

From `PythonClient`, install dependencies into the existing virtual environment:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Append the settings in `.env.example` to your existing `.env` and fill in your
Search admin key and Foundry account key. Adjust the endpoints for your subscription.
The Foundry project endpoint and chat model deployment in the existing `.env`
are separate from the account endpoint and embedding deployment used here.

Preview the documents without credentials, embeddings, or Azure requests:

```powershell
.\.venv\Scripts\python.exe src/index_billing_rules.py --dry-run
```

Upload documents and generate 1,536-dimensional embeddings:

```powershell
.\.venv\Scripts\python.exe src/index_billing_rules.py
```

The Terraform-managed `billing-knowledge-index` must already have the new schema.
The uploader does not create or rebuild it. Each rule uses its `RuleId` as
`document_id`, so rerunning updates the same documents. It does not delete other
documents or rules removed from the JSON file.

`RuleName` maps to `document_name`; type, region, action, and reason map to the
corresponding `rule_*` fields. Trigger phrases and all conditions are preserved
in `merged_text`, which is embedded into `content_vector`. Work order, store,
contract, and product fields are omitted because the supplied rules do not
contain those values. A rule's suggested action is not an actual work order
billing decision; the conditions still need to be evaluated.

Uploads use batches and check individual document results. If a later batch
fails, earlier batches remain uploaded; fix the reported error and rerun.

## Build billing prompts with RAG evidence

`src/billing_prompts.py` ports the supplied C# system policy into
`src/billing_system_prompt.txt`. It builds the system and user prompts without
calling the chat model or changing stored records. It uses the account OpenAI
endpoint for query embeddings and Azure AI Search for evidence.

Load an existing JSON order from Blob Storage (set the storage connection string
and container in `.env`; supply the actual blob name):

```powershell
.\.venv\Scripts\python.exe src/billing_prompts.py --blob-name "orders/12345.json" --order-id 12345 --output prompts.json
```

Or use a local JSON WorkOrderDto, or retrieve an already indexed order:

```powershell
.\.venv\Scripts\python.exe src/billing_prompts.py --order-file data/order.json --order-id 12345 --output prompts.json
.\.venv\Scripts\python.exe src/billing_prompts.py --order-id 12345 --output prompts.json
```

Blob/local input must be a single JSON order object. `WorkOrderId` or
`work_order_id` identifies the order; `--order-id` supplies it when absent.
Keep `OrderSearchContent`, `Contracts`, and contract items in that object.
An explicitly empty `Contracts` list means no current contract. If that field
is absent, the prompt looks for explicit contract evidence in the order text.
The indexed-order path retrieves every chunk matching `work_order_id` and uses
their `merged_text`; it does not retrieve additional contracts from other orders.

Historical orders must already be indexed in `billing-knowledge-index` with
`work_order_id`, `billing_status` (`BILLABLE` or `FREE`), `merged_text`, and
1,536-dimensional `content_vector`. Blob presence alone does not make them
searchable; this script does not index stored orders. Exhaustive vector retrieval
excludes the target order, filters by `store_id` when supplied, and returns only
the three closest distinct orders, ordered by cosine similarity. An order is
ranked by its closest indexed chunk. Duplicate chunks are handled by excluding
already selected order IDs and searching for the remaining slots. Fewer than
three are returned if fewer eligible orders exist. `--all-stores` removes the
store filter. `similarity` is cosine similarity (-1 to 1; higher means closer),
and `search_score` retains the raw Azure vector score. Neither is a probability.
The conversion is `similarity = 2 - 1 / search_score`, for the Terraform index's
cosine metric, as documented in [Azure vector ranking](https://learn.microsoft.com/en-us/azure/search/vector-search-ranking).
No minimum similarity threshold is applied.

History-query embeddings use `OrderContentWithoutContract` when available,
otherwise line/remark content before falling back to the order search text.
The query is tokenized with `cl100k_base` and capped at 8,000 tokens to stay
below the embedding model's 8,192-token input limit. Only the similarity query
is capped; the complete order and contracts supplied to the billing model are
preserved. `tiktoken` is included in `requirements.txt`; its first use downloads
and caches the tokenizer data. See [Azure embedding input limits](https://learn.microsoft.com/en-us/rest/api/aifoundry/azureopenai/embeddings).

All indexed `special_billing_rule` documents are included because the current
catalog is small and dropping a conflicting rule could change the decision.
The model must validate triggers, regions, and conditions. Historical orders can
affect confidence only. Retrieval errors propagate instead of being converted
into empty evidence. The prompt output includes all retrieved evidence for review;
the model's `usedEvidence` should include only documents actually used.

Call the function from Python with existing clients:

```python
from src.billing_prompts import build_billing_prompts

prompts = build_billing_prompts(order, search_client, embeddings_client)
system_prompt = prompts["system_prompt"]
user_prompt = prompts["user_prompt"]
messages = prompts["messages"]
```

Run offline verification from `PythonClient`:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Compare chat models using retrieval tools

`src/compare_model.py` reads stored order JSON and sends each model the billing
system policy and a user message containing **only the current order**. It does
not add historical orders or special rules to that initial message.

Three function tools provide order and RAG evidence:

- `get_special_rules`: retrieves the authorized rule catalog from Azure AI Search,
  retaining all candidates so conflicting overrides are not hidden.
- `get_historical_orders`: returns only the three closest distinct historical
  orders by exhaustive cosine vector search, with a `similarity` value for each,
  excluding the current order and applying the configured store filter.
- `get_line_item`: resolves an unselected `WorkOrderLines[*].ContractItemId`
  (`null`, `0`, `-1`, or empty) by returning date-valid candidates from the
  current order's supplied contracts, along with the current remarks. It
  replaces the previous placeholder. A selected ContractId restricts candidates
  to that contract. Existing selections are preserved. Missing service dates,
  expired items, and missing date bounds do not produce invented candidates.

All tools accept `{}`. The application binds them to the current order and
store settings, so a model cannot supply another target ID or override filters.
When an order has an unselected contract item and supplied contracts, the
application forces `get_line_item` on the first turn using the appropriate
named-function `tool_choice` schema for each API. A model response that skips
that required call is rejected. Project work and FCL/Calco major-account early
stops retain priority. The next turn uses `auto` for further tools.
The model chooses a candidate only when remarks and performed work clearly
match its scope; it cites the inferred association in `usedEvidence.contracts`.
Candidate date validity alone does not establish coverage. Ambiguous or absent
matches retain the original selection, and stored orders are never modified.
The tool returns candidates for semantic evaluation by the model; it does not
persist a chosen item.

`get_special_rules` is mandatory once per model per order, even for early-stop
decisions. When line-item resolution is required, the sequence is
`get_line_item` followed by `get_special_rules`; otherwise rules are retrieved
first. History remains optional. Each model runner maintains its own
`completed_tools` set across turns and records the successful tool names in
its JSON result. A tool is marked complete only after its result is delivered
to that conversation, including cached results. The shared evidence cache does
not mark a tool complete for another model. Final answers that skip required
tools are rejected, and insufficient tool-round budgets fail before requests.
Retrieving rules does not change the existing billing-policy precedence.
Tool responses are sent back as `tool`
messages with the corresponding `tool_call_id`, then the model continues until
it returns the final billing JSON. This follows the official
[function-calling workflow](https://developers.openai.com/api/docs/guides/function-calling).

Each model has an independent conversation. Successful tool results are cached
for that order, so subsequent requests from either model receive identical
retrieved evidence. The cache is reset for the next order. Retrieval failures
mark that model as `tool_error`; remaining models and orders continue. Failed
retrievals are never substituted with empty successful results. Models must
support Chat Completions function tools, strict function schemas, JSON mode,
and `max_completion_tokens`.

`ai-billing-foundry-6.1-sol` uses the Responses API with
`reasoning={"effort": "low"}`. This deployment rejects function tools with
reasoning on Chat Completions and also rejects `reasoning_effort="none"`.
The Responses client uses the same account endpoint/key with `/openai/v1/`,
as described in the [Azure Responses documentation](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/responses).
It sends flattened function definitions and `function_call_output` items,
preserves reasoning output items between turns, and uses `store=False`.
`ai-billing-foundry-5.6-sol` retains the working Chat Completions path.
Per-model `api` and `request_options` in the JSON report record this difference.
Responses input/output token counts are mapped to the report's prompt/completion
totals; native usage details remain in each entry of `chat_calls`.
Python callers supplying the 6.1 deployment must also pass an OpenAI
`responses_client` configured with the Azure `/openai/v1/` base URL to
`compare_models` or `compare_all_orders`; the CLI creates this client automatically.

From `PythonClient`, compare all JSON blobs one by one:

```powershell
.\.venv\Scripts\python.exe src/compare_model.py --all-orders
```

Batch comparison starts at most one order round per minute by default. A round
runs all configured models for one order. The first order starts immediately;
the next waits until at least 60 seconds after the previous round started. If a
round already takes 60 seconds or more, the next can start when it finishes.
No extra wait occurs after the final order. Use `--round-interval 120` for a
two-minute interval. The interval and per-order start timestamps are recorded
in the batch JSON report. This pacing applies between orders, not between the
individual chat/tool calls within an order.

Optional filters and a single-order example:

```powershell
.\.venv\Scripts\python.exe src/compare_model.py --all-orders --prefix "orders/" --limit 5
.\.venv\Scripts\python.exe src/compare_model.py --blob-name "orders/12345.json" --order-id 12345
```

Replace the example blob name and ID with actual values. With no blob selected
in the environment, no arguments also means all orders. `--all-orders` overrides
`AZURE_STORAGE_BLOB_NAME`. `AZURE_STORAGE_BLOB_PREFIX` supplies an optional
prefix. Each batch blob must contain one JSON order object with its work-order
ID; `--order-id` is restricted to single-blob mode. Non-JSON filenames are
skipped. Invalid order blobs are recorded and the batch continues.

Batch reports:

- `compare_results.json`: checkpointed manifest with order status, decisions,
  agreement, errors, and full-report paths.
- `compare_results.md`: combined comparison table.
- `compare_results_orders/<run timestamp>/`: full per-order JSON and Markdown
  reports, containing initial prompts, tool definitions, each model's conversation,
  tool-call arguments/results/cache hits/errors, parsed final decisions,
  per-turn and total token usage, latency, and chat/tool call counts.

Single-blob mode writes the full report directly to `compare_results.json` and
its table to `compare_results.md`. `--output` changes the destination. Completed
order reports are checkpointed before moving on; listing failures mark the batch
`interrupted`. Rerunning starts a new run rather than resuming the old one.

`--max-tool-rounds` defaults to 4. After that many retrieval turns, a final
request disables tools and asks for billing JSON. `--max-completion-tokens`
defaults to 4096 **per chat turn**. `--timeout` defaults to 120 seconds per chat
request. No temperature or automatic chat retries are used. Latency covers all
chat turns and tool execution, and can be lower for models receiving cached
retrieval results. `--all-stores` permits history from other stores. Agreement is
incomplete if any model fails; agreement alone does not establish correctness.

Nonsecret defaults are copied from `AiBillingDecision/main/main.tf`
(`is_prod = false`), `shared/variables.tf`, and `aiDecision/{main,variables}.tf`:

| Setting | Terraform dev default |
| --- | --- |
| Storage account | `aibillingstorage69fe` |
| Container | `foundry` |
| Search endpoint | `https://ai-billing-search-69fe.search.windows.net` |
| Search index | `billing-knowledge-index` |
| OpenAI account endpoint | `https://ai-billing-foundry-69fe.cognitiveservices.azure.com` |
| Compared deployments | `ai-billing-foundry-6.1-sol`, `ai-billing-foundry-5.6-sol` |
| Embedding deployment | `text-embedding-3-small-code-vector` |

CLI settings override `.env`/environment values, which override these defaults.
The local order container discovered during setup is `model-data-matrix`; select
it with `AZURE_STORAGE_CONTAINER_NAME` or `--storage-container`. Credentials are
loaded from `.env`: Search query/admin key, OpenAI API key, and either a storage
connection string or `AZURE_STORAGE_ACCOUNT_KEY`. A connection string selects
its own account. Optional `AZURE_OPENAI_CHAT_ENDPOINT` and
`AZURE_OPENAI_CHAT_API_KEY` select a separate chat account; otherwise the
embedding account settings are reused. `--models` or
`AZURE_OPENAI_COMPARE_DEPLOYMENTS` override the compared deployments.

The standalone `billing_prompts.py` command still supports building prompts with
retrieved evidence embedded; `compare_model.py` uses `billing_tools.py` instead.

### Index billed historical orders

`src/index_billed_orders.py` reads JSON orders from the Blob container configured
in `.env` (connection string or account name/key). It selects stored billing
status `1` / `Billable` / `BILLABLE`, not model predictions. It embeds work-only
content using the same helper and deployment as historical retrieval, and stores
the complete supplied order, including contracts, in `merged_text`. Contract
evidence must reflect coverage at the historical service date.

```powershell
.\.venv\Scripts\python.exe src/index_billed_orders.py --dry-run
.\.venv\Scripts\python.exe src/index_billed_orders.py
.\.venv\Scripts\python.exe src/index_billed_orders.py --include-free
.\.venv\Scripts\python.exe src/index_billed_orders.py --order-file data/orders.json --dry-run
```

Use `--blob-name` for a single blob or `--prefix` for a subset. A local file may
contain one order or a list. Dry runs validate eligible orders without embedding
calls or index writes. Uploads require `AZURE_SEARCH_ENDPOINT`,
`AZURE_SEARCH_INDEX_NAME`, `AZURE_SEARCH_ADMIN_KEY`, `AZURE_OPENAI_ENDPOINT`, and
`AZURE_OPENAI_API_KEY`; optional deployment/API-version settings match the other
scripts. Supply `OrderContentWithoutContract`, work lines, or remarks for vectors;
full contract-heavy content is never used as a fallback.

Large evidence is split into UTF-8 chunks of at most 30,000 bytes to fit the
existing filterable `merged_text` field. Each chunk shares the order's work-only
vector; reconstruct full evidence by joining `merged_text` in numeric
`chunk_number` order. A single retrieved chunk may contain only part of a contract.
Repeated uploads use stable chunk keys. Foreign multi-chunk orders or stale extra
chunks stop the run for explicit migration. Upload failures stop the run;
previously uploaded orders remain saved.
