// ============================================================================
// Indigo Video AI Machine - Azure Deployment Template
// Region: westus3
// ============================================================================

@description('The location for all resources')
param location string = 'westus3'

@description('Base name for resources')
param baseName string = 'indigo'

@description('Tags for all resources')
param tags object = {
  Environment: 'Production'
  Project: 'IndigoVideoAI'
}

// ============================================================================
// VARIABLES
// ============================================================================

var uniqueSuffix = uniqueString(resourceGroup().id)
var storageAccountName = toLower('${baseName}stor${uniqueSuffix}')
var searchServiceName = '${baseName}-search-${uniqueSuffix}'
var openAiAccountName = '${baseName}-openai-${uniqueSuffix}'
var videoIndexerName = '${baseName}-vi-${uniqueSuffix}'
var functionAppName = '${baseName}-func-${uniqueSuffix}'
var appServicePlanName = '${baseName}-asp-${uniqueSuffix}'
var managedIdentityName = 'agentindigo'

// ============================================================================
// USER ASSIGNED MANAGED IDENTITY
// ============================================================================

resource managedIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' = {
  name: managedIdentityName
  location: location
  tags: tags
}

// ============================================================================
// STORAGE ACCOUNT (General Purpose + Video Indexer)
// ============================================================================

resource storageAccount 'Microsoft.Storage/storageAccounts@2024-01-01' = {
  name: storageAccountName
  location: location
  tags: tags
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    accessTier: 'Hot'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: true
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    networkAcls: {
      defaultAction: 'Allow'
      bypass: 'AzureServices'
    }
    encryption: {
      services: {
        blob: {
          enabled: true
        }
        file: {
          enabled: true
        }
        queue: {
          enabled: true
        }
        table: {
          enabled: true
        }
      }
      keySource: 'Microsoft.Storage'
    }
  }
}

// Blob Services for Storage Account (with CORS for Video Indexer)
resource blobServices 'Microsoft.Storage/storageAccounts/blobServices@2024-01-01' = {
  parent: storageAccount
  name: 'default'
  properties: {
    deleteRetentionPolicy: {
      enabled: true
      days: 7
    }
    containerDeleteRetentionPolicy: {
      enabled: true
      days: 7
    }
    cors: {
      corsRules: [
        {
          allowedOrigins: [
            'https://*.videoindexer.ai'
          ]
          allowedMethods: [
            'GET'
            'OPTIONS'
          ]
          exposedHeaders: [
            '*'
          ]
          allowedHeaders: [
            '*'
          ]
          maxAgeInSeconds: 200
        }
      ]
    }
  }
}

// Blob container for DR videos
resource drVideosContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2024-01-01' = {
  parent: blobServices
  name: 'dr-videos'
  properties: {
    publicAccess: 'None'
  }
}

// ============================================================================
// AZURE AI SEARCH (Basic SKU)
// ============================================================================

resource searchService 'Microsoft.Search/searchServices@2025-02-01-preview' = {
  name: searchServiceName
  location: location
  tags: tags
  sku: {
    name: 'basic'
  }
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${managedIdentity.id}': {}
    }
  }
  properties: {
    replicaCount: 1
    partitionCount: 1
    hostingMode: 'default'
    publicNetworkAccess: 'Enabled'
    authOptions: {
      aadOrApiKey: {
        aadAuthFailureMode: 'http401WithBearerChallenge'
      }
    }
  }
}

// ============================================================================
// AZURE OPENAI (Cognitive Services)
// ============================================================================

resource openAiAccount 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: openAiAccountName
  location: location
  tags: tags
  kind: 'OpenAI'
  sku: {
    name: 'S0'
  }
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${managedIdentity.id}': {}
    }
  }
  properties: {
    customSubDomainName: openAiAccountName
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      defaultAction: 'Allow'
    }
  }
}

// Deploy GPT-4o model for Video Indexer
resource gpt4oDeployment 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  parent: openAiAccount
  name: 'gpt-4o'
  sku: {
    name: 'Standard'
    capacity: 10
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: 'gpt-4o'
      version: '2024-08-06'
    }
  }
}

// ============================================================================
// AZURE AI VIDEO INDEXER
// ============================================================================

resource videoIndexer 'Microsoft.VideoIndexer/accounts@2024-06-01-preview' = {
  name: videoIndexerName
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${managedIdentity.id}': {}
    }
  }
  properties: {
    storageServices: {
      resourceId: storageAccount.id
      userAssignedIdentity: managedIdentity.id
    }
    openAiServices: {
      resourceId: openAiAccount.id
      userAssignedIdentity: managedIdentity.id
    }
    publicNetworkAccess: 'Enabled'
  }
  dependsOn: [
    storageBlobDataContributorRole
  ]
}

// ============================================================================
// APP SERVICE PLAN (Consumption for Azure Functions)
// ============================================================================

resource appServicePlan 'Microsoft.Web/serverfarms@2024-11-01' = {
  name: appServicePlanName
  location: location
  tags: tags
  sku: {
    name: 'Y1'
    tier: 'Dynamic'
  }
  kind: 'functionapp,linux'
  properties: {
    reserved: true // false = Windows, true = Linux
  }
}

