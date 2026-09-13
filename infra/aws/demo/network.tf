data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  name     = "mcpgw-demo"
  az       = data.aws_availability_zones.available.names[0]
  vpc_cidr = "10.42.0.0/24"

  # The whole /24 is one public subnet. There is no private subnet because
  # there is nothing to put in one, and a private subnet would need a NAT
  # gateway at roughly USD 32/month plus data processing to reach ghcr.io.
  subnet_cidr = "10.42.0.0/24"
}

resource "aws_vpc" "this" {
  cidr_block           = local.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = {
    Name = local.name
  }
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id

  tags = {
    Name = local.name
  }
}

resource "aws_subnet" "public" {
  vpc_id                  = aws_vpc.this.id
  cidr_block              = local.subnet_cidr
  availability_zone       = local.az
  map_public_ip_on_launch = false

  tags = {
    Name = "${local.name}-public"
  }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this.id
  }

  tags = {
    Name = "${local.name}-public"
  }
}

resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}

resource "aws_security_group" "instance" {
  name        = "${local.name}-instance"
  description = "Public HTTP and HTTPS for the demo gateway. No SSH."
  vpc_id      = aws_vpc.this.id

  tags = {
    Name = "${local.name}-instance"
  }
}

resource "aws_vpc_security_group_ingress_rule" "http" {
  for_each = toset(var.allowed_ingress_cidrs)

  security_group_id = aws_security_group.instance.id
  description       = "HTTP, for the ACME HTTP-01 challenge and the redirect to HTTPS"
  cidr_ipv4         = each.value
  from_port         = 80
  to_port           = 80
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "https" {
  for_each = toset(var.allowed_ingress_cidrs)

  security_group_id = aws_security_group.instance.id
  description       = "HTTPS, the only service port the demo actually uses"
  cidr_ipv4         = each.value
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
}

# Egress is open: the host pulls images from ghcr.io, packages from the AL2023
# mirrors, and reaches the SSM and Let's Encrypt endpoints. There is no NAT and
# no VPC endpoint, so all of that goes out through the internet gateway.
resource "aws_vpc_security_group_egress_rule" "all" {
  security_group_id = aws_security_group.instance.id
  description       = "All outbound"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}
