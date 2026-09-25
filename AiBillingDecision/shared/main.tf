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


output "storage_account_id" {
  value = "${azurerm_storage_account.crc-storage.name}:${azurerm_storage_account.crc-storage.id}"
}

output "storage_container_id" {
  value = "${azurerm_storage_container.crc-storage-container.name}:${azurerm_storage_container.crc-storage-container.id}"
}

output "storage_container_resource_id" {
  description = "ARM resource ID of the container for role assignment scope."
  value       = "${azurerm_storage_account.crc-storage.id}/blobServices/default/containers/${azurerm_storage_container.crc-storage-container.name}"
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
