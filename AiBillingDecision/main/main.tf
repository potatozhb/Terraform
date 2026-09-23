terraform {
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 5.0.0"
    }
  }
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
  source = "../aiDecision"
  resource_group_name = azurerm_resource_group.crc-rg.name
  location            = azurerm_resource_group.crc-rg.location
}

module "shared" {
  source = "../shared"
  resource_group_name = azurerm_resource_group.crc-rg.name
  location            = azurerm_resource_group.crc-rg.location
}

output "resource_group_id" {
  value = "${azurerm_resource_group.crc-rg.name}:${azurerm_resource_group.crc-rg.id}"
}