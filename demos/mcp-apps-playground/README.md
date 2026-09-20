# MCP Apps Playground

## What I want to understand

MCP toolの結果をinteractive UIとして操作できると、何度も自然言語で指示する操作をどこまでUIへ移せるか。toolに渡す入力を「プロンプト比較」か「構造化データ抽出」に変え、異なるフォームが開くExperiment Dashboardで観察します。

**MCP protocol / Apps bridgeは実実装、experiment resultsはdeterministic mockです。実LLMへの呼び出しはありません。**

## Architecture

```text
Local host (browser) ── MCP Streamable HTTP ── MCP server
       │                                        │
       │ resources/read                         └─ Map<experimentId, Experiment>
       ▼
UI resource (self-contained HTML)
       │ App SDK / postMessage
       ▼
AppBridge → tools/call → structuredContent → UI update
```

- `registerAppTool`と`_meta.ui.resourceUri`がtoolとUIを関連づけます。
- `registerAppResource`はSDKの`RESOURCE_MIME_TYPE`を使ってHTMLを返します。
- **返り値にボックスのHTMLが直接入るわけではありません。** tool定義は`ui://experiments/dashboard.html`を参照し、hostが`resources/read`でHTML/JavaScriptを取得します。tool resultの`structuredContent`には実験の値`experiment`とフォーム定義`form`を返し、UIがそれを描画します。
- `kind: prompt-comparison`ではPrompt A/B、Temperature、評価基準。`kind: structured-extraction`では文書、抽出フィールド、欠損時の扱いを表示します。同じUI resourceが異なるフォーム定義を受け取る設計です。
- `form.fields`は**このDemo独自のJSON形式**です。MCP Appsがフォームの標準schemaを規定しているわけではなく、他のアプリではグラフや表なども表示できます。
- UIは`App.ontoolresult`を受け取り、`App.callServerTool`で変更・実行・再取得します。
- `create_experiment(settings)` → `update_experiment(experimentId, settings)` → `run_experiment(experimentId)` → `get_result(experimentId)`。
- 更新すると古い結果を無効化します。不明なhandleはtool errorになります。
- MCP AppsというUI拡張と、MCP core transportのstateless modeは別概念です。HTTP requestごとにserver/transportを作成し、session IDを発行しません。applicationの状態はexplicit handleでprocess内Mapから取得します。再起動で消えるため、永続化・複数process対応を意味しません。

## Run

repository rootで:

```sh
pnpm install
pnpm demo:dev mcp-apps-playground
```

http://localhost:5174 で入力の種類を選び、**Call tool & open app**を押します。フォームの値を変えて実行し、左側の**Observe the loop**で通信を追ってください。**Interactive UI / Tool result JSON**で、同じ結果のUI表示とデータを比較できます。UIを変更したらcommandを再実行してHTMLを再buildしてください。server sourceはwatchします。

MCP Apps対応hostからはStreamable HTTP endpoint `http://localhost:5174/mcp` を登録し、`create_experiment`を呼んでください。stdioも利用できます:

```sh
pnpm --filter @playground/mcp-apps-playground build
pnpm --filter @playground/mcp-apps-playground stdio
```

hostが直接processを起動する場合はdemo directoryをcwdとし、rootの`node_modules/tsx/dist/cli.mjs server.ts --stdio`をNodeで実行します。pnpmのbannerをstdio protocolに混ぜない設定を使ってください。

## Things to try

1. 「プロンプト比較」でtoolを呼ぶ。Prompt A/Bと評価基準を変更して実行し、比較結果とtool result JSONを見比べる。
2. 「構造化データ抽出」で新しいexperimentを開く。フォームの入力欄が変わり、JSONの`form.fields`も変わったことを確認する。
3. 抽出フィールドに`purchase_order`を追加する。文書に値がない場合の`null`と要確認表示を観察し、文書へ`purchase_order: PO-123`を追加して再実行する。
4. 実行すると`update_experiment`と`run_experiment`が呼ばれることを履歴で確認する。UI内の操作もMCP経由です。
5. `experimentId`をコピーし、別MCP clientから`get_result`する。connectionを変えても同じ結果が取れる。
6. serverを再起動して同じhandleを使う。application stateが失われることを確認する。

## Findings

- UIのstateとtransport sessionを分けられる。handleはapplication stateの識別子であり、認可tokenではありません。
- local hostはこのrepositoryのUIだけを読み込む開発用harnessです。第三者のUIを扱う汎用hostではありません。外部hostのCSPやsandbox proxy実装を置き換えるものではありません。
- Mapは最大1,000 experiment。プロンプト比較のscoreは文字列等から作る決定的な疑似値で、品質評価ではありません。抽出は`key: value`形式の行を読む処理で、自由文を理解しません。Latencyもmock値です。
- 構造化抽出のscoreは指定フィールドの充足率、プロンプト比較のscoreは疑似評価です。どちらもUIとtool往復の理解が目的です。
- Material UIでフォームを描画します。UI resourceはself-contained HTMLとしてbuildされ、hostとUIのruntimeはiframe境界で分かれます。
- 真の分散stateless化には外部state store等が必要です。

## References

2026-09-20確認。実装はMCP SDK / ext-apps **2.0.0**で固定。

- [MCP Apps official repository / specification](https://github.com/modelcontextprotocol/ext-apps)
- [Official server example](https://github.com/modelcontextprotocol/ext-apps/tree/main/examples/basic-server-vanillajs)
- [Official host example](https://github.com/modelcontextprotocol/ext-apps/tree/main/examples/basic-host)
