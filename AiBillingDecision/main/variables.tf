variable "subscription_id" {
  type        = string
  # AMPM WebSite Subscription ID
  default     = "2f482561-182f-4bd5-840b-1dc7d21f0811"
  description = "Visual Studio Subscription ID"
  # Visual Studio Subscription ID
  # default     = "69feff3c-2c0a-4c0f-b633-367ff3ca483d"
  # description = "AMPM Subscription ID"
}

variable "resource_group_name" {
  type        = string
  default     = "aibillingdecision-prod"
  description = "Name of the resource group for the search service."
}
