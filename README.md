# Tech Playground v0.1

個人用の技術実験monorepo。新しい技術への問いを、独立して動くDemoとして残します。AI/frontend/backendといったカテゴリで分割せず、**Demo単位のvertical slice**で構成します。

## Quick start

必要: Node.js **22+**、pnpm **11.19.0**。MAFの実行にはPython **3.11+**。DockerとLLM credentialは任意です。

```sh
pnpm install
pnpm dev
```

Portal: http://localhost:5173 。`pnpm dev`はPortalだけを起動します。Demoはそれぞれ別terminalで:

```sh
pnpm demo:dev mcp-apps-playground
pnpm demo:dev maf-magentic-scrum
pnpm demo:dev openfga-sharing-playground
```

| Demo | URL | Default | Optional setup |
| --- | --- | --- | --- |
| [MCP Apps Playground](demos/mcp-apps-playground/README.md) | http://localhost:5174 | Real MCP / Apps bridge + mock experiment results | External MCP Apps host |
| [MAF Magentic Scrum](demos/maf-magentic-scrum/README.md) | http://localhost:5175 | Scripted mock agents + actual Python fixture tests | Python SDK + OpenAI API key for live Magentic |
| [OpenFGA Sharing Playground](demos/openfga-sharing-playground/README.md) | http://localhost:5176 | Local mock authorization evaluator | Docker OpenFGA for live checks |

各DemoのOpen Demoリンクは起動を代行しません。先に対応commandを実行してください。各serverはloopbackでlistenします。初期データとmock/liveの区別はUIにも表示します。

環境のproxyやlocalhost名前解決で接続できない場合は、同じportの`http://127.0.0.1:5173`（各Demoは5174〜5176）を使用してください。

## Repository tree

```text
tech-playground/
├─ apps/
│  └─ portal/                     React + Vite; JSON index + Markdown viewer
├─ demos/                         Flat, demo-first vertical slices
│  ├─ mcp-apps-playground/         MCP server / UI resource / local host
│  ├─ maf-magentic-scrum/          Python orchestrator / fixture / trajectory UI
│  └─ openfga-sharing-playground/  Model / server / sharing UI / Docker compose
├─ packages/
│  ├─ demo-schema/                Shared Zod Demo / DemoStatus + search
│  └─ playground-ui/              Optional Material UI theme and presentation components
├─ templates/
│  ├─ blank/
│  ├─ react-vite/
│  ├─ node/
│  └─ python/
├─ tools/
│  ├─ create-demo/
│  ├─ metadata.ts                 Filesystem → validation → JSON index
│  └─ demo-dev.ts
├─ tests/
├─ infra/
│  ├─ github/                     Terraform: repository settings
│  └─ azure/                      Bicep + Portal container preparation
├─ .devops-agent/                 Verification commands and readiness policies
├─ package.json
├─ pnpm-workspace.yaml
├─ pnpm-lock.yaml
└─ turbo.json
```

## Add an experiment

```sh
pnpm demo:new
```

Demo name、id、試したいこと、template、tagsを入力します。日本語titleも可、idはASCII kebab-caseを指定します。`demos/<id>/`に`demo.yaml`、README、template filesを生成します。既存directoryを上書きしません。

生成後は`pnpm install`を実行。Node/React templateにはbuild/typecheck/dev scriptsがあり、Pythonは`python main.py`から始めます。blankはruntimeを決めない出発点です。`demo:dev` helperはpackage.jsonにdev scriptを持つDemo用です。

1. `demo.yaml`のgoal/questionsに、知りたいことを書く。
2. runtimeやDBが必要ならそのDemo内部へ追加する。
3. `app.url`にlocal UIのURLを指定するとPortalにOpen Demoが出る。
4. 観察を`findings`へ追記し、`updatedAt`とstatusを更新する。
5. READMEに起動手順とThings to tryを残す。

## Metadata and Portal

Portalと3つのDemoはMaterial UIを使い、濃い紺〜青紫のprimaryと薄いグレーのsecondaryで統一しています。`@playground/ui`はtheme・panel・shellなど表示だけを共有する任意のpackageです。新しいDemoにReactやこのpackageの採用を要求せず、backend/runtimeは各Demoに閉じています。

source of truthは`demos/*/demo.yaml`。`packages/demo-schema`のZod schemaをCLI・validation・Portalが共用します。READMEは各Demo rootから読み込みます。schemaはidとdirectoryの一致、status、HTTP(S) URL、実在日付、日付順序を検証します。

`tools/metadata.ts`がNode側でfilesystemを読み、`apps/portal/src/generated/demos.json`を生成します。ReactにはNode filesystem APIを持ち込みません。DBも共通backendもありません。

