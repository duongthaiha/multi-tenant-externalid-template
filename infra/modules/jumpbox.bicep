param environmentName string
param location string
param tags object
param jumpboxSubnetId string
param bastionSubnetId string
param bastionSubnetAddressPrefix string
param operatorPrincipalId string

@allowed([
  'User'
  'Group'
])
param operatorPrincipalType string

param vmSize string
param adminUsername string

@secure()
param adminPassword string

param shutdownTime string
param shutdownTimeZone string

var vmName = 'vm-${environmentName}-jumpbox'
var vmUserLoginRoleDefinitionId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  'fb879df8-f326-4884-b1cf-06f3ad86be52'
)
var readerRoleDefinitionId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  'acdd72a7-3385-48ef-bd42-f606fba81ae7'
)

resource bastionPublicIp 'Microsoft.Network/publicIPAddresses@2024-05-01' = {
  name: 'pip-${environmentName}-bastion'
  location: location
  tags: tags
  sku: {
    name: 'Standard'
  }
  properties: {
    publicIPAllocationMethod: 'Static'
  }
}

resource bastion 'Microsoft.Network/bastionHosts@2024-05-01' = {
  name: 'bas-${environmentName}'
  location: location
  tags: tags
  sku: {
    name: 'Basic'
  }
  properties: {
    ipConfigurations: [
      {
        name: 'bastionIpConfiguration'
        properties: {
          subnet: {
            id: bastionSubnetId
          }
          publicIPAddress: {
            id: bastionPublicIp.id
          }
        }
      }
    ]
  }
}

resource jumpboxNsg 'Microsoft.Network/networkSecurityGroups@2024-05-01' = {
  name: 'nsg-${environmentName}-jumpbox'
  location: location
  tags: tags
  properties: {
    securityRules: [
      {
        name: 'Allow-Bastion-RDP'
        properties: {
          priority: 100
          access: 'Allow'
          direction: 'Inbound'
          protocol: 'Tcp'
          sourcePortRange: '*'
          destinationPortRange: '3389'
          sourceAddressPrefix: bastionSubnetAddressPrefix
          destinationAddressPrefix: '*'
        }
      }
      {
        name: 'Deny-All-Other-Inbound'
        properties: {
          priority: 200
          access: 'Deny'
          direction: 'Inbound'
          protocol: '*'
          sourcePortRange: '*'
          destinationPortRange: '*'
          sourceAddressPrefix: '*'
          destinationAddressPrefix: '*'
        }
      }
    ]
  }
}

resource vmPublicIp 'Microsoft.Network/publicIPAddresses@2024-05-01' = {
  name: 'pip-${environmentName}-jumpbox-egress'
  location: location
  tags: union(tags, {
    purpose: 'jumpbox-outbound-only'
  })
  sku: {
    name: 'Standard'
  }
  properties: {
    publicIPAllocationMethod: 'Static'
  }
}

resource vmNic 'Microsoft.Network/networkInterfaces@2024-05-01' = {
  name: 'nic-${environmentName}-jumpbox'
  location: location
  tags: tags
  properties: {
    networkSecurityGroup: {
      id: jumpboxNsg.id
    }
    ipConfigurations: [
      {
        name: 'ipconfig1'
        properties: {
          primary: true
          privateIPAllocationMethod: 'Dynamic'
          subnet: {
            id: jumpboxSubnetId
          }
          publicIPAddress: {
            id: vmPublicIp.id
          }
        }
      }
    ]
  }
}

resource vm 'Microsoft.Compute/virtualMachines@2024-07-01' = {
  name: vmName
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    hardwareProfile: {
      vmSize: vmSize
    }
    storageProfile: {
      imageReference: {
        publisher: 'MicrosoftWindowsServer'
        offer: 'WindowsServer'
        sku: '2022-datacenter-azure-edition'
        version: 'latest'
      }
      osDisk: {
        name: 'osdisk-${vmName}'
        createOption: 'FromImage'
        deleteOption: 'Delete'
        caching: 'ReadWrite'
        managedDisk: {
          storageAccountType: 'StandardSSD_LRS'
        }
      }
    }
    osProfile: {
      computerName: take(replace(vmName, '-', ''), 15)
      adminUsername: adminUsername
      adminPassword: adminPassword
      windowsConfiguration: {
        provisionVMAgent: true
        enableAutomaticUpdates: true
        patchSettings: {
          assessmentMode: 'AutomaticByPlatform'
          patchMode: 'AutomaticByPlatform'
        }
      }
    }
    networkProfile: {
      networkInterfaces: [
        {
          id: vmNic.id
          properties: {
            primary: true
            deleteOption: 'Delete'
          }
        }
      ]
    }
    diagnosticsProfile: {
      bootDiagnostics: {
        enabled: true
      }
    }
    securityProfile: {
      securityType: 'TrustedLaunch'
      uefiSettings: {
        secureBootEnabled: true
        vTpmEnabled: true
      }
    }
    licenseType: 'Windows_Server'
  }
}

resource entraLoginExtension 'Microsoft.Compute/virtualMachines/extensions@2024-07-01' = {
  parent: vm
  name: 'AADLoginForWindows'
  location: location
  properties: {
    publisher: 'Microsoft.Azure.ActiveDirectory'
    type: 'AADLoginForWindows'
    typeHandlerVersion: '2.2'
    autoUpgradeMinorVersion: true
  }
}

resource vmUserLogin 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(vm.id, operatorPrincipalId, vmUserLoginRoleDefinitionId)
  scope: vm
  properties: {
    roleDefinitionId: vmUserLoginRoleDefinitionId
    principalId: operatorPrincipalId
    principalType: operatorPrincipalType
  }
}

resource bastionReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(bastion.id, operatorPrincipalId, readerRoleDefinitionId)
  scope: bastion
  properties: {
    roleDefinitionId: readerRoleDefinitionId
    principalId: operatorPrincipalId
    principalType: operatorPrincipalType
  }
}

resource nicReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(vmNic.id, operatorPrincipalId, readerRoleDefinitionId)
  scope: vmNic
  properties: {
    roleDefinitionId: readerRoleDefinitionId
    principalId: operatorPrincipalId
    principalType: operatorPrincipalType
  }
}

resource vmReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(vm.id, operatorPrincipalId, readerRoleDefinitionId)
  scope: vm
  properties: {
    roleDefinitionId: readerRoleDefinitionId
    principalId: operatorPrincipalId
    principalType: operatorPrincipalType
  }
}

resource autoShutdown 'Microsoft.DevTestLab/schedules@2018-09-15' = {
  name: 'shutdown-computevm-${vm.name}'
  location: location
  tags: tags
  properties: {
    status: 'Enabled'
    taskType: 'ComputeVmShutdownTask'
    dailyRecurrence: {
      time: shutdownTime
    }
    timeZoneId: shutdownTimeZone
    notificationSettings: {
      status: 'Disabled'
    }
    targetResourceId: vm.id
  }
}

output vmName string = vm.name
output bastionName string = bastion.name
output privateIpAddress string = vmNic.properties.ipConfigurations[0].properties.privateIPAddress
