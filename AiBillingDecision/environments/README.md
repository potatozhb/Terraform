# Terraform environments

Use `dev` or `prod`; both reuse the `shared`, `aiDecision`, and `functions` modules.

| Root | Subscription | Existing resource group |
| --- | --- | --- |
| `dev` | `69feff3c-2c0a-4c0f-b633-367ff3ca483d` | `aibillingdecision-69fe` |
| `prod` | `2f482561-182f-4bd5-840b-1dc7d21f0811` | `aibillingdecision-prod` |

## Run Terraform

From the repository root:

```powershell
terraform -chdir=AiBillingDecision/environments/dev init
terraform -chdir=AiBillingDecision/environments/dev plan -out=changes.tfplan
terraform -chdir=AiBillingDecision/environments/dev apply changes.tfplan
```

Replace `dev` with `prod` for production. Review each plan before applying.
The logged-in Azure identity must have access to the root's subscription.
Use the `default` workspace in each root. Clear inherited `TF_WORKSPACE` and
`TF_DATA_DIR` overrides; environments are separated by directory.

## Local state and credentials

Each root uses its own local `terraform.tfstate`. These files track the existing
resources and must be backed up, not deleted or committed. On another machine,
transfer the matching state before running Terraform. Local states do not provide
shared locking between machines; do not run concurrent deployments from copies.

Database credentials belong in each root's ignored `secrets.auto.tfvars`, or in
`TF_VAR_sql_connection_string`. Use `secrets.auto.tfvars.example` as a template.
The environment resource groups must already exist.

Both provider lock files are retained. A shared, ignored provider cache lives at
`AiBillingDecision/.terraform/providers`; environment initialization directories
reference it locally. It is only a download cache, not resource state. Standard
`terraform init` can obtain providers again on another machine.

## Recovery and first production plan

The retired `main` root was removed. Its original state snapshots, backups, and
configuration were verified and archived in the ignored local file
`AiBillingDecision/.recovery/main-before-cleanup.zip`. It contains sensitive data;
keep it private. Active environment states and credentials were retained.
Do not apply using an archived copy of a state.

The empty `database` module and unused subnet lookup were removed. Function App
integration still uses `module.shared.subnet_ids.subnetFunctions`. The `removed`
block preserves the formerly managed resource group in Azure.

The production state predates current AI module changes: it tracks
`billing_knowledge`, while the module declares `billing_knowledge_index`.
Review the first production plan for index and agent changes before applying.
State relocation does not migrate index documents.

There is no remote backend because no storage account is available. Remote state
can be configured later using a separate blob key for each environment and
`terraform init -migrate-state` to transfer the existing local state.
