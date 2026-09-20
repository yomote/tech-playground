# DevOps Agent と改善ループ

このrepositoryではDevOps Agent v0.1.1を、変更の取得 → review → test plan → 実command実行 → release assessment → evidence保存まで使用します。CIの`readiness`は**自動検証の証拠を確認するjob**です。現在のreview/plannerはmockであり、意味的なAIレビューやrelease承認を実施したことにはなりません。

## 日常の流れ

1. Demoの問いを`demo.yaml`へ書き、独立したvertical sliceとして実装する。
2. 変更した画面・protocol・実serverを必要な範囲で触り、観察をREADME/findingsへ残す。
3. commit済みのcleanなfeature branchで`pnpm devops:check`を実行する。build/typecheck/lint/root tests/Python runner/Decision Workbench contract/CI gate testsは毎回実行する。
4. `pnpm devops:report`と`.devops-agent/runs/`を確認する。mockの終了code 3は「実reviewが必要」。テスト失敗・未実施を成功に読み替えない。
5. PRのActionsでrevisionに結び付いたevidenceを確認し、実レビューを行う。CIの緑だけでrelease可能と判断しない。

初回pushは比較元commitがないためHEAD対HEADのbaselineです。全必須commandを実行し、差分レビューは行っていないとsummaryへ明記します。以降のPRはbase/head SHA、main pushはpush前後のSHAを用います。workflowはread-only token、固定SHAのActionsを使用し、外部アプリへの投稿・自動deployment・Terraform applyは行いません。

## 実AI executorを接続する境界

v0.1.1が提供するのは`mock`と`command` executorです。特定のCodex/Claude CLIやMCP serverを呼べばそのまま利用できる、というAPIは仮定しません。実行環境とcredentialが用意できたら、reviewとtestPlanningそれぞれの設定を`executor: command`へ変更し、検証済みwrapperの実行ファイルと`args`を登録します。workflowはexecutorを強制overrideしていないため、その設定を使用します。

wrapperはstdinの単一JSON（`protocolVersion: 1`、`task`、`instructions`、`input`）を読み、stdoutに契約を満たす単一JSONだけを返します。ログはstderrです。reviewは`summary/risks/findings`、test-planningは`rationale/tests`を返し、正確なfieldとenumは固定versionのdomain schemaで検証します。モデルのJSONを無検証でコマンドとして実行しません。検証commandは登録済みID・command全文・cwdに一致する必要があります。

実executorの追加時には、正常JSON・不正JSON・timeout・異常終了・required test省略・required manual validationを確認してください。credentialは環境変数で渡し、信頼できないPRへ渡しません。AI利用環境未設定の現在は、自動実reviewの完了を主張しません。

## Harness・skill・MCPを改善する基準

| 観察した問題 | 最初に改善する場所 |
| --- | --- |
| 同じ不具合が再発する | 対象Demoの小さな回帰テストとcommand catalog |
| 検証手順を毎回忘れる | repository instructions、必要なら繰り返し使うskill |
| 起動・停止やport待ちに手間がかかる | Demoごとのservice smoke harness |
| 人手でしか見えないprotocolや関係を追う必要がある | structured event/evidenceとMCP toolの契約 |
| mockでは判断できない挙動がある | 実server/SDK用の独立したintegration check |

改善は実際の失敗・操作の手間から一つずつ行い、変更前の問題と検証結果を残します。新しいskillやMCPを増やすこと自体を目的にせず、不要な権限や共通runtime依存を持ち込まないこと。UI browser検証、実OpenFGA、MCP HTTP smoke、credentialを使うMagentic実行、Decision Workbenchの実モデル推論は現在の自動baselineの外です。追加時はserverの起動・終了、port衝突、timeout、credentialなしの扱いまでcommandとして定義してください。

DevOps Agent v0.1.1には手動evidenceのimport機能がありません。実plannerがrequired manual testを追加した場合はblockedになります。結果を偽装せず、実行可能なcheckを登録してreview/plan/verifyをやり直します。`--approve-by`はローカルの人間の申告であり、未実施テストを解除する機能ではありません。

仕様確認元: [v0.1.1 README](https://github.com/yomote/devops-agent/blob/v0.1.1/README.md)、インストール済み同versionのCLI/config schema/policy/command executor実装。導入済みversionを更新する場合は、設定schema・exit code・mock advisoryの文言と`ci-readiness`回帰テストも確認します。
