resource "azurerm_service_plan" "functions" {
  name                = "plan-aibilling-flex"
  resource_group_name = var.resource_group_name
  location            = var.location
  os_type             = "Linux"
  sku_name            = "FC1"
}

resource "azurerm_function_app_flex_consumption" "decision" {
  name                = "func-aibilling-decision"
  resource_group_name = var.resource_group_name
  location            = var.location
  service_plan_id     = azurerm_service_plan.functions.id

  storage_container_type      = "blobContainer"
  storage_container_endpoint  = var.deployment_container_endpoint
  storage_authentication_type = "StorageAccountConnectionString"
  storage_access_key          = var.storage_account_access_key

  runtime_name           = "dotnet-isolated"
  runtime_version        = "8.0"
  instance_memory_in_mb  = 2048
  maximum_instance_count = 40

  site_config {}

  identity {
    type = "SystemAssigned"
  }
}

output "function_app_id" {
  value = azurerm_function_app_flex_consumption.decision.id
}
