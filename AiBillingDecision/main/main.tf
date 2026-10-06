# Switch between prod and dev resources, login with the correct subscription first, then run terraform plan and apply.
# 1. Change the subscription_id variable in main.tf to switch between prod and dev resources.
# 2. Change the resource_group_name variable in main.tf to switch between prod and dev resources.
# 3. Change the local.is_prod variable in main.tf to switch between prod and dev resources.
# 4. Change the "azurerm_resource_group" "crc-rg" based on the database VM location.
# 5. Change the "azurerm_subnet" "existing_subnet_function_prod" based on the database VM location.



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
  is_prod = true

  storage_account_name = local.is_prod ? "aibillingstorageprod" : "aibillingstorage69fe"
  eventhub_namespace   = local.is_prod ? "evhns-closedorders-prod" : "evhns-closedorders-69fe"
  search_service_name  = local.is_prod ? "ai-billing-search-prod" : "ai-billing-search-69fe"
  foundry_name         = local.is_prod ? "ai-billing-foundry-prod" : "ai-billing-foundry-69fe"
  foundry_project_name = local.is_prod ? "ai-billing-foundry-project-prod" : "ai-billing-foundry-project-69fe"
  function_app_name    = local.is_prod ? "func-aibilling-decision-prod" : "func-aibilling-decision-69fe"
  sql_connection_string = local.is_prod ? "Data Source=10.0.0.4,14333;Initial Catalog=crcii;User ID=ampmdev;Password=Am6044215677pm!;TrustServerCertificate=True;" : "Data Source=10.0.0.5,1433;Initial Catalog=crcii_copy;User ID=dev;Password=Ampm6044215677;TrustServerCertificate=True;"
}

# if account is not owner, need to manually assign the owner role to the group
resource "azurerm_resource_group" "crc-rg" {
  name     = var.resource_group_name
  location = "Canada Central"
  # location = "West US 2"
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
  foundry_project_name = local.foundry_project_name
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
  sql_connection_string          = local.sql_connection_string

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


