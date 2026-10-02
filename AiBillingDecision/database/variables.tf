
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

variable "virtual_network_subnet_id" {
  type        = string
  description = "Delegated subnet for Function App VNet integration."
}