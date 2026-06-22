// ---------------------------------------------------------------------------
// Prior Auth MAF — Main Bicep template
// Deploys: Resource Group, Microsoft Foundry (Resource + Project), Container Registry,
//          Container Apps Environment, Backend + 4 Agent + Frontend Container Apps,
//          Log Analytics, App Insights, Role Assignments (Cognitive Services OpenAI User, Azure AI User)
// ---------------------------------------------------------------------------

targetScope = 'subscription'

// ── Parameters ──────────────────────────────────────────────────────────────

@minLength(1)
@maxLength(64)
@description('Name of the environment (e.g., dev, staging, prod)')
param environmentName string

@minLength(1)
@description('Primary location for all resources. gpt-5.4 GlobalStandard is available in East US 2 and Sweden Central. DataZoneStandard is available in East US 2 only.')
@allowed([
  'eastus2'
  'swedencentral'
])
param location string

@description('Azure OpenAI deployment name to use across all agent containers (e.g., gpt-5.4)')
param azureOpenAIDeploymentName string = 'gpt-5.4'

@description('Deployment SKU: GlobalStandard (default, wider region support) or DataZoneStandard (data residency, East US 2 only).')
@allowed(['GlobalStandard', 'DataZoneStandard'])
param deploymentSkuName string = 'GlobalStandard'

@description('Whether container images have been built to ACR (set automatically by postprovision hook)')
param imagesBuilt string = ''

// MCP server URLs are configured at agent-registration time (see
// scripts/register_agents.py, MEDICAL_MCP_BASE_URL) — not via Bicep params.

// ── Variables ───────────────────────────────────────────────────────────────

var abbrs = loadJsonContent('./abbreviations.json')
var resourceToken = toLower(uniqueString(subscription().id, environmentName, location))
var tags = {
  'azd-env-name': environmentName
  'solution-accelerator': 'prior-auth-maf'
}

// ── Resource Group ──────────────────────────────────────────────────────────

resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: '${abbrs.resourcesResourceGroups}${environmentName}'
  location: location
  tags: tags
}

// ── Container Registry ──────────────────────────────────────────────────────

module containerRegistry './modules/container-registry.bicep' = {
  name: 'container-registry'
  scope: rg
  params: {
    name: '${abbrs.containerRegistryRegistries}${resourceToken}'
    location: location
    tags: tags
  }
}

// ── Log Analytics + Application Insights ────────────────────────────────────

module monitoring './modules/monitoring.bicep' = {
  name: 'monitoring'
  scope: rg
  params: {
    logAnalyticsName: '${abbrs.operationalInsightsWorkspaces}${resourceToken}'
    appInsightsName: '${abbrs.insightsComponents}${resourceToken}'
    location: location
    tags: tags
  }
}

// ── Microsoft Foundry (Resource + Project) ──────────────────────────────────

module aiFoundry './modules/ai-foundry.bicep' = {
  name: 'ai-foundry'
  scope: rg
  params: {
    name: '${abbrs.aiFoundry}${resourceToken}'
    location: location
    tags: tags
    appInsightsInstrumentationKey: monitoring.outputs.appInsightsInstrumentationKey
    appInsightsResourceId: monitoring.outputs.appInsightsResourceId
    deploymentName: azureOpenAIDeploymentName
    deploymentSkuName: deploymentSkuName
  }
}

// ── Container Apps Environment ──────────────────────────────────────────────

module containerAppsEnv './modules/container-apps-env.bicep' = {
  name: 'container-apps-env'
  scope: rg
  params: {
    name: '${abbrs.appManagedEnvironments}${resourceToken}'
    location: location
    tags: tags
    logAnalyticsWorkspaceId: monitoring.outputs.logAnalyticsWorkspaceId
  }
}

// ── Backend Container App ────────────────────────────────────────────────────────

module backend './modules/container-app.bicep' = {
  name: 'backend'
  scope: rg
  params: {
    name: '${abbrs.appContainerApps}backend-${resourceToken}'
    location: location
    tags: union(tags, { 'azd-service-name': 'backend' })
    containerAppsEnvironmentId: containerAppsEnv.outputs.environmentId
    containerRegistryName: containerRegistry.outputs.name
    containerRegistryLoginServer: containerRegistry.outputs.loginServer
    imageName: 'backend'
    targetPort: 8000
    useAcrImage: imagesBuilt == 'true'
    cpu: '1'
    memory: '2Gi'
    minReplicas: 1
    env: [
      // Foundry project endpoint — backend calls Foundry Hosted Agents via the Responses API
      { name: 'AZURE_AI_PROJECT_ENDPOINT', value: aiFoundry.outputs.projectEndpoint }
      // Foundry Hosted Agent names (as registered by scripts/register_agents.py post-deploy)
      { name: 'HOSTED_AGENT_CLINICAL_NAME', value: 'clinical-reviewer-agent' }
      { name: 'HOSTED_AGENT_COVERAGE_NAME', value: 'coverage-assessment-agent' }
      { name: 'HOSTED_AGENT_COMPLIANCE_NAME', value: 'compliance-agent' }
      { name: 'HOSTED_AGENT_SYNTHESIS_NAME', value: 'synthesis-agent' }
      { name: 'HOSTED_AGENT_TIMEOUT_SECONDS', value: '180' }
      { name: 'APPLICATION_INSIGHTS_CONNECTION_STRING', value: monitoring.outputs.appInsightsConnectionString }
      // Debug Console — Foundry-native observability (App Insights KQL + deep-links).
      { name: 'APPLICATION_INSIGHTS_RESOURCE_ID', value: monitoring.outputs.appInsightsResourceId }
      { name: 'AZURE_SUBSCRIPTION_ID', value: subscription().subscriptionId }
      { name: 'AZURE_RESOURCE_GROUP', value: rg.name }
      { name: 'AZURE_AI_PROJECT_ID', value: aiFoundry.outputs.projectId }
      { name: 'FRONTEND_ORIGIN', value: 'https://${abbrs.appContainerApps}frontend-${resourceToken}.${containerAppsEnv.outputs.defaultDomain}' }
    ]
    secrets: []
    healthCheckPath: '/health'
  }
}
// ── Role Assignments ─────────────────────────────────────────────────────────
// Backend → CognitiveServicesOpenAIUser on Foundry (Responses API + agent_reference)
// Foundry project identity → AcrPull on ACR (agent image pull for hosted agents)
// Deployer → Azure AI User is assigned via `az role assignment create` in postprovision hook (idempotent)

