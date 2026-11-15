# AgentDefinition 台帳と実行前判定の局所試行

Goose と MAF の方式を同じ `AgentDefinition` で記述し、ローカルの SQLite 台帳へ版付きで登録する。登録した定義から環境別の `DeploymentBinding` を作り、指定した要求者・モデル経路・Tool が許可範囲に入るか判定する。このデモは **台帳と実行前判定**までで、Agent は起動しない。

## 操作と結果

`trial_registry.py` は、既定ではこのデモの非実行 fixture を隔離した Git リポジトリにコピーして commit する。その commit と artifact の Git OID を固定した Goose・MAF 方式の定義を登録し、台帳を閉じて再読込する。次に、両方式の合成要求を許可し、許可外 Tool・別の要求者・一時停止後・取消後の新規要求を拒否する。結果は `result.json`、定義と判定履歴は `registry.sqlite3` に残る。

```bash
# リポジトリルートから実行。依存は requirements.txt に記載。
python3 demos/agent-definition-contract/trial_registry.py \
  --output-dir .artifacts/agent-registry-trial-local
python3 -m unittest discover -s demos/agent-definition-contract -p 'test_*.py' -q
```

既定の fixture は **Goose Agent や MAF workflow ではない**。既存のソースファイルを版固定したい場合だけ、`--goose-source path/to/file` と `--maf-source path/to/file` を渡す。その場合もソースをコピーして登録するだけで、Agent は起動しない。`--output-dir` は毎回新しいパスを指定する。

## 定義と配備設定

`AgentDefinition` は Agent ID、版、所有者、用途、runner 方式、Git 上の実装、要求するモデル・Tool を記述する。`useCaseId` は同じ用途の別方式を比較するための任意の識別子で、`agentId` は方式ごとに分ける。`requestedCapabilities` は開発者の要求であり、許可ではない。例の `examples/*.agent.yaml` は形式検証用で、Git revision は合成値なので台帳へ登録できない。

`registry.py` は信頼する checkout と `owner/repository` 名を指定して初期化する。登録時に JSON Schema の形式検査、実在する Git commit と artifact の照合、symlink と submodule の拒否を行う。同じ `agentId` と版の内容は変更できない。台帳には定義の一覧・再読込、状態変更、配備設定、判定と状態変更の履歴を保存する。

配備設定は登録済み定義の要求能力以下に制限する。状態は定義が `draft → tested → approved → retired`、配備設定が `planned → active → suspended/retired`。`suspended` と `retired` の配備設定への**新規要求**は拒否する。`retired` は同じ配備 ID では再開できない。局所試行で使う `approved` は API 呼出側が記録した状態名にすぎず、人の承認や認証済み操作者を意味しない。

## 検証範囲と残る実装

`validate_agent.py` は PyYAML の安全読込を使い、重複キー、別名・merge、未知フィールド、secret や任意の `command` 欄を拒否する。入出力・評価の schema 参照は安全な相対 JSON パスに限定する。登録時は参照先 schema の内容、自由文に混入した秘密、Agent の品質を評価しない。

局所台帳は操作者と要求者の文字列を **認証していない**。実行入口・Gateway・Tool 呼出とは未接続であり、判定後のすり替えや実 Tool の強制は防げない。停止は新規要求の判定だけに作用し、稼働中の run を終了しない。共通 run envelope、実 Agent の結果と trace、実 identity による認可、運用者承認、複数環境の配備管理も残る。SQLite ファイル自体の改ざん耐性もない。

したがって、この局所試行は [PLAT-REG-01 と PLAT-RUN-01](../../docs/ai-platform-v38/agent-platform-capability-audit-20261114.md) の一部の実装証拠であり、両項目の受入や共通実行の成立を示さない。次は認証済み主体からの共通入口で Goose・MAF の実 artifact を登録・起動し、同じ配備設定が実 Tool 呼出と停止後の新規実行に適用されるか確認する。
