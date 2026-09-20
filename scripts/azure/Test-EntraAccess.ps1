#Requires -Version 7.0
<#
.SYNOPSIS
Read-only, fail-closed Entra guard for the personal Portal in public Azure.
.DESCRIPTION
Checks application/service-principal settings and direct user assignments.
With both ContainerAppName and ResourceGroup, also checks deployed Easy Auth
and the registered callback. Does not enable ingress, mutate Azure, or read secrets.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateNotNullOrEmpty()][string]$TenantId,
    [Parameter(Mandatory)][ValidateNotNullOrEmpty()][string]$ClientId,
    [Parameter(Mandatory)][ValidateNotNullOrEmpty()][ValidateCount(1, 100)][string[]]$AllowedUserObjectIds,
    [string]$ContainerAppName,
    [string]$ResourceGroup
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false

function ConvertTo-CheckedGuid {
    param([string]$Value, [string]$Label)
    $parsed = [guid]::Empty
    if ($Value -notmatch '^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$' -or
        -not [guid]::TryParse($Value, [ref]$parsed) -or $parsed -eq [guid]::Empty) {
        throw "$Label must contain nonempty GUID values."
    }
    return $parsed.ToString('D')
}

function Get-Field {
    param($Object, [string[]]$Path)
    $value = $Object
    foreach ($key in $Path) {
        if ($value -isnot [System.Collections.IDictionary] -or -not $value.Contains($key)) { return $null }
        $value = $value[$key]
    }
    return $value
}

function Assert-Guard {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw $Message }
}

function Invoke-AzRead {
    param([string[]]$Arguments, [string]$Label)
    # Commands below are a fixed read-only catalog. Suppress raw Azure error text,
    # which may contain identifiers; report only the failed operation and exit code.
    $global:LASTEXITCODE = 0
    $raw = & az @Arguments --only-show-errors --output json 2>$null
    $code = $LASTEXITCODE
    if ($code -ne 0) { throw "Azure read failed: $Label (exit $code)." }
    try { return ($raw -join "`n" | ConvertFrom-Json -AsHashtable -ErrorAction Stop) }
    catch { throw "Azure read returned invalid JSON: $Label." }
}

$tenant = ConvertTo-CheckedGuid $TenantId 'TenantId'
$client = ConvertTo-CheckedGuid $ClientId 'ClientId'
$allowed = [string[]]@($AllowedUserObjectIds | ForEach-Object { ConvertTo-CheckedGuid $_ 'AllowedUserObjectIds' })
Assert-Guard ($allowed.Count -gt 0) 'At least one allowed user is required.'
Assert-Guard (@($allowed | Sort-Object -Unique).Count -eq $allowed.Count) 'AllowedUserObjectIds must not contain duplicates.'
$hasApp = -not [string]::IsNullOrWhiteSpace($ContainerAppName)
$hasGroup = -not [string]::IsNullOrWhiteSpace($ResourceGroup)
Assert-Guard ($hasApp -eq $hasGroup) 'ContainerAppName and ResourceGroup must be provided together.'

