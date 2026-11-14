# Gooseによる依存脆弱性の修正候補

`security_loop.py`は、GitHubのCVE watchが保存したOSV SARIFとDependabot alertを照合し、**一件のnpm依存脆弱性**を選ぶ。Gooseに修正候補を作らせ、別の一時作業ツリーで検査と再走査を行い、Developer拡張を持たない別のGoose実行で差分をレビューする。`validate`はコミット済みPR候補を同じ構造条件で再検査する。結果は段階別JSONに保存する。PR作成、GitHub上の正式レビュー、承認、merge、配備はこのCLIの機能ではない。

入力を同じGitHub main commitに固定する。OSVの`evidence.json`に記録されたcommit、lockfile SHA-256、SARIF SHA-256、scanner版と実体SHA-256、検出行数を照合する。Dependabot alertは`open`、npm package名・GHSA/CVEがSARIFと一致し、`first_patched_version.identifier`が導入版より新しい場合だけ候補になる。その固定版が[npm公式Registry API](https://github.com/npm/registry/blob/main/docs/REGISTRY-API.md)で公開済みであることも確認する。404なら次候補へ進み、API障害時は選定を停止する。修正版がない場合や、同じadvisoryのPRが既に開いている場合は停止する。自由文に書かれた「修正版」をGooseが選ぶことは認めない。

## 入力と実行

Python 3.12以上、Git、pnpm、固定版Goose 1.54.0、固定版OSV-Scanner 2.7.0を使う。Gooseの固定SHA-256は`security_loop.py`の`GOOSE_SHA256`、OSV-Scannerの取得元と固定SHA-256は[`cve-watch.yml`](../../.github/workflows/cve-watch.yml)を参照する。GooseのCLI実行と拡張設定は[公式CLI資料](https://goose-docs.ai/docs/guides/goose-cli-commands/)と[公式設定資料](https://goose-docs.ai/docs/guides/config-files/)に従う。

`alerts.json`と`prs.json`はGitHub APIの全ページを取得した側が以下の包みに入れる。`repository`は取得先の`owner/repo`で、`select`はソースの`origin`とも一致させる。取得失敗やページ不足時は`complete: false`にし、`select`を停止させる。PRの`title`、`body`、`head_ref`にはcase IDまたはadvisoryを含める。GitHubのPR一覧は最新状態を選定直前に取り直す。

```json
{"complete": true, "repository": "yomote/tech-playground", "alerts": [{"state": "open", "dependency": {}, "security_advisory": {}, "security_vulnerability": {}}]}
```

```json
{"complete": true, "repository": "yomote/tech-playground", "pulls": [{"number": 12, "state": "open", "title": "...", "body": "...", "head_ref": "..."}]}
```

出力をGit管理外の`.artifacts/`などへ置き、ソースcheckoutを清潔にして実行する。以下の`--check`は実行コマンドのJSON配列であり、shell文字列として解釈しない。

```bash
python3 demos/blue-team-agent/security_loop.py select \
  --repo . --repository yomote/tech-playground \
  --sarif .artifacts/cve-watch/results.sarif \
  --evidence .artifacts/cve-watch/evidence.json \
  --dependabot-alerts .artifacts/blue-team/alerts.json \
  --open-prs .artifacts/blue-team/prs.json \
  --out .artifacts/blue-team/case.json

python3 demos/blue-team-agent/security_loop.py propose \
  --repo . --case .artifacts/blue-team/case.json \
  --goose .artifacts/goose-agentgateway-20261016/goose-v1.54.0-linux-amd64 \
  --out .artifacts/blue-team/proposal.json

python3 demos/blue-team-agent/security_loop.py verify \
  --repo . --case .artifacts/blue-team/case.json \
  --proposal .artifacts/blue-team/proposal.json \
  --scanner .artifacts/security-tools/osv-scanner \
  --check '["pnpm","install","--frozen-lockfile"]' \
  --check '["pnpm","typecheck"]' \
  --check '["pnpm","test"]' \
  --out .artifacts/blue-team/verification.json

python3 demos/blue-team-agent/security_loop.py review \
  --proposal .artifacts/blue-team/proposal.json \
  --verification .artifacts/blue-team/verification.json \
  --goose .artifacts/goose-agentgateway-20261016/goose-v1.54.0-linux-amd64 \
  --out .artifacts/blue-team/review.json

# PR候補をcommitした後、CIでmain由来のCLIから再検査する
python3 demos/blue-team-agent/security_loop.py validate \
  --repo . --case .blue-team/security-case.json \
  --out .artifacts/blue-team/validation.json
```

`select`はseverity順で公開済み固定版を持つ一件を選び、`caseId`、`ruleId`、`packageName`、`installedVersion`、`fixedVersion`、`baseSha`、入力hashを出す。特定の案件を再現する場合は`--advisory`と`--package`を指定できる。`propose`は同じcommitから一時git worktreeを作り、Goose終了後に変更を`pnpm-workspace.yaml`と`pnpm-lock.yaml`へ限定する。既存workspace設定・lockfile importerを保持し、別のクリーンworktreeでpnpm 11.19.0に同じoverrideからlockfileを再計算させ、候補lockfile全体と比較する。これにより対象の依存閉包に必要な`qs`などの更新は通し、無関係の削除は拒否する。Goose案が空か構造検査に不合格なら、その案を破棄し、元commitから固定版overrideとpnpm再計算で候補を作る。JSONの`producer`は`goose`か`deterministic_fallback_after_goose_rejection`を示す。Goose起動失敗、時間切れ、許可外ファイル変更では代替案を作らず停止する。`proposal.patch`は0600で保存し、同じcommitから作る検証用worktreeへ適用する。`verify`は明示した検査に加えてOSVを再実行し、同じpackage/advisoryの行が消えたことを確かめる。`review`は別のGooseプロセスで差分と検査結果を読む。各段階は出力を上書きせず、失敗時は`status: blocked`と理由をJSONへ書き、終了コード2を返す。

## 権限と未検証範囲

Goose修正実行にはGitHub tokenを渡さず、一時HOME・`GOOSE_PATH_ROOT`・`--no-profile`で既存拡張を読み込まない。修正実行にだけDeveloper拡張と`GOOSE_MODE=auto`を与える。レビュー実行は`--no-profile`でDeveloper拡張を持たない。モデルの標準出力・標準エラーはhashとバイト数だけを記録し、生の対話は保存しない。検査コマンドにもGitHub tokenを渡さない。

**git worktreeはOSサンドボックスではない。** GooseのDeveloper拡張と検査コマンドは、このCLIだけではhostのファイルや通信へ完全に隔離されない。実際の自動PRには隔離実行環境と、選定直前のGitHub alert・PR再照会、PR headの必須検査が必要である。別Gooseの`APPROVE`はレビュー草稿であり、GitHub上の別主体レビューや保護規則上の承認ではない。そのため全stageの`publishable`は`false`のままにする。PR作成・mergeの実行主体はGooseと別にし、最新head・承認・保護規則・反映後走査を照合する。

焦点検査：`python3 -m unittest discover -s demos/blue-team-agent -p 'test_security_loop.py' -v`。テストのGooseとscannerは偽実行体であり、実CVEの修正や隔離の安全性を示さない。

2026-11-14のローカル検証では、GitHub main `f715deb`から別worktreeを作り、`body-parser`の`2.4.0` overrideを追加してpnpm 11.19.0で再計算した2,574バイトの差分（`qs`の依存更新を含む）が構造ガードを通った。同日のGoose候補でworkspaceの`apps/*`と`allowBuilds`、lockfileのimporterを削った55,142バイトの差分は拒否した。これは**ローカルの合成alertによる候補検査**であり、実GitHub alertを選定した証拠でもPR反映の証拠でもない。

## Agentgateway経由のモデル通信試行

`gateway_trial.py`は、`propose`と`review`のGooseモデル通信を、JWTを要求するAgentgateway 1.6.0へ通す。許可モデルは`qwen3:8b`と`qwen3.5:35b`の二つで、JWT subject別の[モデル認可規則](https://agentgateway.dev/docs/standalone/latest/documentation/configuration/security/http-authz/)を付け、ローカルOllamaへ転送する。`verify`はモデルを呼ばない。GatewayのOTLP出力はローカルの検査受信器で受け、case IDに結び付けたtrace ID、親span、モデル名、Gatewayが記録したJWT subject別の要求数だけを保存する。受信器は**Langfuse本体ではない**。`X-Playground-Run-Id`と`traceparent`は相関用ヘッダーであり、認可の根拠にはしない。

実行にはPython 3.12以上と[`requirements-control-plane-trial.txt`](requirements-control-plane-trial.txt)のPythonパッケージ、固定版Goose 1.54.0、Agentgateway 1.6.0、OSV-Scanner 2.7.0、ローカルOllamaの上記二モデル、Node 22とpnpm 11.19.0が要る。入力caseと`--repo`は同じmain commitを指し、checkoutは清潔でなければならない。ポート16520、16522、16523を空けてから実行する。

```bash
python demos/blue-team-agent/gateway_trial.py \
  --repo /path/to/clean-main-checkout \
  --case /path/to/case.json \
  --goose /path/to/goose-v1.54.0-linux-amd64 \
  --gateway /path/to/agentgateway-v1.6.0-linux-amd64 \
  --scanner /path/to/osv-scanner-v2.7.0-linux-amd64 \
  --output .artifacts/blue-goose-gateway-trial.json
```

CLIは秘密鍵、JWT、プロンプト、patch本文、raw span、Gatewayログを一時領域から外へ保存しない。GooseのDeveloper拡張によるローカルのファイル・shell操作はGatewayを通らず、隔離や統制の実証にはならない。試行結果はPR作成・別主体のGitHubレビュー・mergeの証拠にもならない。

`agentmesh_trial.py`は、AGT 4.1.0のAgentMesh Registryへ合成Agentを登録し、版違いと失効後に登録メタデータが一致しなくなることを確認する。`python demos/blue-team-agent/agentmesh_trial.py`で実行できる。Registryはメモリ内で、資格情報は未検証のfixtureである。結果の`metadata_match=true`はGoose起動の許可ではない。実起動の制御、永続化、GatewayとToolへの主体連携は[統制設計と受入条件](../../docs/ai-platform-v38/goose-security-control-plane-20261114.md)に残している。

`--authorization-only`はGooseを起動せず、JWTなし、未登録モデル、主体に許可していないモデルの拒否を確認する。OTLP検査で報告する「本文属性名なし」は既知の属性名に限った観測であり、**全属性の安全性は未確認**。本文を扱う別の属性名が増える場合は受信前の設定と全属性を改めて確認する。`trialPassed`はGoose自身が候補を作成し、検証と別レビューが通り、既知の本文属性名を観測しない場合だけ真になる。

認可だけの再現コマンドは次のとおり。2026-11-14の実測では、JWTなし401、未登録モデル404、propose主体からreviewモデル403、review主体からproposeモデル403だった。別の全段階試行ではGoose自身の候補作成と再走査は通ったが、reviewが指定JSONを返さず`trialPassed: false`となった。Langfuse本体への接続・読戻しは未実施である。

```bash
python demos/blue-team-agent/gateway_trial.py --repo /path/to/clean-main-checkout --case /path/to/case.json --goose /path/to/goose-v1.54.0-linux-amd64 --gateway /path/to/agentgateway-v1.6.0-linux-amd64 --scanner /path/to/osv-scanner-v2.7.0-linux-amd64 --output .artifacts/blue-goose-gateway-auth.json --authorization-only
```
