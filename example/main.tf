terraform {
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "= 5.0.0"
    }
  }
}

provider "azurerm" {
  features {}
}

resource "azurerm_resource_group" "mtc-rg" {
  name     = "zhbtest2"
  location = "East US"
  tags = {
    environment = "dev"
  }
}


resource "azurerm_virtual_network" "mtc-vnet" {
  name                = "mtc-vnet"
  address_space       = ["10.0.0.0/16"]
  resource_group_name = azurerm_resource_group.mtc-rg.name
  location            = azurerm_resource_group.mtc-rg.location

  tags = {
    environment = "dev"
  }
}

resource "azurerm_subnet" "mtc-subnet" {
  name                 = "mtc-subnet"
  resource_group_name  = azurerm_resource_group.mtc-rg.name
  virtual_network_name = azurerm_virtual_network.mtc-vnet.name
  address_prefixes     = ["10.0.1.0/24"]
}

resource "azurerm_network_security_group" "mtc-nsg" {
  name                = "mtc-nsg"
  location            = azurerm_resource_group.mtc-rg.location
  resource_group_name = azurerm_resource_group.mtc-rg.name

  # security_rule {
  #   name                       = "AllowSSH"
  #   priority                   = 1001
  #   direction                  = "Inbound"
  #   access                     = "Allow"
  #   protocol                   = "Tcp"
  #   source_port_range          = "*"
  #   destination_port_range     = "22"
  #   source_address_prefix      = "*"
  #   destination_address_prefix = "*"
  # }

  tags = {
    environment = "dev"
  }
}

resource "azurerm_network_security_rule" "mtc-dev-rule" {
  name                        = "AllowSSH"
  priority                    = 100
  direction                   = "Inbound"
  access                      = "Allow"
  protocol                    = "Tcp"
  source_port_range           = "*"
  destination_port_range      = "*"
  source_address_prefix       = "*"
  destination_address_prefix  = "*"
  resource_group_name         = azurerm_resource_group.mtc-rg.name
  network_security_group_name = azurerm_network_security_group.mtc-nsg.name
}

resource "azurerm_subnet_network_security_group_association" "mtc-subnet-nsg-association" {
  subnet_id                 = azurerm_subnet.mtc-subnet.id
  network_security_group_id = azurerm_network_security_group.mtc-nsg.id
}

resource "azurerm_public_ip" "mtc-public-ip" {
  name                = "mtc-public-ip"
  location            = azurerm_resource_group.mtc-rg.location
  resource_group_name = azurerm_resource_group.mtc-rg.name
  allocation_method   = "Static"
  sku                 = "Standard"

  tags = {
    environment = "dev"
  }
}

resource "azurerm_network_interface" "mtc-nic" {
  name                = "mtc-nic"
  location            = azurerm_resource_group.mtc-rg.location
  resource_group_name = azurerm_resource_group.mtc-rg.name

  ip_configuration {
    name                          = "internal"
    subnet_id                     = azurerm_subnet.mtc-subnet.id
    private_ip_address_allocation = "Dynamic"
    public_ip_address_id          = azurerm_public_ip.mtc-public-ip.id
  }

  tags = {
    environment = "dev"
  }
}

resource "azurerm_linux_virtual_machine" "mtc-vm" {
  name                = "mtc-vm"
  resource_group_name = azurerm_resource_group.mtc-rg.name
  location            = azurerm_resource_group.mtc-rg.location
  size                = "Standard_B2s"
  admin_username      = "adminuser"
  network_interface_ids = [
    azurerm_network_interface.mtc-nic.id,
  ]

  custom_data = filebase64("customdata.tpl")

  admin_ssh_key {
    username   = "adminuser"
    public_key = file("~/.ssh/id_rsa.pub")
  }

  os_disk {
    caching              = "ReadWrite"
    storage_account_type = "Standard_LRS"
  }

  source_image_reference {
    publisher = "Canonical"
    offer     = "0001-com-ubuntu-server-jammy"
    sku       = "22_04-lts-gen2"
    version   = "latest"
  }

  provisioner "local-exec" {
    command = templatefile("${var.host_os}-ssh-script.tpl", {
      hostname = self.public_ip_address,
      user = "adminuser",
      identityfile = "~/.ssh/id_rsa"
    })
    interpreter = var.host_os == "windows" ? ["PowerShell", "-Command"] : ["bash", "-c"] //["bash", "-c"] for Linux
  }

  tags = {
    environment = "dev"
  }
}

data "azurerm_public_ip" "mtc-public-ip" {
  name                = azurerm_public_ip.mtc-public-ip.name
  resource_group_name = azurerm_resource_group.mtc-rg.name
}

output "public_ip_address" {
  value = "${azurerm_public_ip.mtc-public-ip.name}: ${data.azurerm_public_ip.mtc-public-ip.ip_address}"
}