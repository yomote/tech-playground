# GitHub settings as code

このディレクトリをGitHub設定の正本にします。[agent-worldのTerraform構成](https://github.com/yomote/agent-world/tree/main/infra/github)を基に、個人用repository向けに管理範囲を絞っています。

対象は既存の **`yomote/tech-playground` / public / main**。所有者の指示によりpublicで管理します。このコードの追加やCIの成功だけでは、GitHub設定を適用したことにはなりません。Azure resourceは管理しません。

## Managed settings

既定でrepositoryの公開範囲、Issues、squash-only merge、merge後のbranch削除、mainを管理します。ActionsはGitHub製と明示したActionのみを許可し、既定tokenをread-onlyにしてPR自己承認を禁止します。Dependabot vulnerability alerts/security updatesも管理します。repositoryには`prevent_destroy`を設定しています。

次の機能は別途有効化する方針のため、**既定は無効**です。private repositoryへ適用する場合はGitHubプランによる利用可否も確認してください。

| Input | 有効化した場合 |
| --- | --- |
| `enable_main_ruleset` | mainへのPR必須、force push・削除禁止、linear history、未解決thread禁止、`readiness`必須。GitHub Actions App ID `15368`を指定。単独開発のため承認数0、管理者bypassなし。 |
| `enable_azure_environment` | `azure-production` environmentを作成し、deploy元をmain branchだけに限定。管理者bypassなし。 |

無効のままなら、mainのbranch保護やdeployment environmentはこのTerraformでは保証されません。GitHub Freeのprivate repositoryなどで機能が利用できなくても、visibilityをpublicへ変更して回避しません。Secret scanning、push protection、organization policy、CodeQL、GitHub Appも管理対象外です。workflow内のActionはcommit SHAで固定しますが、この構成ではアカウント依存のSHA強制policyを変更しません。

すでに適用したoptional featureをfalseへ戻すと、そのTerraform管理resourceの削除がplanに出ます。機能の停止や削除は差分を確認して判断してください。

## Validate locally

Terraform **1.16.1**とprovider **integrations/github 6.13.0**で検証しています。通常はTerraformをPATHに配置してください。

```sh
terraform -chdir=infra/github fmt -check -recursive
terraform -chdir=infra/github init -backend=false -input=false -lockfile=readonly
terraform -chdir=infra/github validate
terraform -chdir=infra/github test
```

mock provider testはprivate向け既定値とoptional policyを検証し、実GitHubへのアクセスや変更は行いません。validate/test成功は、実環境の権限・プラン対応・driftなしを証明しません。

provider更新時はWindows/Linuxのlock情報を更新してコミットします。

```sh
terraform -chdir=infra/github providers lock -platform=windows_amd64 -platform=linux_amd64
```

## First plan and apply

1. repositoryとmainがすでに存在すること、現在のvisibilityが意図したpublicであることを`gh repo view yomote/tech-playground`で確認します。`imports.tf`は既存repo/settingsをimportするため、repo名が間違っていればplanを失敗させ、新規repoを作りません。
2. `terraform.tfvars.example`を`terraform.tfvars`へコピーします。既存の`gh`管理権限ログイン、またはprocess内の`GITHUB_TOKEN`を使います。tokenはtfvars・stateの任意属性・CI artifactへ書かないでください。
3. optional featureを有効にする場合は、GitHubプラン対応、既存のruleset/classic protection、environmentをAPIで確認します。`readiness`が実際に成功し、check-runの`app.id`が`15368`であることを確認してからrulesetを有効化します。
4. 既存の`tech-playground-main` rulesetは`existing_ruleset_id`へIDを入れます。既存environmentは`import_azure_environment=true`にします。environmentに既存のmain deployment policyがある場合は、次のimportで取り込み、重複作成を防いでください（`POLICY_ID`を実際のIDに置換）。

```sh
terraform -chdir=infra/github init -input=false -lockfile=readonly
terraform -chdir=infra/github import 'github_repository_environment_deployment_policy.azure_main["production"]' 'tech-playground:azure-production:POLICY_ID'
```

既存policyがない場合、このimportコマンドは不要です。環境の確認例:

```sh
gh api repos/yomote/tech-playground/rulesets
gh api repos/yomote/tech-playground/environments
gh api repos/yomote/tech-playground/environments/azure-production/deployment-branch-policies
```

対象と差分を確認してから適用します。

```sh
terraform -chdir=infra/github init -input=false -lockfile=readonly
terraform -chdir=infra/github plan -input=false -out=settings.tfplan
terraform -chdir=infra/github show settings.tfplan
terraform -chdir=infra/github apply settings.tfplan
terraform -chdir=infra/github plan -input=false -detailed-exitcode
```

最後のexit codeは0=差分なし、2=差分あり、1=取得/評価失敗です。403/404やプラン非対応を、設定済み・保護済みとは扱いません。追加の既存rulesetやclassic branch protectionは、この宣言だけでは削除されません。

## State and credentials

初期backendはlocalです。`.terraform/`、state、plan、実tfvarsはGit管理外で、lockとexampleだけをコミットします。stateはアクセス制限した場所へbackupし、同じstateを複数端末から同時にapplyしないでください。共有backendを導入する場合は、ロック・暗号化・アクセス制御を用意して`terraform init -migrate-state`します。

CIにはGitHub管理tokenを渡さず、宣言の検証だけを行います。実plan/applyや定期driftは初期CIに含めません。将来Azure配備を有効にする際のOIDC identity、Azure側role、GitHub variablesの設定は別手順です。

## References

- [GitHub provider 6.13.0](https://github.com/integrations/terraform-provider-github/tree/v6.13.0/docs)
- [Repository ruleset resource](https://github.com/integrations/terraform-provider-github/blob/v6.13.0/docs/resources/repository_ruleset.md)
- [Environment deployment policy and import](https://github.com/integrations/terraform-provider-github/blob/v6.13.0/docs/resources/repository_environment_deployment_policy.md)
- [GitHub rulesets availability](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/about-rulesets)
