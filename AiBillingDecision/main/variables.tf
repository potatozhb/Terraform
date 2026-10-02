variable "subscription_id" {
  type        = string
  # AMPM WebSite Subscription ID
  # default     = "2f482561-182f-4bd5-840b-1dc7d21f0811"
  # description = "AMPM Subscription ID"
  # Visual Studio Subscription ID
  default     = "69feff3c-2c0a-4c0f-b633-367ff3ca483d"
  description = "Visual Studio Subscription ID"
}

variable "resource_group_name" {
  type        = string
  default     = "aibillingdecision"
  description = "Name of the resource group for the search service."
}
