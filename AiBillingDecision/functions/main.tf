resource "azurerm_service_plan" "functions" {
  name                = "plan-aibilling-flex"
  resource_group_name = var.resource_group_name
  location            = var.location
  os_type             = "Linux"
  sku_name            = "FC1"
}

# Test database VM
data "azurerm_subnet" "existing_subnet_function" {
  name                 = "Function"
  virtual_network_name = "USWest-vnet"
  resource_group_name  = "USWest"
}

resource "azurerm_function_app_flex_consumption" "decision" {
  name                = var.function_app_name
  resource_group_name = var.resource_group_name
  location            = var.location
  service_plan_id     = azurerm_service_plan.functions.id

  storage_container_type      = "blobContainer"
  storage_container_endpoint  = var.deployment_container_endpoint
  storage_authentication_type = "StorageAccountConnectionString"
  storage_access_key          = var.storage_account_access_key

# add it to a virtual network subnet to access the database and eventhub
#   virtual_network_subnet_id   = var.virtual_network_subnet_id
  virtual_network_subnet_id = data.azurerm_subnet.existing_subnet_function.id

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
    "AzureSearch__ServiceName"                      = var.search_service_name
    "Foundry__ApiKey"                               = var.foundry_api_key
    "Foundry__ServiceUrl"                           = var.foundry_service_url
    "Foundry__EmbeddingDeploymentName"              = var.embedding_deployment_name
    "Foundry__EmbeddingModelName"                   = "text-embedding-3-small"
    "Sql__ConnectionString"                         = var.sql_connection_string
    "ClosedOrderEventHubConnection"                 = var.eventhub_connection_string
    "ClosedOrderEventHubName"                       = var.eventhub_name
  }

  site_config {
    application_insights_connection_string = azurerm_application_insights.functions_log.connection_string
  }

  identity {
    type = "SystemAssigned"
  }
}

output "function_app_id" {
  value = azurerm_function_app_flex_consumption.decision.id
}

resource "azurerm_log_analytics_workspace" "functions_ws" {
  name                = "log-aibilling-functions"
  location            = var.location
  resource_group_name = var.resource_group_name
  sku                 = "PerGB2018"
  retention_in_days   = 30
}

resource "azurerm_application_insights" "functions_log" {
  name                = "appi-aibilling-functions"
  location            = var.location
  resource_group_name = var.resource_group_name
  workspace_id        = azurerm_log_analytics_workspace.functions_ws.id
  application_type    = "web"
}