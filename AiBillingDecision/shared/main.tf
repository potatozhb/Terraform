resource "azurerm_storage_account" "crc-storage" {
   name                     = var.storage_account_name
   resource_group_name      = var.resource_group_name
   location                 = var.location
   account_tier             = "Standard"
   account_replication_type = "LRS"

    tags = {
      environment = "dev"
    }
}

resource "azurerm_storage_container" "crc-storage-container" {
  name                  = var.storage_container_name
  storage_account_id    = azurerm_storage_account.crc-storage.id
  container_access_type = "private"
}

output "azure_search_blob_storage_connection_string" {
  value = azurerm_storage_account.crc-storage.primary_connection_string
  sensitive = true
}

output "storage_account_name" {
  description = "Name of the storage account used by the function app."
  value       = azurerm_storage_account.crc-storage.name
}

output "storage_account_access_key" {
  description = "Storage account access key used by the function app."
  value       = azurerm_storage_account.crc-storage.primary_access_key
  sensitive   = true
}

resource "azurerm_storage_container" "function_deployments" {
  name                  = "function-deployments"
  storage_account_id    = azurerm_storage_account.crc-storage.id
  container_access_type = "private"
}

output "function_deployment_endpoint" {
  value = "${azurerm_storage_account.crc-storage.primary_blob_endpoint}${azurerm_storage_container.function_deployments.name}"
}



# Stable subscription suffix keeps the namespace name globally distinct.
data "azurerm_client_config" "eventhubs" {}

resource "azurerm_eventhub_namespace" "evhns-ClosedOrders" {
  name                = var.eventhub_namespace_name
  resource_group_name = var.resource_group_name
  location            = var.location
  sku                 = "Basic"
  capacity            = 1
  minimum_tls_version = "1.2"

  tags = {
    environment = "dev"
  }
}

resource "azurerm_eventhub" "closedOrders" {
  name            = var.eventhub_name
  namespace_id    = azurerm_eventhub_namespace.evhns-ClosedOrders.id
  partition_count = 2

  retention_description {
    cleanup_policy          = "Delete"
    retention_time_in_hours = 24
  }
}

output "eventhub_namespace_id" {
  value = azurerm_eventhub_namespace.evhns-ClosedOrders.id
}

output "eventhub_fully_qualified_namespace" {
  value = "${azurerm_eventhub_namespace.evhns-ClosedOrders.name}.servicebus.windows.net"
}

output "eventhub_name" {
  value = azurerm_eventhub.closedOrders.name
}

output "closed_order_eventhub_connection_string" {
  value     = azurerm_eventhub_namespace.evhns-ClosedOrders.default_primary_connection_string
  sensitive = true
}


resource "azurerm_virtual_network" "aiBillingNetwork" {
  name                = "vnet-aibilling"
  resource_group_name = var.resource_group_name
  location            = var.location
  address_space       = ["10.20.0.0/16"]

  tags = {
    environment = "dev"
  }
}

resource "azurerm_subnet" "subnetDatabase" {
  name                 = "snet-aibilling-database"
  resource_group_name  = var.resource_group_name
  virtual_network_name = azurerm_virtual_network.aiBillingNetwork.name
  address_prefixes     = ["10.20.1.0/24"]
}

resource "azurerm_subnet" "subnetFunctions" {
  name                 = "snet-aibilling-functions"
  resource_group_name  = var.resource_group_name
  virtual_network_name = azurerm_virtual_network.aiBillingNetwork.name
  address_prefixes     = ["10.20.2.0/24"]

  delegation {
    name = "Microsoft.App/environments"

    service_delegation {
      name = "Microsoft.App/environments"
    }
  }
}

output "virtual_network_id" {
  value = azurerm_virtual_network.aiBillingNetwork.id
}

output "subnet_ids" {
  value = {
    subnetDatabase  = azurerm_subnet.subnetDatabase.id
    subnetFunctions = azurerm_subnet.subnetFunctions.id
  }
}