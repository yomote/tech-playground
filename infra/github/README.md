# GitHub settings as code

このディレクトリをGitHub設定の正本にします。[agent-worldのTerraform構成](https://github.com/yomote/agent-world/tree/main/infra/github)を基に、個人用repository向けに管理範囲を絞っています。

対象は既存の **`yomote/tech-playground` / public / main**。所有者の指示によりpublicで管理します。このコードの追加やCIの成功だけでは、GitHub設定を適用したことにはなりません。Azure resourceは管理しません。

## Managed settings

既定でrepositoryの公開範囲、Issues、squash-only merge、merge後のbranch削除、mainを管理します。ActionsはGitHub製と明示したActionのみを許可し、既定tokenをread-onlyにしてPR自己承認を禁止します。Dependabot vulnerability alerts/security updatesも管理します。repositoryには`prevent_destroy`を設定しています。自動マージは既定で無効です。

次の機能は別途有効化する方針のため、**既定は無効**です。private repositoryへ適用する場合はGitHubプランによる利用可否も確認してください。

| Input | 有効化した場合 |
| --- | --- |
| `enable_main_ruleset` | mainへのPR必須、force push・削除禁止、linear history、未解決thread禁止。別のGitHub actorによる承認1件と、最新のpushに対する承認を要求する。`readiness`・`github-settings-check`・`osv-pnpm`・`blue-security-verify`を必須CIとし、GitHub Actions App ID `15368`を指定。bypass actorなし。 |
| `enable_security_auto_merge` | `enable_main_ruleset=true`の場合だけrepositoryのauto-mergeを許可する。PRごとにauto-mergeを設定しても、上記の別主体承認とCIが成立するまでGitHubがマージを待つ。 |
| `enable_azure_environment` | `azure-production` environmentを作成し、deploy元をmain branchだけに限定。管理者bypassなし。 |

両方の入力を有効にするまではauto-mergeを許可しません。`enable_main_ruleset=false`ならmainのbranch保護はこのTerraformでは保証されません。GitHub Freeのprivate repositoryなどで機能が利用できなくても、visibilityをpublicへ変更して回避しません。Secret scanning、push protection、organization policy、CodeQL、GitHub Appも管理対象外です。workflow内のActionはcommit SHAで固定しますが、この構成ではアカウント依存のSHA強制policyを変更しません。

## Security PRの自動マージを有効にする前の条件

2026-11-14の作業開始時点で、リモートのmainには有効な保護がなく、repositoryの`allow_auto_merge`はfalseです。このディレクトリの変更は**未適用**です。次を満たすまでは両入力をfalseに保ちます。

1. `readiness`、`github-settings-check`、`osv-pnpm`、`blue-security-verify`の4 check-runがPRで毎回実行され、成功し、`app.id=15368`であることをAPIで確認する。`cve-watch.yml`と`blue-security-pr.yml`は全PRでcheckを出す必要があります。`blue-security-verify`は対象のあるPRで修正後の走査とアラート解消を判定し、無関係のPRでもcheckを出します。`osv-pnpm`は脆弱性を検出しても走査証拠を保存して成功するため、その成功だけを修正の証拠にしません。
2. PR作成者または最後のpushを行うactorと**異なるGitHub actor**が、正式な承認レビューを行えることを確認する。Gooseの別プロセスによるレビューはPRの正式承認とは異なります。現行workflowはBotとReviewerに別ユーザーのfine-grained PATを要求し、`/user`のloginが異なることを確認します。この自動承認は人間の品質判定を意味しません。GitHub App installation tokenは`/user`で本人を確認できないため、現行workflowの資格方式には使えません。
3. 修正Agentのrunnerと、PR作成・auto-mergeに使う資格を分離する。`BLUE_ALERTS_TOKEN`はDependabot alertsの読取専用、`BLUE_BOT_TOKEN`はPR作成・branch push・auto-merge設定、`BLUE_REVIEWER_TOKEN`は別ユーザーのPR承認に使います。Botには対象repositoryのContents/PR書込とChecks読取、ReviewerにはPR書込、AlertsにはDependabot alerts読取を設定します。`BLUE_POLICY_TOKEN`はrulesetのlive確認だけに使い、Bot/Reviewerとは別のtokenにします。GitHubが`bypass_actors`を返すにはruleset書込権限が必要なため、対象repositoryのAdministration書込権限を持つ資格を設定管理者が用意します。workflowはこのtokenでrulesets GETだけを呼び、GitHub-hostedのreview-and-merge job内の確認stepに限って渡します。`GITHUB_TOKEN`で作ったPRのCIが起動しない条件を避けるため、Botのfine-grained PATでPRを作成します。Gooseを起動するjobへ書込tokenを渡しません。
4. 既存ruleset/classic protection、プラン対応、`tech-playground-main`のimport要否をAPIとplanで確認する。`enable_main_ruleset=true`は**全PR**へ1件の承認を要求する変更です。bypass actorを空にしていても、repository管理者によるruleset自体の変更権限までは排除しません。

`goose-security-agent.yml`は成功した定期OSV走査から、GitHub alertと既存PRを照合して一件を選びます。専用runner上でGooseが修正案を作り、別のローカルモデルが差分をレビューします。GitHub-hosted jobは選定時のcase hash、修正差分、検証記録を照合してdraft PRを作ります。draftのまま必須CIを待ち、保護設定・別ユーザーによる正式承認を確かめてからauto-mergeします。マージ後はGitHubが返したmerge commit SHAを固定して同じOSVで再走査し、対象が残ればworkflowを失敗させ、SARIFと結果JSONを保存します。現状はworkflowの実装と構文検査までで、実サービスの一巡は未確認です。

workflowの実行Gateはrepository variable `BLUE_SECURITY_ENABLED`です。既定では未設定のためselect jobから停止します。専用runner、4種のtoken、2モデル、PR必須CI、保護規則を確認するまでは`true`に設定しません。Gateを有効にした後も、候補がない走査ではPRを作りません。

専用runnerの`blue-ephemeral-gpu` labelは隔離を証明しません。運用者はrunnerが毎回破棄され、不要な外部通信を遮断し、作業ディレクトリとローカルOllamaに秘密情報がないことを別に確認する必要があります。Gooseと依存関係の検査はこのrunner上でコードを実行します。2モデル（提案`qwen3:8b`、レビュー`qwen3.5:35b`）が事前に利用できない場合、候補jobは停止します。

**自動マージは未実証です。** `BLUE_POLICY_TOKEN`が未設定またはrulesetの書込権限を持たず、GitHubのGETが`bypass_actors`を返さない場合、workflowはdraft PRのまま停止します。このtokenは設定変更も可能な権限を持つため、設定管理者は保管と利用履歴を管理する必要があります。BotへAdministration権限は付けません。現時点のmain rulesetは未適用でauto-mergeも無効のため、実サービスのauto-mergeは実行していません。

条件を確認した後、**二段階で適用**します。最初に`enable_main_ruleset=true`・`enable_security_auto_merge=false`でplan/applyし、リモートのactive ruleset・4 check・承認規則・bypass actor空をAPIで読戻します。次に`enable_security_auto_merge=true`のplan/applyを行い、repo設定を再読込します。両方を同じapplyで初めてtrueにすると、Terraformの操作順によって一時的にauto-mergeだけが有効になり得るため、避けます。停止時は逆にauto-mergeをfalseにして読戻した後、必要ならrulesetを変更します。PRごとのauto-merge設定にはGitHub GraphQLの`enablePullRequestAutoMerge`と`expectedHeadOid`を使い、`gh pr merge`の即時マージ経路、`--admin`、ruleset bypassは使いません。保護規則は設定直前に再読込しますが、管理者による規則変更との競合を完全に原子的には防げません。workflowはマージコミットをOSVで再走査します。GitHub上のDependabot alert状態とmainの別CI結果は、その後に確認します。

確認するGitHub APIの例です。`PR_HEAD_SHA`は対象PRの最新head SHAに置き換えます。

```sh
gh api repos/yomote/tech-playground --jq '{allow_auto_merge,default_branch,visibility}'
gh api repos/yomote/tech-playground/rulesets
gh api repos/yomote/tech-playground/rulesets/RULESET_ID
gh api repos/yomote/tech-playground/commits/PR_HEAD_SHA/check-runs --jq '.check_runs[] | {name,conclusion,app_id:.app.id,head_sha}'
```

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
3. optional featureを有効にする場合は、GitHubプラン対応、既存のruleset/classic protection、environmentをAPIで確認します。`readiness`・`github-settings-check`・`osv-pnpm`・`blue-security-verify`がPRで毎回成功し、各check-runの`app.id`が`15368`であることを確認してからrulesetを有効化します。auto-mergeを有効にする場合は、上の別主体レビュアーと資格分離の条件も確認します。
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
- [Available rules for rulesets](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets)
- [Automatically merging a pull request](https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/automatically-merging-a-pull-request)
- [GitHub GraphQL `enablePullRequestAutoMerge`](https://docs.github.com/en/graphql/reference/pulls#enablepullrequestautomerge)
- [Ruleset GETで返るbypass actorの権限条件](https://docs.github.com/en/rest/repos/rules#get-a-repository-ruleset)
- [Actions `GITHUB_TOKEN` and triggered workflows](https://docs.github.com/en/actions/concepts/security/github_token)
