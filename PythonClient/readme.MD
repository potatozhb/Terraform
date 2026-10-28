
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
.\.venv\Scripts\python.exe index_billing_rules.py --dry-run
```

Upload documents and generate 1,536-dimensional embeddings:

```powershell
.\.venv\Scripts\python.exe index_billing_rules.py
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

