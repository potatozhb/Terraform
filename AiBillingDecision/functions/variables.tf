

variable "resource_group_name" {
  type        = string
  default     = "aibillingdecision"
  description = "Name of the resource group for the search service."
}

variable "location" {
  type        = string
  default     = "Canada Central"
  description = "Azure region for the search service."
}

variable "storage_account_name" {
  type        = string
  default     = "aibillingstorage1"
  description = "Name of the storage account for the foundry project."
}

variable "storage_account_access_key" {
  type      = string
  sensitive = true
}
variable "deployment_container_endpoint" {
  type        = string
  description = "Blob container URL for Flex Consumption deployment packages."
}

variable "search_api_key" {
  type      = string
  default   = ""
  sensitive = true
}
variable "search_service_url" {
  type    = string
  default = ""
}

variable "foundry_api_key" {
  type      = string
  default   = ""
  sensitive = true
}

variable "foundry_service_url" {
  type    = string
  default = ""
}

variable "azure_search_blob_storage_connection_string" {
  sensitive = true
  type      = string
  default   = ""
}

variable "sql_connection_string" {
  sensitive = true
  type      = string
  default   = "Data Source=testdb.westus2.cloudapp.azure.com,1433;Initial Catalog=crcii_copy;User ID=dev;Password=Ampm6044215677;TrustServerCertificate=True;"
}

variable "eventhub_connection_string" {
  sensitive = true
  type      = string
  default   = ""
}

variable "eventhub_name" {
  type        = string
  default     = "closedorders"
  description = "Name of the Event Hub."
}

variable "embedding_deployment_name" {
  type        = string
  description = "Foundry deployment used to generate text embeddings."
}

variable "virtual_network_subnet_id" {
  type        = string
  description = "Delegated subnet for Function App VNet integration."
}