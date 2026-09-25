variable "search_service_name" {
  type        = string
  default     = "ai-billing-search"
  description = "Azure Search Service Name"
}

variable "foundry_name" {
  type        = string
  default     = "ai-billing-foundry"
  description = "ai billing foundry name"
}

variable "foundry_project_name" {
  type        = string
  default     = "ai-billing-foundry-project"
  description = "ai billing foundry project name"
}

variable "foundry_deployment_name" {
  type        = string
  default     = "ai-billing-foundry-deployment"
  description = "ai billing foundry deployment name"
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

# variable "foundry_user_object_id" {
#   type        = string
#   description = "Microsoft Entra object ID of the user accessing Foundry."
# }