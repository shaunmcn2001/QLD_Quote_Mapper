@description('Azure region for resources')
param location string = resourceGroup().location

@description('Environment name used in resource names')
param environmentName string

@description('OCI reference for your built image (set by azd)')
param containerImage string

@secure()
@description('API key required by the backend')
param apiKey string

@description('QLD MapServer base URL')
param qldMapserverBase string = 'https://spatial-gis.information.qld.gov.au/arcgis/rest/services/PlanningCadastre/LandParcelPropertyFramework/MapServer'

resource la 'Microsoft.OperationalInsights/workspaces@2022-10-01' = {
  name: 'log-${environmentName}'
  location: location
  properties: {
    retentionInDays: 30
  }
}

resource caenv 'Microsoft.App/managedEnvironments@2023-05-01' = {
  name: 'cae-${environmentName}'
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: la.properties.customerId
        sharedKey: listKeys(la.id, '2022-10-01').primarySharedKey
      }
    }
  }
}

resource api 'Microsoft.App/containerApps@2023-05-01' = {
  name: 'api-${environmentName}'
  location: location
  properties: {
    managedEnvironmentId: caenv.id
    configuration: {
      ingress: {
        external: true
        targetPort: 8000
      }
      secrets: [
        {
          name: 'x-api-key'
          value: apiKey
        }
      ]
      activeRevisionsMode: 'single'
    }
    template: {
      containers: [
        {
          name: 'api'
          image: containerImage
          env: [
            {
              name: 'X_API_KEY'
              secretRef: 'x-api-key'
            }
            {
              name: 'QLD_MAPSERVER_BASE'
              value: qldMapserverBase
            }
          ]
          resources: {
            cpu: 0.25
            memory: '0.5Gi'
          }
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: 2
      }
    }
  }
}

output apiUrl string = 'https://${api.properties.configuration.ingress.fqdn}'
