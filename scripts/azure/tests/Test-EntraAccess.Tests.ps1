#Requires -Version 7.0
# Standalone fixture tests; no Pester install, Azure login, network, or mutations.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$guardPath = Join-Path $PSScriptRoot '../Test-EntraAccess.ps1'
$tenant = '11111111-1111-4111-8111-111111111111'
$client = '22222222-2222-4222-8222-222222222222'
$userA = '33333333-3333-4333-8333-333333333333'
$userB = '44444444-4444-4444-8444-444444444444'
$spId = '55555555-5555-4555-8555-555555555555'
$parameters = @{ TenantId = $tenant; ClientId = $client; AllowedUserObjectIds = @($userA, $userB); ContainerAppName = 'fixture-portal'; ResourceGroup = 'fixture-rg' }

function New-Fixture {
    @{
        account = @{ tenantId = $tenant; environmentName = 'AzureCloud' }
        app = @{ appId = $client; signInAudience = 'AzureADMyOrg'; web = @{ redirectUris = @('https://fixture-portal.example.test/.auth/login/aad/callback') } }
        sp = @{ id = $spId; appId = $client; accountEnabled = $true; appRoleAssignmentRequired = $true }
        assignments = @{ value = @(
            @{ principalId = $userA; principalType = 'User'; resourceId = $spId },
            @{ principalId = $userB; principalType = 'User'; resourceId = $spId }
        ) }
        container = @{ id = '/subscriptions/66666666-6666-4666-8666-666666666666/resourceGroups/fixture-rg/providers/Microsoft.App/containerApps/fixture-portal'; name = 'fixture-portal'; environmentId = '/subscriptions/66666666-6666-4666-8666-666666666666/resourceGroups/fixture-rg/providers/Microsoft.App/managedEnvironments/fixture-env'; fqdn = 'fixture-portal.example.test' }
        environment = @{ properties = @{ defaultDomain = 'example.test' } }
        auth = @{ properties = @{
            platform = @{ enabled = $true }
            globalValidation = @{ unauthenticatedClientAction = 'RedirectToLoginPage'; redirectToProvider = 'azureactivedirectory'; excludedPaths = @() }
            httpSettings = @{ requireHttps = $true; routes = @{ apiPrefix = '/.auth' } }
            identityProviders = @{ azureActiveDirectory = @{
                enabled = $true
                registration = @{ clientId = $client; openIdIssuer = "https://login.microsoftonline.com/$tenant/v2.0" }
                validation = @{ defaultAuthorizationPolicy = @{ allowedPrincipals = @{ identities = @($userA, $userB); groups = @() } } }
            } }
        } }
    }
}

function Assert-Fails {
    param([string]$Name, [scriptblock]$Mutation, [string]$Expected, [hashtable]$Arguments = $parameters)
    $global:EntraGuardFixture = New-Fixture
    & $Mutation $global:EntraGuardFixture
    $caught = $false
    try { & $guardPath @Arguments | Out-Null }
    catch {
        if ($_.Exception.Message -notmatch $Expected) { throw "${Name}: unexpected failure: $($_.Exception.Message)" }
        $caught = $true
    }
    if (-not $caught) { throw "${Name}: guard unexpectedly passed." }
    Write-Output "PASS: $Name"
}