// ============================================================================
// AZURE FUNCTION APP
// ============================================================================

resource functionApp 'Microsoft.Web/sites@2024-11-01' = {
  name: functionAppName
  location: location
  tags: tags
  kind: 'functionapp,linux'
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${managedIdentity.id}': {}
    }
  }
  properties: {
    serverFarmId: appServicePlan.id
    httpsOnly: true
    siteConfig: {
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      linuxFxVersion: 'Python|3.11'
      appSettings: [
        {
          name: 'AzureWebJobsStorage'
          value: 'DefaultEndpointsProtocol=https;AccountName=${storageAccount.name};EndpointSuffix=${environment().suffixes.storage};AccountKey=${storageAccount.listKeys().keys[0].value}'
        }
        {
          name: 'FUNCTIONS_EXTENSION_VERSION'
          value: '~4'
        }
        {
          name: 'FUNCTIONS_WORKER_RUNTIME'
          value: 'python'
        }
        {
          name: 'AZURE_OPENAI_ENDPOINT'
          value: openAiAccount.properties.endpoint
        }
        {
          name: 'AZURE_SEARCH_ENDPOINT'
          value: 'https://${searchService.name}.search.windows.net'
        }
        {
          name: 'AZURE_SEARCH_INDEX_NAME'
          value: 'videos'
        }
        {
          name: 'AZURE_VIDEO_INDEXER_ACCOUNT_ID'
          value: videoIndexer.properties.accountId
        }
        {
          name: 'AZURE_VIDEO_INDEXER_ACCOUNT_NAME'
          value: videoIndexer.name
        }
        {
          name: 'AZURE_VIDEO_INDEXER_LOCATION'
          value: location
        }
        {
          name: 'AZURE_SUBSCRIPTION_ID'
          value: subscription().subscriptionId
        }
        {
          name: 'AZURE_RESOURCE_GROUP'
          value: resourceGroup().name
        }
        {
          name: 'MANAGED_IDENTITY_CLIENT_ID'
          value: managedIdentity.properties.clientId
        }
        {
          name: 'STORAGE_ACCOUNT_NAME'
          value: storageAccount.name
        }
        {
          name: 'FUNCTION_APP_URL'
          value: 'https://${functionAppName}.azurewebsites.net'
        }
      ]
    }
  }
  dependsOn: [
    storageBlobDataContributorRole
  ]
}

// ============================================================================
// ROLE ASSIGNMENTS
// ============================================================================

// Storage Blob Data Contributor for Managed Identity
resource storageBlobDataContributorRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageAccount.id, managedIdentity.id, 'Storage Blob Data Contributor')
  scope: storageAccount
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
    principalId: managedIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// Storage Blob Data Delegator for Managed Identity (required for User Delegation SAS)
resource storageBlobDataDelegatorRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageAccount.id, managedIdentity.id, 'Storage Blob Data Delegator')
  scope: storageAccount
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'db58b8e5-c6ad-4a2a-8342-4190687cbf4a')
    principalId: managedIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// Cognitive Services OpenAI User for Managed Identity (for Function App)
resource cognitiveServicesOpenAiUserRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(openAiAccount.id, managedIdentity.id, 'Cognitive Services OpenAI User')
  scope: openAiAccount
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd')
    principalId: managedIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// Search Index Data Contributor for Managed Identity (required to upload documents)
resource searchDataContributorRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(searchService.id, managedIdentity.id, 'Search Index Data Contributor')
  scope: searchService
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7ca78c08-252a-4471-8644-bb5ff32d4ba0')
    principalId: managedIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// Note: Cognitive Services Contributor and User roles already exist from previous deployment
// If deploying to a new environment, uncomment these:

/*
// Cognitive Services Contributor for Video Indexer OpenAI integration
resource cognitiveServicesContributorRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(openAiAccount.id, managedIdentity.id, 'Cognitive Services Contributor')
  scope: openAiAccount
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '25fbc0a9-bd7c-42a3-aa1a-3b75d497ee68')
    principalId: managedIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// Cognitive Services User for Video Indexer OpenAI integration
resource cognitiveServicesUserRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(openAiAccount.id, managedIdentity.id, 'Cognitive Services User')
  scope: openAiAccount
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'a97b65f3-24c7-4388-baec-2e87135dc908')
    principalId: managedIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}
*/

// ============================================================================
// OUTPUTS
// ============================================================================

@description('Managed Identity Resource ID')
output managedIdentityId string = managedIdentity.id

@description('Managed Identity Client ID')
output managedIdentityClientId string = managedIdentity.properties.clientId

@description('Storage Account Name')
output storageAccountName string = storageAccount.name

@description('Azure AI Search Endpoint')
output searchEndpoint string = 'https://${searchService.name}.search.windows.net'

@description('Azure OpenAI Endpoint')
output openAiEndpoint string = openAiAccount.properties.endpoint

@description('Video Indexer Account ID')
output videoIndexerAccountId string = videoIndexer.properties.accountId

@description('Function App URL')
output functionAppUrl string = 'https://${functionApp.properties.defaultHostName}'

@description('Function App Name')
output functionAppName string = functionApp.name
