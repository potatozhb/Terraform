# az login
# az account set --subscription 2f482561-182f-4bd5-840b-1dc7d21f0811

# cd C:\dev\Terraform\AiBillingDecision\environments\prod
# terraform init
# terraform plan -out=changes.tfplan

# Select this environment by working in this directory.
terraform {
  required_version = ">= 1.7.0, < 2.0.0"

  backend "local" {
    path = "terraform.tfstate"
  }

  required_providers {
    azapi = {
      source  = "Azure/azapi"
      version = ">= 2.9.0, < 3.0.0"
    }
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 5.0.0"
    }
  }
}

# need same subscription with VM

provider "azapi" {
  subscription_id = "2f482561-182f-4bd5-840b-1dc7d21f0811"
}

provider "azurerm" {
  features {}
  subscription_id = "2f482561-182f-4bd5-840b-1dc7d21f0811"
}

locals {
  storage_account_name    = "aibillingstorageprod"
  resource_group_name     = "aibillingdecision-prod"
  resource_group_location = "Canada Central"
  eventhub_namespace      = "evhns-closedorders-prod"
  search_service_name     = "ai-billing-search-prod"
  foundry_name            = "ai-billing-foundry-prod"
  foundry_project_name    = "ai-billing-foundry-project-prod"
  function_app_name       = "func-aibilling-decision-prod"
}

# Keep the existing group when switching from a managed resource to a data source.
removed {
  from = azurerm_resource_group.crc-rg

  lifecycle {
    destroy = false
  }
}

module "shared" {
  source               = "../../shared"
  resource_group_name  = local.resource_group_name
  location             = local.resource_group_location
  storage_account_name = local.storage_account_name

  eventhub_namespace_name = local.eventhub_namespace
  eventhub_name           = "closedorders"
}


module "aiDecision" {
  source               = "../../aiDecision"
  resource_group_name  = local.resource_group_name
  search_service_name  = local.search_service_name
  foundry_name         = local.foundry_name
  foundry_project_name = local.foundry_project_name  
  foundry_deployment_name_old = "${local.search_service_name}-foundry-5.6-sol"
  foundry_deployment_name = "${local.search_service_name}-foundry-6.1-sol"
  # West US 2 is not supported openai
  location = "Canada Central"
}


data "azurerm_subnet" "existing_functions" {
  name                 = "Function"
  virtual_network_name = "Canada-vnet"
  resource_group_name  = "Canada"
}

module "functions" {
  source                        = "../../functions"
  resource_group_name           = local.resource_group_name
  location                      = local.resource_group_location
  function_app_name             = local.function_app_name
  storage_account_name          = module.shared.storage_account_name
  storage_account_access_key    = module.shared.storage_account_access_key
  deployment_container_endpoint = module.shared.function_deployment_endpoint
  virtual_network_subnet_id     = data.azurerm_subnet.existing_functions.id
  sql_connection_string         = var.sql_connection_string

  search_api_key                              = module.aiDecision.search_api_key
  search_service_url                          = module.aiDecision.search_service_url
  search_service_name                         = local.search_service_name
  azure_search_blob_storage_connection_string = module.shared.azure_search_blob_storage_connection_string

  embedding_deployment_name = module.aiDecision.embedding_deployment_name
  foundry_api_key           = module.aiDecision.foundry_api_key
  foundry_service_url       = module.aiDecision.foundry_service_url

  eventhub_connection_string = module.shared.closed_order_eventhub_connection_string
  eventhub_name              = module.shared.eventhub_name
}

data "azurerm_resource_group" "existing" {
  name = local.resource_group_name

  lifecycle {
    precondition {
      condition     = terraform.workspace == "default"
      error_message = "Use the default workspace in this environment root. Dev and prod are separated by directory, not workspace."
    }
  }
}

output "resource_group_id" {
  value = data.azurerm_resource_group.existing.id
}


