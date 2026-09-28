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

provider "azapi" {
  subscription_id = var.subscription_id
}

provider "azurerm" {
  features {}
  subscription_id = var.subscription_id
}

resource "azurerm_resource_group" "crc-rg" {
  name     = var.resource_group_name
  location = "Canada Central"
  tags = {
    environment = "dev"
  }
}


module "aiDecision" {
  source              = "../aiDecision"
  resource_group_name = azurerm_resource_group.crc-rg.name
  location            = azurerm_resource_group.crc-rg.location
}

module "shared" {
  source              = "../shared"
  resource_group_name = azurerm_resource_group.crc-rg.name
  location            = azurerm_resource_group.crc-rg.location

  eventhub_namespace_name = "evhns-ClosedOrders"
  eventhub_name           = "closedorders"
}

module "functions" {
  source                        = "../functions"
  resource_group_name           = azurerm_resource_group.crc-rg.name
  location                      = azurerm_resource_group.crc-rg.location
  storage_account_name          = module.shared.storage_account_name
  storage_account_access_key    = module.shared.storage_account_access_key
  deployment_container_endpoint = module.shared.function_deployment_endpoint

  search_api_key                              = module.aiDecision.search_api_key
  search_service_url                          = module.aiDecision.search_service_url
  azure_search_blob_storage_connection_string = module.shared.azure_search_blob_storage_connection_string

  foundry_api_key     = module.aiDecision.foundry_api_key
  foundry_service_url = module.aiDecision.foundry_service_url

  eventhub_connection_string = module.shared.closed_order_eventhub_connection_string
  eventhub_name              = module.shared.eventhub_name
}

output "resource_group_id" {
  value = "${azurerm_resource_group.crc-rg.name}:${azurerm_resource_group.crc-rg.id}"
}
