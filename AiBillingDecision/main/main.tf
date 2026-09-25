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
  source                 = "../aiDecision"
  resource_group_name    = azurerm_resource_group.crc-rg.name
  location               = azurerm_resource_group.crc-rg.location
  foundry_user_object_id = "a86ac5df-09fc-4ca7-8bb1-d0ed20d367ba"
}

module "shared" {
  source              = "../shared"
  resource_group_name = azurerm_resource_group.crc-rg.name
  location            = azurerm_resource_group.crc-rg.location
}

module "functions" {
  source                        = "../functions"
  resource_group_name           = azurerm_resource_group.crc-rg.name
  location                      = azurerm_resource_group.crc-rg.location
  storage_account_name          = module.shared.storage_account_name
  storage_account_access_key    = module.shared.storage_account_access_key
  deployment_container_endpoint = module.shared.function_deployment_endpoint
}

output "resource_group_id" {
  value = "${azurerm_resource_group.crc-rg.name}:${azurerm_resource_group.crc-rg.id}"
}
