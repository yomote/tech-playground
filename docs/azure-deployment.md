# Azure deployment — Portal

PortalをAzure Container Appsへ公開するための準備です。この手順の追加だけではAzure resourceの作成、imageのpush、公開は行いません。

## 構成

`infra/azure/main.bicep`はresource group単位でContainer Apps environmentとPortalのContainer Appを作成します。Consumption、0.25 vCPU / 0.5 GiB、min 0 / max 1 replica、8080番を使います。**既存のMicrosoft Entraテナントで認証し、許可したユーザーのobject IDだけを通します。** Entra認証を無効にするparameterはありません。初期の`publishIngress=false`では外部アクセスを閉じ、認証設定の確認後にHTTPS ingressを公開します。

認証はContainer Appsの組み込み認証（Easy Auth）がnginxの前で処理します。専用Entra applicationはsingle tenant、Enterprise applicationは割り当て必須にします。テナントに所属するだけでは利用できません。`allowedUserObjectIds`は空を許容せず、アプリ割り当てと許可リストの両方で管理します。`/healthz`、静的assets、詳細ページも外部リクエストの認証対象です。Container Appsのprobeはcontainerの8080番へ直接接続します。

client secretはKey Vaultに保存し、専用User Assigned Managed Identityで参照します。Bicepにはsecret値を渡しません。Log Analytics workspaceは作成せず、ログの永続的な収集も設定しません。scale to zeroにはcold startがあり、利用量等に応じたAzure料金は発生し得ます。

Portal imageはReact/Viteの静的buildをnon-root nginxで配信します。`/demos/:id`への直接アクセスはSPAへfallbackし、`/healthz`でhealth checkできます。DemoのPython/Node server、DB、OpenFGA、共通backendは含みません。

## 先に理解しておくDemoリンク

`demo.yaml`とREADMEはimage build時にJSONへ取り込みます。現状の`app.url: http://localhost:5174`等は、閲覧者のPCを指します。AzureにDemoが起動するわけではありません。

公開PortalのOpen Demoは、対応するDemoを自分のPCで起動した場合だけ利用できます。ブラウザのlocal network access制限等で開けない場合は、Demo READMEのURLを別tabへ直接入力してください。全Demoをhostする段階では、各Demoを独立してdeployし、`app.url`とREADMEを更新してPortalを再buildします。

## Local image build

必要: Docker Engine/DesktopのLinux container、build用のinternet接続。repository rootで実行します。依存はimage内でpnpm lockfileから導入し、Portalだけをbuildします。

```powershell
docker build --file infra/azure/portal.Dockerfile --tag tech-playground-portal:local .
docker run --rm --name tech-playground-portal-check --publish 127.0.0.1:8088:8080 tech-playground-portal:local
```

別terminalで確認します。

```powershell
Invoke-WebRequest http://127.0.0.1:8088/healthz
Invoke-WebRequest http://127.0.0.1:8088/demos/mcp-apps-playground
docker exec tech-playground-portal-check id
```

browserで`http://127.0.0.1:8088`を開き、一覧・検索・詳細を確認します。終了はrunしたterminalでCtrl+C。Dockerfile専用ignoreで`.env`、node_modules、実行結果、Demo実装などをbuild contextから除外します。最終imageには静的distだけをコピーします。

## Image registryへpush

Bicepの`containerImage`には**匿名pull可能な公開image**を渡します。private registryのcredentialやmanaged identityによるpullは、この最小構成には含めていません。

以下はGHCRの例です。`your-account`は小文字のaccount名に変更してください。`docker login`の認証promptへregistry用credentialを入力します。公開imageにはPortalへ取り込まれたDemo metadata/READMEが含まれます。

```powershell
$portalOwner = 'your-account'
$portalRevision = git rev-parse --short HEAD
$portalImage = "ghcr.io/${portalOwner}/tech-playground-portal:${portalRevision}"
docker login ghcr.io --username $portalOwner
docker build --platform linux/amd64 --file infra/azure/portal.Dockerfile --tag $portalImage .
docker push $portalImage
```

