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

output "storage_account_id" {
  value = "${azurerm_storage_account.crc-storage.name}:${azurerm_storage_account.crc-storage.id}"
}