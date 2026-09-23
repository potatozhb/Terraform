

variable "storage_account_name" {
  type        = string
  default     = "aibillingstorage1"
  description = "Name of the storage account for the foundry project."
}


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
