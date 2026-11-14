targetScope = 'resourceGroup'

param location string = 'eastus2'
param principalId string
param deployClaude bool = true
@minValue(1)
param coordinatorCapacity int = 900
@minValue(1)
param miniCapacity int = 200
@minValue(1)
param claudeCapacity int = 20
@description('Purchasing organization reported to the Claude Marketplace offer (only used when deployClaude is true).')
param claudeOrganizationName string = 'Contoso'
param tags object = {
  project: 'caldova-control-tower'
  environment: 'demo'
}

var suffix = uniqueString(resourceGroup().id)
var accountName = 'caldova-foundry-${suffix}'
var projectName = 'caldova-control-tower'
var foundryUserRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '53ca6127-db72-4b80-b1b0-d745d6d5456d')
var projectManagerRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'eadc314b-1a2d-4efa-be10-5d325db5065e')
var cognitiveUserRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'a97b65f3-24c7-4388-baec-2e87135dc908')
var acrPullRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
var metricsPublisherRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '3913510d-42f4-4e42-8a64-420c390055eb')
var readerRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'acdd72a7-3385-48ef-bd42-f606fba81ae7')
var logAnalyticsReaderRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '73c42c96-874c-492b-b04d-ab87d138a893')

resource foundry 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' = {
  name: accountName
  location: location
  tags: tags
  kind: 'AIServices'
  sku: {
    name: 'S0'
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    allowProjectManagement: true
    customSubDomainName: accountName
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
  }
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-04-01-preview' = {
  parent: foundry
  name: projectName
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    displayName: 'Caldova Control Tower'
    description: 'Supply-chain disruption demonstration'
  }
}

resource coordinatorModel 'Microsoft.CognitiveServices/accounts/deployments@2025-10-01-preview' = {
  parent: foundry
  name: 'gpt-55'
  tags: tags
  sku: {
    name: 'GlobalStandard'
    capacity: coordinatorCapacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: 'gpt-5.5'
      version: '2026-04-24'
    }
  }
  dependsOn: [
    project
  ]
}

resource miniModel 'Microsoft.CognitiveServices/accounts/deployments@2025-10-01-preview' = {
  parent: foundry
  name: 'gpt-54-mini'
  tags: tags
  sku: {
    name: 'GlobalStandard'
    capacity: miniCapacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: 'gpt-5.4-mini'
      version: '2026-03-17'
    }
  }
  dependsOn: [
    coordinatorModel
  ]
}

resource claudeModel 'Microsoft.CognitiveServices/accounts/deployments@2025-10-01-preview' = if (deployClaude) {
  parent: foundry
  name: 'claude-opus-55'
  tags: tags
  sku: {
    name: 'GlobalStandard'
    capacity: claudeCapacity
  }
  properties: {
    model: {
      format: 'Anthropic'
      name: 'claude-opus-5-5'
      version: '2'
    }
    // Required by the provider for Anthropic, but absent from some Bicep type catalogs.
    modelProviderData: {
      organizationName: claudeOrganizationName
      countryCode: 'US'
      industry: 'technology'
    }
  }
  dependsOn: [
    miniModel
  ]
}

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: 'caldovacr${suffix}'
  location: location
  tags: tags
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: false
    publicNetworkAccess: 'Enabled'
  }
}

resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'caldova-logs-${suffix}'
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: 'caldova-insights-${suffix}'
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: workspace.id
    DisableLocalAuth: false
  }
}

resource insightsConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-04-01-preview' = {
  parent: project
  name: 'caldova-appinsights'
  properties: {
    category: 'AppInsights'
    authType: 'ApiKey'
    target: appInsights.id
    credentials: {
      key: appInsights.properties.ConnectionString
    }
    metadata: {
      ApiType: 'Azure'
      ResourceId: appInsights.id
    }
  }
}

resource diagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'caldova-foundry-diagnostics'
  scope: foundry
  properties: {
    workspaceId: workspace.id
    logs: [
      {
        categoryGroup: 'allLogs'
        enabled: true
      }
    ]
    metrics: [
      {
        category: 'AllMetrics'
        enabled: true
      }
    ]
  }
}

resource userFoundryRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(foundry.id, principalId, foundryUserRole)
  scope: foundry
  properties: {
    principalId: principalId
    principalType: 'User'
    roleDefinitionId: foundryUserRole
  }
}

resource userProjectRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(project.id, principalId, projectManagerRole)
  scope: project
  properties: {
    principalId: principalId
    principalType: 'User'
    roleDefinitionId: projectManagerRole
  }
}

resource userCognitiveRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(foundry.id, principalId, cognitiveUserRole)
  scope: foundry
  properties: {
    principalId: principalId
    principalType: 'User'
    roleDefinitionId: cognitiveUserRole
  }
}

resource projectFoundryRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(foundry.id, project.id, foundryUserRole)
  scope: foundry
  properties: {
    principalId: project.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: foundryUserRole
  }
}

resource projectPullRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, project.id, acrPullRole)
  scope: registry
  properties: {
    principalId: project.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: acrPullRole
  }
}

resource projectMetricsRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(appInsights.id, project.id, metricsPublisherRole)
  scope: appInsights
  properties: {
    principalId: project.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: metricsPublisherRole
  }
}

// Monitoring: the presenter reads traces and the agent Monitor dashboard (log-based views).
resource userInsightsLogReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(appInsights.id, principalId, logAnalyticsReaderRole)
  scope: appInsights
  properties: {
    principalId: principalId
    principalType: 'User'
    roleDefinitionId: logAnalyticsReaderRole
  }
}

resource userWorkspaceLogReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(workspace.id, principalId, logAnalyticsReaderRole)
  scope: workspace
  properties: {
    principalId: principalId
    principalType: 'User'
    roleDefinitionId: logAnalyticsReaderRole
  }
}

// Continuous evaluation: the project identity reads agent traces from Application Insights.
resource projectInsightsReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(appInsights.id, project.id, readerRole)
  scope: appInsights
  properties: {
    principalId: project.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: readerRole
  }
}

resource projectWorkspaceLogReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(workspace.id, project.id, logAnalyticsReaderRole)
  scope: workspace
  properties: {
    principalId: project.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: logAnalyticsReaderRole
  }
}

output foundryAccountName string = foundry.name
output foundryAccountId string = foundry.id
output foundryProjectName string = project.name
output foundryProjectId string = project.id
output foundryProjectEndpoint string = 'https://${foundry.name}.services.ai.azure.com/api/projects/${project.name}'
output foundryProjectPrincipalId string = project.identity.principalId
output acrName string = registry.name
output acrLoginServer string = registry.properties.loginServer
output acrId string = registry.id
output appInsightsName string = appInsights.name
output appInsightsId string = appInsights.id
output logAnalyticsWorkspaceId string = workspace.id
