variable "sql_connection_string" {
  type        = string
  sensitive   = true
  nullable    = false
  description = "Database connection string for this environment; supply via TF_VAR_sql_connection_string or secrets.auto.tfvars."
}
