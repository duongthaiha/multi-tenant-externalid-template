# ADR 0008: Optional Bastion access for Cosmos Data Explorer

## Status

Accepted

## Context

All POC Cosmos accounts disable public network access and expose the SQL data
plane only through private endpoints. Azure Portal Data Explorer runs in the
operator's browser, so a workstation whose public egress IP changes through
Global Secure Access cannot reliably reach those private endpoints.

ADR 0005 selected cloud-only validation as the baseline and intentionally
excluded a jumpbox. The live demo now has an explicit operator requirement to
inspect private Cosmos data interactively without enabling public Cosmos access
or local authentication.

## Decision

Provide an optional Windows Server jumpbox in the workload VNet and connect to
it through Azure Bastion Basic in the Azure portal. Gate the complete stack
behind `enableJumpbox` so environments that do not need interactive Data
Explorer access retain the cloud-only baseline and avoid its cost.

The operator signs in to Windows with Microsoft Entra ID and receives
`Virtual Machine User Login` at VM scope plus Reader on the VM, NIC, and
Bastion. The same operator receives Cosmos DB Built-in Data Reader and ARM
Reader on every POC Cosmos account. The VM identity receives no Cosmos role,
and Cosmos local authentication and public network access remain disabled.

The VM uses a dedicated public IP for outbound HTTPS because that lower-cost
option was selected instead of NAT Gateway. Its NSG permits RDP only from
`AzureBastionSubnet` and denies every other inbound source. A nightly auto-shutdown
schedule limits VM compute cost. The emergency local administrator password is
generated out of band, stored in Key Vault, and is not used for daily access.

## Consequences

- Data Explorer runs inside the VNet and resolves Cosmos endpoints through
  `privatelink.documents.azure.com`.
- Changing operator egress IPs no longer require Cosmos firewall changes.
- Human data access is read-only and attributable to the signed-in Entra user.
- The VM has no public RDP path, but its outbound-only public IP remains a
  deliberate POC tradeoff that production should replace with controlled
  egress.
- Bastion and public IP resources remain billable when the VM is deallocated.
- Browser tokens can persist on the VM, so operators must lock sessions and
  clear browser data before transferring access.