$priorAz = Get-Item Function:\global:az -ErrorAction SilentlyContinue
try {
    function global:az {
        $global:LASTEXITCODE = 0
        $arguments = @($args)
        $command = $arguments -join ' '
        if ($global:EntraGuardFixture.ContainsKey('failCommand')) { $global:LASTEXITCODE = 7; return '{}' }
        $payload = switch -Regex ($command) {
            '^account show ' { $global:EntraGuardFixture.account; break }
            '^ad app show ' { $global:EntraGuardFixture.app; break }
            '^ad sp show ' { $global:EntraGuardFixture.sp; break }
            '^containerapp show ' { $global:EntraGuardFixture.container; break }
            '^rest --method GET --url https://graph\.microsoft\.com/' { $global:EntraGuardFixture.assignments; break }
            '^rest --method GET --url https://management\.azure\.com/.*/managedEnvironments/' { $global:EntraGuardFixture.environment; break }
            '^rest --method GET --url https://management\.azure\.com/.*/authConfigs/current\?' { $global:EntraGuardFixture.auth; break }
            default { throw 'Guard attempted an unexpected command; no real Azure CLI was invoked.' }
        }
        return ($payload | ConvertTo-Json -Depth 30 -Compress)
    }
    $global:EntraGuardFixture = New-Fixture
    $result = & $guardPath @parameters
    if ($result.EntraAccess -ne 'Passed' -or $result.ContainerAppAuth -ne 'Passed' -or $result.AllowedUserCount -ne 2) { throw 'Positive fixture did not pass.' }
    Write-Output 'PASS: matching tenant, two direct users, deployed auth and callback'
    $global:EntraGuardFixture.container.fqdn = 'fixture-portal.internal.example.test'
    $result = & $guardPath @parameters
    if ($result.ContainerAppAuth -ne 'Passed') { throw 'Internal ingress with future public callback must pass.' }
    Write-Output 'PASS: internal ingress validates future public callback before publishing'
    $entraOnly = @{ TenantId = $tenant; ClientId = $client; AllowedUserObjectIds = @($userA, $userB) }
    $result = & $guardPath @entraOnly
    if ($result.ContainerAppAuth -ne 'NotChecked') { throw 'Preflight must not claim deployed auth was checked.' }
    Write-Output 'PASS: Entra-only preflight explicitly leaves deployed auth unchecked'

    Assert-Fails 'missing assignment' { param($f) $f.assignments.value = @($f.assignments.value[0]) } 'lacks a direct'
    Assert-Fails 'wrong current tenant' { param($f) $f.account.tenantId = $client } 'tenant does not match'
    Assert-Fails 'multi-tenant application' { param($f) $f.app.signInAudience = 'AzureADMultipleOrgs' } 'single-tenant'
    Assert-Fails 'assignment requirement disabled' { param($f) $f.sp.appRoleAssignmentRequired = $false } 'require user assignment'
    Assert-Fails 'group assignment' { param($f) $f.assignments.value[0].principalType = 'Group' } 'Only direct User'
    Assert-Fails 'missing deployed auth' { param($f) $f.auth = @{} } 'platform must be enabled'
    Assert-Fails 'extra allowed user' { param($f) $f.auth.properties.identityProviders.azureActiveDirectory.validation.defaultAuthorizationPolicy.allowedPrincipals.identities += $client } 'exactly match'
    Assert-Fails 'anonymous excluded path' { param($f) $f.auth.properties.globalValidation.excludedPaths = @('/health') } 'excludedPaths must be empty'
    Assert-Fails 'extra identity provider' { param($f) $f.auth.properties.identityProviders.google = @{ enabled = $true } } 'Additional identity providers'
    Assert-Fails 'wrong issuer' { param($f) $f.auth.properties.identityProviders.azureActiveDirectory.registration.openIdIssuer = 'https://login.microsoftonline.com/common/v2.0' } 'exact tenant'
    Assert-Fails 'missing callback' { param($f) $f.app.web.redirectUris = @() } 'missing the exact'
    Assert-Fails 'internal-only callback cannot authorize publishing' { param($f) $f.container.fqdn = 'fixture-portal.internal.example.test'; $f.app.web.redirectUris = @('https://fixture-portal.internal.example.test/.auth/login/aad/callback') } 'missing the exact'
    Assert-Fails 'FQDN outside managed environment' { param($f) $f.container.fqdn = 'fixture-portal.other.test' } 'does not match'
    Assert-Fails 'invalid managed environment ID' { param($f) $f.container.environmentId = 'https://example.test/credentials' } 'environment resource ID is invalid'
    Assert-Fails 'invalid managed environment domain' { param($f) $f.environment.properties.defaultDomain = 'example.test/path' } 'defaultDomain is missing or invalid'
    Assert-Fails 'Azure command failure' { param($f) $f.failCommand = $true } 'Azure read failed'
    Assert-Fails 'unsafe pagination URL' { param($f) $f.assignments['@odata.nextLink'] = 'https://example.test/next' } 'Unexpected assignment pagination'
    $empty = $parameters.Clone(); $empty.AllowedUserObjectIds = @()
    Assert-Fails 'empty user allowlist' {} 'AllowedUserObjectIds|At least one' $empty
    $invalid = $parameters.Clone(); $invalid.TenantId = 'not-a-guid'
    Assert-Fails 'invalid tenant GUID' {} 'TenantId must contain' $invalid
    $unpaired = $entraOnly.Clone(); $unpaired.ContainerAppName = 'fixture-portal'
    Assert-Fails 'unpaired live check arguments' {} 'must be provided together' $unpaired
}
finally {
    Remove-Item Function:\global:az -ErrorAction SilentlyContinue
    if ($priorAz) { Set-Item Function:\global:az $priorAz.ScriptBlock }
    Remove-Variable EntraGuardFixture -Scope Global -ErrorAction SilentlyContinue
}
