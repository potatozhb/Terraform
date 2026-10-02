terraform {
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
  subscription_id = var.subscription_id
}

provider "azurerm" {
  features {}
  subscription_id = var.subscription_id
}

locals {
  migration_workspace = terraform.workspace == "vs-69fe-migration"

  storage_account_name = local.migration_workspace ? "aibillingstorage69fe" : "aibillingstorage1"
  eventhub_namespace   = local.migration_workspace ? "evhns-closedorders-69fe" : "evhns-ClosedOrders"
  search_service_name  = local.migration_workspace ? "ai-billing-search-69fe" : "ai-billing-search"
  foundry_name         = local.migration_workspace ? "ai-billing-foundry-69fe" : "ai-billing-foundry"
  function_app_name    = local.migration_workspace ? "func-aibilling-decision-69fe" : "func-aibilling-decision"
}

# if account is not owner, need to manually assign the owner role to the group
resource "azurerm_resource_group" "crc-rg" {
  name     = var.resource_group_name
  # location = "Canada Central"
  location = "West US 2"
  tags = {
    environment = "dev"
  }
}

module "shared" {
  source              = "../shared"
  resource_group_name = azurerm_resource_group.crc-rg.name
  location            = azurerm_resource_group.crc-rg.location
  storage_account_name = local.storage_account_name

  eventhub_namespace_name = local.eventhub_namespace
  eventhub_name           = "closedorders"
}


module "aiDecision" {
  source              = "../aiDecision"
  resource_group_name = azurerm_resource_group.crc-rg.name
  search_service_name = local.search_service_name
  foundry_name        = local.foundry_name
  # West US 2 is not supported openai
  location            = "Canada Central"
}

module "database" {
  source              = "../database"
  resource_group_name = azurerm_resource_group.crc-rg.name
  location            = azurerm_resource_group.crc-rg.location
  virtual_network_subnet_id = module.shared.subnet_ids.subnetDatabase
}

module "functions" {
  source                        = "../functions"
  resource_group_name           = azurerm_resource_group.crc-rg.name
  location                      = azurerm_resource_group.crc-rg.location
  function_app_name             = local.function_app_name
  storage_account_name          = module.shared.storage_account_name
  storage_account_access_key    = module.shared.storage_account_access_key
  deployment_container_endpoint = module.shared.function_deployment_endpoint
  virtual_network_subnet_id     = module.shared.subnet_ids.subnetFunctions

  search_api_key                              = module.aiDecision.search_api_key
  search_service_url                          = module.aiDecision.search_service_url
  azure_search_blob_storage_connection_string = module.shared.azure_search_blob_storage_connection_string

  embedding_deployment_name = module.aiDecision.embedding_deployment_name
  foundry_api_key           = module.aiDecision.foundry_api_key
  foundry_service_url       = module.aiDecision.foundry_service_url

  eventhub_connection_string = module.shared.closed_order_eventhub_connection_string
  eventhub_name              = module.shared.eventhub_name
}

output "resource_group_id" {
  value = "${azurerm_resource_group.crc-rg.name}:${azurerm_resource_group.crc-rg.id}"
}


