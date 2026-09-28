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

  app_settings = {
    "AzureWebJobsStorage"                           = var.azure_search_blob_storage_connection_string
    "DEPLOYMENT_STORAGE_CONNECTION_STRING"          = var.azure_search_blob_storage_connection_string
    "AzureSearch__ApiKey"                           = var.search_api_key
    "AzureSearch__ServiceUrl"                       = var.search_service_url
    "AzureSearch__BLOBStorageConnectionString"      = var.azure_search_blob_storage_connection_string
    "AzureSearch__VectorAlgorithmConfigurationName" = "myHnsw"
    "AzureSearch__VectorDimensions"                 = "1536"
    "AzureSearch__VectorizerName"                   = "myFoundry"
    "AzureSearch__VectorSearchProfileName"          = "vector-profile"
    "Foundry__ApiKey"                               = var.foundry_api_key
    "Foundry__ServiceUrl"                           = var.foundry_service_url
    "Foundry__EmbeddingDeploymentName"              = "text-embedding-3-small-code-vector"
    "Foundry__EmbeddingModelName"                   = "text-embedding-3-small"
    "Sql__ConnectionString"                         = var.sql_connection_string
    "ClosedOrderEventHubConnection"                 = var.eventhub_connection_string
    "ClosedOrderEventHubName"                       = var.eventhub_name
  }

  site_config {}

  identity {
    type = "SystemAssigned"
  }
}

output "function_app_id" {
  value = azurerm_function_app_flex_consumption.decision.id
}