GHCR packageのvisibilityをPublicに設定し、Azureから匿名pullできる状態にします。build時の変更をcommit済みにしてからcommit hashをtagへ使ってください。再deployでは新しいtag、またはimage digestを指定します。

## Entraと秘密情報の準備

必要: Azure CLI、PowerShell 7、Bicep CLI、既存テナント内でのapplication管理権限と、対象resource groupへのdeployment・role assignment権限。Entra側の権限とAzure subscriptionの権限は別です。以下の手順は準備例であり、このrepositoryへの変更では実行しません。

1. 既存Entraテナント内にTech Playground専用applicationを登録し、サポートするaccountを**この組織ディレクトリのみ**にします。agent-worldのclient IDやclient secretは流用しません。
2. Enterprise applicationの「割り当てが必要ですか？」を「はい」にし、利用させるユーザーを直接割り当てます。初期版はgroupやservice principalの割り当てを扱わず、割り当てたユーザー集合と許可リストの完全一致を確認します。ユーザーobject IDを`allowedUserObjectIds`へ指定します。application/client IDやメールアドレスとは別のIDです。
3. `infra/azure/auth-foundation.bicep`をwhat-ifしてから適用し、専用Managed IdentityとRBAC有効のKey Vaultを作成します。identityの権限はそのvaultのKey Vault Secrets Userに限定します。
4. Entra applicationのclient secretを作成し、Key Vaultへ保存します。secretの値はGit、parameter JSON、ログへ書きません。登録作業者には別途vaultへのsecret書き込み権限が必要です。foundationは作業者への権限を自動付与しません。
5. `infra/azure/portal.local.parameters.json`を作り、下記の値を実環境の値へ置換します。このファイルはGit管理外です。

```json
{
  "$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#",
  "contentVersion": "1.0.0.0",
  "parameters": {
    "namePrefix": { "value": "tech-playground" },
    "containerImage": { "value": "ghcr.io/your-account/tech-playground-portal:COMMIT" },
    "tenantId": { "value": "YOUR-TENANT-GUID" },
    "clientId": { "value": "DEDICATED-APPLICATION-CLIENT-GUID" },
    "allowedUserObjectIds": { "value": ["ALLOWED-USER-OBJECT-GUID"] },
    "authManagedIdentityResourceId": { "value": "/subscriptions/SUB/resourceGroups/RG/providers/Microsoft.ManagedIdentity/userAssignedIdentities/IDENTITY" },
    "authClientSecretKeyVaultUri": { "value": "https://VAULT.vault.azure.net/secrets/SECRET-NAME" },
    "publishIngress": { "value": false }
  }
}
```

## 段階的な配備

既存のsubscriptionとtenantを指定してloginします。以下は実リソースを作成する手順です。各what-ifの差分確認後にcreateを実行してください。`namePrefix`は2〜20文字、小文字英数字とhyphen、先頭は英字、末尾は英数字です。

```powershell
az login --tenant '<existing-tenant-id>'
az account set --subscription '<subscription-id>'
$portalGroup = 'rg-tech-playground'
az group create --name $portalGroup --location japaneast
az deployment group what-if --resource-group $portalGroup --template-file infra/azure/auth-foundation.bicep
az deployment group create --name portal-auth-foundation --resource-group $portalGroup --template-file infra/azure/auth-foundation.bicep
```

foundationのoutputからidentityとvaultを確認し、前節のEntra設定・secret保存・parameterファイルを完成させます。必要に応じてsubscriptionのresource providerを登録してください。

