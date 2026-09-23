resource "azurerm_search_service" "crc-search" {
  name                = var.search_service_name
  resource_group_name = var.resource_group_name
  location            = var.location
  sku                 = "basic"
  partition_count     = 1
  replica_count       = 1

  tags = {
    environment = "dev"
  }
}

resource "azurerm_cognitive_account" "crc-foundry" {
  name                = var.foundry_name
  resource_group_name = var.resource_group_name
  location            = var.location
  kind                = "AIServices"
  sku_name            = "S0"

  project_management_enabled = true
  custom_subdomain_name      = var.foundry_name

  identity {
    type = "SystemAssigned"
  }

  tags = {
    environment = "dev"
  }
}

resource "azurerm_cognitive_account_project" "crc-foundry-project" {
  name                 = var.foundry_project_name
  location             = var.location
  cognitive_account_id = azurerm_cognitive_account.crc-foundry.id

  identity {
    type = "SystemAssigned"
  }

  tags = {
    environment = "dev"
  }
}

output "search_service_id" {
  value = "${azurerm_search_service.crc-search.name}:${azurerm_search_service.crc-search.id}"
}

output "foundry_id" {
  value = "${azurerm_cognitive_account.crc-foundry.name}:${azurerm_cognitive_account.crc-foundry.id}"
}

output "foundry_project_id" {
  value = "${azurerm_cognitive_account_project.crc-foundry-project.name}:${azurerm_cognitive_account_project.crc-foundry-project.id}"
}