$account = Invoke-AzRead @('account', 'show', '--query', '{tenantId:tenantId,environmentName:environmentName}') 'current account'
Assert-Guard ((Get-Field $account @('tenantId')) -eq $tenant) 'Current Azure account tenant does not match TenantId.'
Assert-Guard ((Get-Field $account @('environmentName')) -eq 'AzureCloud') 'This guard supports public Azure only.'
$app = Invoke-AzRead @('ad', 'app', 'show', '--id', $client, '--query', '{appId:appId,signInAudience:signInAudience,web:web}') 'application registration'
Assert-Guard ((Get-Field $app @('appId')) -eq $client) 'Application registration client ID mismatch.'
Assert-Guard ((Get-Field $app @('signInAudience')) -eq 'AzureADMyOrg') 'Application must be single-tenant (AzureADMyOrg).'
$sp = Invoke-AzRead @('ad', 'sp', 'show', '--id', $client, '--query', '{id:id,appId:appId,accountEnabled:accountEnabled,appRoleAssignmentRequired:appRoleAssignmentRequired}') 'enterprise application'
Assert-Guard ((Get-Field $sp @('appId')) -eq $client) 'Enterprise application client ID mismatch.'
Assert-Guard ((Get-Field $sp @('accountEnabled')) -ceq $true) 'Enterprise application must be enabled.'
Assert-Guard ((Get-Field $sp @('appRoleAssignmentRequired')) -ceq $true) 'Enterprise application must require user assignment.'
$spId = ConvertTo-CheckedGuid (Get-Field $sp @('id')) 'Enterprise application object ID'
$assignmentEndpoint = "https://graph.microsoft.com/v1.0/servicePrincipals/$spId/appRoleAssignedTo"
$next = $assignmentEndpoint + '?$select=principalId,principalType,resourceId'
$seen = [System.Collections.Generic.HashSet[string]]::new()
$assignedUsers = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
while ($next) {
    Assert-Guard ($seen.Add($next) -and $seen.Count -le 20) 'Assignment pagination repeated or exceeded the safety limit.'
    Assert-Guard ($next.StartsWith($assignmentEndpoint + '?', [System.StringComparison]::Ordinal)) 'Unexpected assignment pagination URL.'
    $page = Invoke-AzRead @('rest', '--method', 'GET', '--url', $next) 'direct user assignments'
    Assert-Guard ($page -is [System.Collections.IDictionary] -and $page.Contains('value') -and $page['value'] -is [array]) 'Assignment response is incomplete.'
    foreach ($assignment in $page['value']) {
        Assert-Guard ((Get-Field $assignment @('principalType')) -eq 'User') 'Only direct User assignments are supported; remove group or service-principal assignments.'
        Assert-Guard ((Get-Field $assignment @('resourceId')) -eq $spId) 'Assignment targets a different enterprise application.'
        $principal = ConvertTo-CheckedGuid (Get-Field $assignment @('principalId')) 'Assigned user object ID'
        Assert-Guard ($allowed -contains $principal) 'Enterprise application has an extra assigned user outside the allowlist.'
        [void]$assignedUsers.Add($principal)
    }
    $next = [string](Get-Field $page @('@odata.nextLink'))
}
Assert-Guard ($assignedUsers.SetEquals($allowed)) 'At least one allowed user lacks a direct User app-role assignment.'

