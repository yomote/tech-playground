# Azure deployment — Portal

PortalをAzure Container Appsへ公開するための準備です。この手順の追加だけではAzure resourceの作成、imageのpush、公開は行いません。

## 構成

`infra/azure/main.bicep`はresource group単位でContainer Apps environmentとPortalのContainer Appを作成します。Consumption、0.25 vCPU / 0.5 GiB、min 0 / max 1 replica、外部HTTPS ingressを使い、containerの8080番へ接続します。認証は付けないため、公開後のmetadataとREADMEは誰でも閲覧できます。Log Analytics workspaceは作成せず、ログの永続的な収集も設定しません。scale to zeroにはcold startがあり、利用量等に応じたAzure料金は発生し得ます。

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

## Bicepの検証と手動deploy

必要: Azure CLI、Bicep CLI、対象subscriptionのresource group作成・deployment権限。下記はPowerShell用です。`az login`で本人のAzure accountを使用し、subscriptionを明示します。subscription IDは自分の値へ置き換えてください。

```powershell
az login
az account set --subscription '<subscription-id>'
az provider register --namespace Microsoft.App --wait
$portalGroup = 'rg-tech-playground'
$portalLocation = 'japaneast'
$portalPrefix = 'tech-playground'
az group create --name $portalGroup --location $portalLocation
bicep build infra/azure/main.bicep --stdout | Out-Null
az deployment group validate --resource-group $portalGroup --template-file infra/azure/main.bicep --parameters namePrefix=$portalPrefix containerImage=$portalImage
az deployment group what-if --resource-group $portalGroup --template-file infra/azure/main.bicep --parameters namePrefix=$portalPrefix containerImage=$portalImage
```

`$portalImage`は前節でpushしたimage名です。別terminalなら同じ値を設定してください。`namePrefix`は2〜20文字、小文字英数字とhyphen、先頭は英字、末尾は英数字を使います。

差分確認後、実際にdeployするcommandは以下です。

```powershell
az deployment group create --name portal --resource-group $portalGroup --template-file infra/azure/main.bicep --parameters namePrefix=$portalPrefix containerImage=$portalImage
$portalUrl = az deployment group show --name portal --resource-group $portalGroup --query properties.outputs.portalUrl.value --output tsv
Write-Output $portalUrl
Invoke-WebRequest "$portalUrl/healthz"
Invoke-WebRequest "$portalUrl/demos/mcp-apps-playground"
```

更新時はimageを新しいtagでbuild/pushし、`containerImage`を変えて同じdeploymentを実行します。single revision構成で切り替えます。regionの利用可否やquota、imageのpull可否はAzure側のvalidation/deploymentで確認してください。

## 自動化を追加する場合

GitHub Actionsのimage build/pushとAzure deploymentは次の段階です。Azure LoginをOIDCで行うためのfederated identityと対象resource groupへの権限を準備し、client ID・tenant ID・subscription IDをrepository/environment側に設定する構成を想定します。Azureの長期client secretをrepositoryへ置く構成にはしません。private ACRを選ぶ場合はContainer Appのmanaged identityとimage pull権限も別途設定します。

## 検証状況

この準備ではAzure resourceを作成していません。Bicepのlocal compileを確認し、Dockerがない作業環境ではcontainer build/runtime検証は未実施です。公開前に上記のlocal container確認、Azure validation/what-if、deploy後のhealth/detail確認を行ってください。

## References

- [Container Apps Bicep resource](https://learn.microsoft.com/en-us/azure/templates/microsoft.app/containerapps)
- [Managed environment Bicep resource](https://learn.microsoft.com/en-us/azure/templates/microsoft.app/managedenvironments)
- [NGINX Unprivileged image](https://github.com/nginx/docker-nginx-unprivileged)