```powershell
$portalParameters = Get-Content infra/azure/portal.local.parameters.json -Raw | ConvertFrom-Json
$portalAccess = @{
  TenantId = $portalParameters.parameters.tenantId.value
  ClientId = $portalParameters.parameters.clientId.value
  AllowedUserObjectIds = [string[]]$portalParameters.parameters.allowedUserObjectIds.value
}
./scripts/azure/Test-EntraAccess.ps1 @portalAccess
if (-not $?) { throw 'Entra preflight failed' }
az deployment group validate --resource-group $portalGroup --template-file infra/azure/main.bicep --parameters '@infra/azure/portal.local.parameters.json'
az deployment group what-if --resource-group $portalGroup --template-file infra/azure/main.bicep --parameters '@infra/azure/portal.local.parameters.json' publishIngress=false
az deployment group create --name portal --resource-group $portalGroup --template-file infra/azure/main.bicep --parameters '@infra/azure/portal.local.parameters.json' publishIngress=false
```

初回createは必ず`publishIngress=false`とします。出力`portalUrl`は**公開後の予定URL**です。このURLの`/.auth/login/aad/callback`をEntra applicationの**Web redirect URI**へ登録します。現在の内部URLは別出力`currentIngressUrl`で、hostnameに`.internal.`を含むためcallbackに流用しません。公開する前に、Entraのユーザー割り当て、single tenant設定、配備済みEasy Authの許可リスト・issuer・HTTPS・公開予定callbackを読み取り検証します。

```powershell
$portalAppName = az deployment group show --name portal --resource-group $portalGroup --query properties.outputs.containerAppName.value -o tsv
./scripts/azure/Test-EntraAccess.ps1 @portalAccess -ContainerAppName $portalAppName -ResourceGroup $portalGroup
if (-not $?) { throw 'Deployed authentication does not match the intended access policy' }
az deployment group what-if --resource-group $portalGroup --template-file infra/azure/main.bicep --parameters '@infra/azure/portal.local.parameters.json' publishIngress=true
az deployment group create --name portal --resource-group $portalGroup --template-file infra/azure/main.bicep --parameters '@infra/azure/portal.local.parameters.json' publishIngress=true
```

公開後にguardを再実行し、匿名アクセスがログイン誘導または401になること、許可ユーザーで表示できること、未許可ユーザーが拒否されることを実browserで確認します。匿名で`/healthz`が200になる確認は行いません。guard失敗時には外部ingressを閉じ、原因を修正してから再公開します。Bicepのcompileや設定値の照合だけでは、実際のログイン・拒否動作まで検証したことにはなりません。

更新でも公開前のguardとwhat-ifを実行し、意図する`publishIngress`を明示します。認証変更は一度falseに戻してから設定を確認する手順を使います。Portalの公開URLが変わる場合はcallbackも更新します。single revision構成でimageを切り替えます。ユーザー追加・削除はEntraの直接割り当てと`allowedUserObjectIds`の両方へ反映してください。

## 自動化を追加する場合

GitHub Actionsのimage build/pushとAzure deploymentは次の段階です。Azure LoginをOIDCで行うためのfederated identityと対象resource groupへの権限を準備し、client ID・tenant ID・subscription IDをrepository/environment側に設定する構成を想定します。Azureの長期client secretをrepositoryへ置く構成にはしません。private ACRを選ぶ場合はContainer Appのmanaged identityとimage pull権限も別途設定します。

## 検証状況

この準備ではTech PlaygroundのAzure resourceやEntra applicationを作成していません。Portal containerのbuild、health、SPA fallback、non-root userはGitHub Actionsで検証済みです。これはnginx container単体の検証であり、Easy Authを含む実Azureの認証確認ではありません。Bicepとread-only guardを検証した後も、実配備時のAzure validation/what-ifと、許可・未許可ユーザーでの確認が必要です。CIはimageのpublishやAzure deploymentを行いません。

## References

- [Container Apps Bicep resource](https://learn.microsoft.com/en-us/azure/templates/microsoft.app/containerapps)
- [Managed environment Bicep resource](https://learn.microsoft.com/en-us/azure/templates/microsoft.app/managedenvironments)
- [NGINX Unprivileged image](https://github.com/nginx/docker-nginx-unprivileged)
- [Container Apps Entra authentication](https://learn.microsoft.com/en-us/azure/container-apps/authentication-entra)
- [Container Apps authConfigs schema](https://learn.microsoft.com/en-us/azure/templates/microsoft.app/containerapps/authconfigs)