- Search: title / summary / goal / questions / tags / stack / findings。空白区切りのAND検索、大文字小文字を区別しません。
- Filter: status / tag / stack。検索状態はURL queryへ保存。
- Home: Recently Updated、Demo cards、empty state。
- `/demos/:id`: metadata全項目、references、README、optional Open Demo。
- status: `idea`, `exploring`, `working`, `completed`, `paused`, `abandoned`。
- dev中はdemo.yaml/README変更を監視。build時にもindexを再生成。
- 静的配信する場合は`/demos/*`を`index.html`へfallbackするSPA設定が必要です。

## Observe by interacting

| Demo | 最初に試す操作 | 見るポイント |
| --- | --- | --- |
| MCP Apps | 「プロンプト比較」と「構造化データ抽出」でそれぞれtoolを呼ぶ | 同じUI resourceが、tool resultのフォーム定義に応じて異なる入力欄を表示する。UI操作がさらにtoolを呼ぶ。 |
| Magentic Scrum | Mockのtest failureを実行し、Replayする | Managerのagent選択、fixtureへのtool calls、replanまでの経路がeventごとに変わる。 |
| OpenFGA Sharing | Bobの編集権限をCheckし、Bob → Team Xの線を削除して再Checkする | 間接的な認可経路が緑で表示され、関係を切ると権限が変わる。 |

MCP AppsはHTMLをtool resultに直接埋め込む仕組みではありません。このDemoではtool定義がUI resourceを参照し、hostがそのHTMLを取得して、別途返された`structuredContent`をUIへ渡します。フォーム定義のJSON形式はこのDemo独自です。

## Commands and checks

```sh
pnpm build                 # Generate metadata + all JS/TS Demo UIs and Portal
pnpm typecheck             # Root tools/tests + all workspaces
pnpm lint                  # ESLint + shared metadata validation
pnpm test                  # Schema/search, CLI, handles, ReBAC and UI behavior helpers
pnpm metadata              # Generate JSON index
pnpm metadata:validate     # Validate only
```

Pythonの意味のある挙動確認は別commandです:

```sh
cd demos/maf-magentic-scrum
python -m unittest -v test_runner
```

任意のintegration checks（Demo server起動後）:

```sh
pnpm --filter @playground/mcp-apps-playground exec tsx smoke.ts
# Sharing / MAF server + real OpenFGAを起動してから:
pnpm exec tsx tests/integration.ts
# SDK導入後、MAF directoryで。実LLM呼び出しはしない:
.venv/Scripts/python -m unittest -v test_sdk
```

OpenFGAの実server検証には、Dockerのない環境で公式Windows binary **v1.20.0**を使用しています（composeも同version）。Docker compose自体とMAFのcredentialを使った実LLM runは未検証です。変更後の動作確認には上記のchecksと、各DemoのThings to tryを使ってください。

credentialやDockerがなくてもroot install/build/typecheck/lintとPortalは利用可能です。Python依存はpnpm installから導入しません。MAF mockは標準ライブラリだけ、live dependenciesはそのDemoのrequirements.txtから任意で導入します。

## Scope and limitations

- 個人用local labです。user authentication、SaaS、共通DB、共通backend framework、大規模CI/CDは含みません。
- Portalは一覧と観察メモを探す入口です。Demo runtimeの統一や自動service orchestrationは行いません。
- MCPのapplication stateとOpenFGAのmemory datastoreは再起動で失われます。
- MAF mockは実Magenticの性能評価には使えません。liveはAPI利用環境が必要で、結果は非決定的です。
- OpenFGA explanationはdemo側の教育用graph traversal。serverの完全なdecision traceではありません。
- グラフはOpenFGAではtupleの編集、Magenticでは記録されたeventの観察に使います。MagenticのReplayはagentやtoolを再実行しません。
- Demo UI sourceを変更した場合はdemo commandを再実行してbuildしてください。PortalはVite HMR、Node demo serverはtsx watchです。
- lockfileでJS依存を固定。SDKは公式ドキュメントとinstalled APIを2026-09-20に確認しています。

## Next experiments

1. MCP: フォームを実LLM experimentへつなぎ、handle storeをSQLiteへ移してserver再起動後も継続する。
2. Magentic: live trajectoryを複数保存してround/stall/replanを比較し、human plan revisionを加える。
3. OpenFGA: nested teams、conditional tuples、ListObjects、モデル変更の回帰テスト。
4. [Decision Workbench案](docs/decision-workbench-proposal.md): ModernBERT-Large-Instruct / GLiClass-Instructで分類・条件適合・情報不足を比較する。まだ提案段階で、4つ目のDemoは未実装。

## Delivery and agent workflow

- [DevOps AgentとCI](docs/devops.md): 実チェックの実行、証跡保存、reviewの扱い。
- [GitHub設定IaC](infra/github/README.md): agent-worldと同じTerraform方式。既存repositoryをimportして管理。
- [Azure配備準備](docs/azure-deployment.md): PortalだけをContainer Appsへ配備するBicepとcontainer。クラウドリソースは未作成。
- [Agent作業指針](AGENTS.md): 実験の独立性を保ち、実際の課題からハーネス・スキル・MCPを育てる。