module roleAssignments './modules/role-assignments.bicep' = {
  name: 'role-assignments'
  scope: rg
  params: {
    foundryAccountName: aiFoundry.outputs.accountName
    backendPrincipalId: backend.outputs.principalId
    containerRegistryName: containerRegistry.outputs.name
    foundryProjectPrincipalId: aiFoundry.outputs.projectPrincipalId
    appInsightsName: monitoring.outputs.appInsightsName
  }
}
// ── Frontend Container App ──────────────────────────────────────────────────

module frontend './modules/container-app.bicep' = {
  name: 'frontend'
  scope: rg
  params: {
    name: '${abbrs.appContainerApps}frontend-${resourceToken}'
    location: location
    tags: union(tags, { 'azd-service-name': 'frontend' })
    containerAppsEnvironmentId: containerAppsEnv.outputs.environmentId
    containerRegistryName: containerRegistry.outputs.name
    containerRegistryLoginServer: containerRegistry.outputs.loginServer
    imageName: 'frontend'
    targetPort: 80
    useAcrImage: imagesBuilt == 'true'
    minReplicas: 1
    env: [
      { name: 'BACKEND_URL', value: 'https://${abbrs.appContainerApps}backend-${resourceToken}.${containerAppsEnv.outputs.defaultDomain}' }
    ]
    secrets: []
    healthCheckPath: '/'
  }
}

// ── Medical-data MCP Server Container App ────────────────────────────────────
// Self-hosted replacement for the retired DeepSense MCP servers (mcp.deepsense.ai,
// now NXDOMAIN). Foundry hosted agents (clinical, coverage) call it over HTTPS at
// https://<fqdn>/<domain>/mcp. The image is built by the postprovision hook
// (az acr build ./mcp-servers/medical-data); until then it serves a placeholder.

module mcpMedicalData './modules/container-app.bicep' = {
  name: 'mcp-medical-data'
  scope: rg
  params: {
    name: '${abbrs.appContainerApps}mcp-${resourceToken}'
    location: location
    tags: union(tags, { 'azd-service-name': 'mcp-medical-data' })
    containerAppsEnvironmentId: containerAppsEnv.outputs.environmentId
    containerRegistryName: containerRegistry.outputs.name
    containerRegistryLoginServer: containerRegistry.outputs.loginServer
    imageName: 'mcp-medical-data'
    targetPort: 8080
    useAcrImage: imagesBuilt == 'true'
    cpu: '0.5'
    memory: '1Gi'
    minReplicas: 1
    env: []
    secrets: []
    healthCheckPath: '/health'
  }
}

// ── Outputs ─────────────────────────────────────────────────────────────────

output AZURE_RESOURCE_GROUP string = rg.name
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = containerRegistry.outputs.loginServer
output AI_FOUNDRY_ACCOUNT_NAME string = aiFoundry.outputs.accountName
output AI_FOUNDRY_PROJECT_NAME string = aiFoundry.outputs.projectName
output AI_FOUNDRY_ENDPOINT string = aiFoundry.outputs.endpoint
output AI_FOUNDRY_PROJECT_ENDPOINT string = aiFoundry.outputs.projectEndpoint
output AI_FOUNDRY_PORTAL_URL string = aiFoundry.outputs.portalUrl
output BACKEND_CONTAINER_APP_NAME string = backend.outputs.name
output FRONTEND_CONTAINER_APP_NAME string = frontend.outputs.name
output AZURE_OPENAI_DEPLOYMENT_NAME string = azureOpenAIDeploymentName
output APPLICATION_INSIGHTS_CONNECTION_STRING string = monitoring.outputs.appInsightsConnectionString
output frontendUrl string = frontend.outputs.fqdn
output backendUrl string = backend.outputs.fqdn
output MCP_CONTAINER_APP_NAME string = mcpMedicalData.outputs.name
output MEDICAL_MCP_BASE_URL string = 'https://${mcpMedicalData.outputs.fqdn}'
