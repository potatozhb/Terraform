# resource "azurerm_network_interface" "testdb" {
#   name                = "testdb-nic"
#   location            = var.location
#   resource_group_name = var.resource_group_name

#   ip_configuration {
#     name                          = "internal"
#     subnet_id                     = var.virtual_network_subnet_id
#     private_ip_address_allocation = "Dynamic"
#   }
# }