if ($hasApp) {
    $container = Invoke-AzRead @('containerapp', 'show', '--name', $ContainerAppName, '--resource-group', $ResourceGroup, '--query', '{id:id,name:name,environmentId:properties.environmentId,fqdn:properties.configuration.ingress.fqdn}') 'container app identity'
    $resourceId = [string](Get-Field $container @('id'))
    $appName = [string](Get-Field $container @('name'))
    $environmentId = [string](Get-Field $container @('environmentId'))
    $fqdn = [string](Get-Field $container @('fqdn'))
    Assert-Guard ($resourceId -match '^/subscriptions/[0-9a-fA-F-]{36}/resourceGroups/[a-zA-Z0-9_().-]+/providers/Microsoft\.App/containerApps/[a-zA-Z0-9-]+$') 'Container App resource ID is invalid.'
    Assert-Guard ($appName -match '^[a-z][a-z0-9-]{0,30}[a-z0-9]$' -and $appName -eq $ContainerAppName -and $resourceId.EndsWith("/containerApps/$appName", [System.StringComparison]::OrdinalIgnoreCase)) 'Container App name is missing or mismatched.'
    Assert-Guard ($environmentId -match '^/subscriptions/[0-9a-fA-F-]{36}/resourceGroups/[a-zA-Z0-9_().-]+/providers/Microsoft\.App/managedEnvironments/[a-zA-Z0-9-]+$') 'Managed environment resource ID is invalid.'
    $environmentUrl = "https://management.azure.com${environmentId}?api-version=2026-01-01"
    $environment = Invoke-AzRead @('rest', '--method', 'GET', '--url', $environmentUrl) 'managed environment domain'
    $defaultDomain = [string](Get-Field $environment @('properties', 'defaultDomain'))
    $hostnamePattern = '^(?=.{1,253}$)(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$'
    Assert-Guard ($defaultDomain -match $hostnamePattern) 'Managed environment defaultDomain is missing or invalid.'
    $publicFqdn = "$appName.$defaultDomain"
    $internalFqdn = "$appName.internal.$defaultDomain"
    Assert-Guard ($fqdn -match $hostnamePattern -and ($fqdn -eq $publicFqdn -or $fqdn -eq $internalFqdn)) 'Container App FQDN does not match its managed environment and app name.'
    $authUrl = "https://management.azure.com${resourceId}/authConfigs/current?api-version=2026-01-01"
    $authResponse = Invoke-AzRead @('rest', '--method', 'GET', '--url', $authUrl) 'Container App authentication'
    $auth = Get-Field $authResponse @('properties')
    Assert-Guard ((Get-Field $auth @('platform', 'enabled')) -ceq $true) 'Container App authentication platform must be enabled.'
    Assert-Guard ((Get-Field $auth @('globalValidation', 'unauthenticatedClientAction')) -ceq 'RedirectToLoginPage') 'Anonymous requests must redirect to login.'
    Assert-Guard ((Get-Field $auth @('globalValidation', 'redirectToProvider')) -ceq 'azureactivedirectory') 'Login redirect provider must be Azure Active Directory.'
    Assert-Guard (@((Get-Field $auth @('globalValidation', 'excludedPaths')) | Where-Object { $null -ne $_ }).Count -eq 0) 'Authentication excludedPaths must be empty.'
    Assert-Guard ((Get-Field $auth @('httpSettings', 'requireHttps')) -ceq $true) 'Authentication must require HTTPS.'
    $apiPrefix = Get-Field $auth @('httpSettings', 'routes', 'apiPrefix')
    Assert-Guard ($null -eq $apiPrefix -or $apiPrefix -ceq '/.auth') 'Authentication API prefix must be /.auth.'
    $providers = Get-Field $auth @('identityProviders')
    Assert-Guard ($providers -is [System.Collections.IDictionary]) 'Identity providers are missing.'
    foreach ($key in $providers.Keys) {
        # Azure may return null/empty provider defaults; any configured extra
        # provider, even disabled, is outside this deliberately narrow contract.
        if ($key -cne 'azureActiveDirectory' -and $null -ne $providers[$key]) {
            $value = $providers[$key]
            Assert-Guard ($value -is [System.Collections.IDictionary] -and $value.Count -eq 0) 'Additional identity providers are not allowed.'
        }
    }
    $aad = Get-Field $providers @('azureActiveDirectory')
    Assert-Guard ((Get-Field $aad @('enabled')) -ceq $true) 'Azure Active Directory provider must be enabled.'
    Assert-Guard ((Get-Field $aad @('registration', 'clientId')) -eq $client) 'Authentication client ID does not match the approved application.'
    Assert-Guard ((Get-Field $aad @('registration', 'openIdIssuer')) -ceq "https://login.microsoftonline.com/$tenant/v2.0") 'Authentication issuer must match the exact tenant v2.0 issuer.'
    $policy = Get-Field $aad @('validation', 'defaultAuthorizationPolicy')
    $identities = @((Get-Field $policy @('allowedPrincipals', 'identities')) | Where-Object { $null -ne $_ } | ForEach-Object { ConvertTo-CheckedGuid $_ 'Authentication allowed identities' })
    Assert-Guard ($identities.Count -eq $allowed.Count -and @($identities | Sort-Object -Unique).Count -eq $allowed.Count -and @($identities | Where-Object { $allowed -notcontains $_ }).Count -eq 0) 'Authentication allowed identities must exactly match the user allowlist.'
    Assert-Guard (@((Get-Field $policy @('allowedPrincipals', 'groups')) | Where-Object { $null -ne $_ }).Count -eq 0) 'Authentication allowed groups must be empty.'
    Assert-Guard (@((Get-Field $policy @('allowedApplications')) | Where-Object { $null -ne $_ }).Count -eq 0) 'Authentication allowed applications must be empty.'
    Assert-Guard (@((Get-Field $aad @('validation', 'jwtClaimChecks', 'allowedGroups')) | Where-Object { $null -ne $_ }).Count -eq 0) 'Authentication JWT group allowlist must be empty.'
    # Publishing changes app.internal.<domain> into app.<domain>. Validate the
    # future public callback before opening ingress, and the same URI afterward.
    $callback = "https://$publicFqdn/.auth/login/aad/callback"
    $redirectUris = @(Get-Field $app @('web', 'redirectUris'))
    Assert-Guard ($redirectUris -ccontains $callback) 'Application registration is missing the exact Container App HTTPS callback.'
}

[pscustomobject]@{
    EntraAccess = 'Passed'
    AllowedUserCount = $allowed.Count
    ContainerAppAuth = $(if ($hasApp) { 'Passed' } else { 'NotChecked' })
    ReadOnly = $true
}
