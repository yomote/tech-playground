# 出荷例外の隔離試行ソース

このディレクトリには、合成受注 `SO-24017` の局所実モデル試行で使った業務APIと本人限定入口のソースを置く。リポジトリ直下の `demos/order-exception-saas/server.py` と `src/` は、固定案を含む別のAPI契約である。両者のDBと画面を混用しない。

画面はルートのpnpm workspaceに含まれない。新しい作業環境では、Node 22で `cd demos/order-exception-saas/trial/ui && npm ci && npm run typecheck && npm run build` を実行する。生成した `dist/` はGitに入れず、業務APIの `--dist` に指定する。

保存runをローカルで読む場合は、元DBをSQLiteのbackup APIで別ファイルに複製して `--db` に指定し、業務APIを別ポートで起動する。別端末で `VITE_OE02_LOCAL_NO_AUTH=true npm run dev` を実行すると、`http://127.0.0.1:5346/trial/` が業務APIの別ポートへ `/trial/api` を転送する。初期値は `127.0.0.1:19601`、変更時は `OE02_TRIAL_BACKEND_URL` を指定する。このモードは保存runの局所確認用で、KeycloakとOpenFGAを接続せず、画面でその範囲を表示する。画面の書込ボタンは無効で、Vite経由のAPI書込は405で拒否する。業務APIのポートへ直接送る書込はこの開発用ガードの外なので、必ず複製DBを使う。実認証の在庫補正や新しい実モデルrunを確認した証拠にはしない。

## 起動に必要なもの

この候補の合成受注設定は`backend/scenario-due17.json`を既定で読み、希望日時を2026-11-17 10:00 JST、初期受注版を2にする。倉庫Aの通常便と倉庫Bの特急便は11月16日10:00、倉庫Bの通常便は11月17日10:00の到着予定資料であり、希望日時を変更しても便資料は変更されない。別の合成受注設定を使う場合は、起動前に`OE02_SCENARIO_PATH`へ絶対パスを指定する。設定には`fixtureId`、`orderDueAt`、`initialOrderVersion`の3項目が必要。

この設定では**新規SQLiteファイル**を`--db`へ指定する。起動時にfixtureId、希望日時、初期受注版、便・契約資料のSHA-256をDBの`fixture_meta`へ保存する。同じDBを再起動すると4値をseed・migration前に照合し、不一致や旧schemaを`synthetic_fixture_mismatch`で拒否する。便・契約資料を変更する場合は資料ID・版とfixtureIdも更新し、新規DBで試す。実モデルrunと業務保存結果を保つDBへ合成設定を上書きしない。検査用の固定runは`backend`で`python -m unittest -v test_scenario test_final_reply`を実行する。これは実モデル・本人端末・実担当の受入を示さない。

- Python はリポジトリの `.artifacts/rq03-baseline-20261101/venv/bin/python` を使う。この環境には固定した Microsoft Agent Framework と OpenFGA SDK が必要。`--single-agent` は `agent_framework` を読み、在庫補正の認可は `openfga_sdk` を使う。
- モデル接続には `ORDER_AGENT_MODEL_ID`、`ORDER_AGENT_API_KEY`、`ORDER_AGENT_BASE_URL`、`ORDER_AGENT_ROUTE`、`ORDER_AGENT_PROTOCOL` を設定する。前回の局所試行は `qwen3:8b`、loopback の Ollama `/v1`、`direct`、`chat-completions` を使用した。接続できなければ実モデルの結果は生成されない。
- 業務APIは独立したSQLiteファイルを `--db` で指定する。画面は `trial/ui/dist` を `--dist` で指定する。DB、認証設定、モデル、ビルド済み画面はこのソース移行に含めない。
- 在庫補正を試す場合は、別に起動した局所Keycloak（`127.0.0.1:19580`）、OpenFGA、合成担当者のID対応表と、秘密情報を含む `--inventory-auth-config` JSONが必要。認証処理はこのbackendに同梱した `identity_registry.py` を読み、KeycloakのトークンとOpenFGA判定を業務APIで確認する。他の人役の顧客回答と出荷承認は自己申告の合成操作である。
- 本人限定入口 `owner_entry.py` は、`apps/webhost/tailnet_access.py` の `TailnetViewPolicy` に依存する。既存のTailscale Serveがログインヘッダーを付与するloopback境界の背後でのみ使う。`TP_TAILNET_ORIGIN` と空でない `TP_TAILNET_ALLOWED_LOGINS` が必要。公開issuer、Keycloak転送先、ブラウザーの `/trial/` コールバックはこの試行のURLとポートに固定されているため、別originへ移す場合は認証設定と境界コードを再確認する。

業務APIの起動形は次のとおり。`<別DB>` と `<非公開の認証設定>` は既存ファイルから別途指定し、ポートはrootが共有サービスとの重複を確認する。本人限定画面を使う場合は、画面を先に `trial/ui/dist` へビルドし、既存のKeycloak・OpenFGAとServeの設定を確認する。

```sh
PY=.artifacts/rq03-baseline-20261101/venv/bin/python
ORDER_AGENT_MODEL_ID=qwen3:8b \
ORDER_AGENT_BASE_URL=http://127.0.0.1:11434/v1 \
ORDER_AGENT_ROUTE=direct \
ORDER_AGENT_PROTOCOL=chat-completions \
ORDER_AGENT_API_KEY=ollama-local-placeholder \
"$PY" demos/order-exception-saas/trial/backend/server.py \
  --single-agent --port <別ポート> --db <別DB> \
  --dist demos/order-exception-saas/trial/ui/dist \
  --inventory-auth-config <非公開の認証設定>
```

本人限定入口は業務APIとは別プロセスで、`OE02_BACKEND_PORT` を上記ポート、`OE02_ENTRY_PORT` を入口用の未使用loopbackポートに設定して `python3 demos/order-exception-saas/trial/owner_entry.py` を起動する。入口の `/trial/` を通した実ブラウザー操作には、固定されたTailnet HTTPS origin、Keycloakの公開issuer、Serveのルーティングが必要である。ソースのコピーとPython構文検査は、その起動や本人画面への到達を示さない。

このデモの受注、在庫、契約、便、担当者は合成である。Agentが実モデルを呼ぶ場合でも、案の保存は資料照合を通った結果だけであり、顧客回答、出荷承認、引当、出荷指示は別の業務操作として扱う。保存runの読戻しは新しい推論ではない。

## 保存案から作る未送信文案

現行の局所実モデルrunが2つのToolを読み、構造化した通常便案と特急便案を業務APIへ保存した場合、`GET /api/agent-runs/{runId}` は `customerPreview` を返す。業務APIは保存案とTool応答を照合し、数量、到着予定、追加費用、未確認事項の5行文案を作る。モデルの自由文を顧客へ転用する処理ではない。便の集荷期限・到着予定が過ぎた場合や資料と保存案が食い違う場合は、文案を現在利用できない状態にする。

本人限定入口の合成受注担当者役は、画面の承認済み確認文から一つを選び、理由を入力して `POST /api/orders/SO-24017/customer-preview-review` に未送信レビューを記録できる。業務APIは数量・到着予定・費用・未確認事項の固定行が元の文案と完全一致し、確認文が承認済みの選択肢であることを保存前に検査する。数値や根拠外の文を追加した入力は拒否する。同じ操作IDの同文再送は重複保存しない。保存したレビューは `verified:false`、`sendable:false` であり、顧客回答・出荷承認・引当・出荷指示を作らない。担当者役は自己申告の合成fixtureで、このレビュー操作には実担当のKeycloak/OpenFGA認可を接続していない。

旧版の自由修正文は `legacy_unchecked` として表示する。保存後に新runが始まる、便期限を過ぎる、資料照合に失敗する場合も、保存時の原文・修正文・理由を読取専用の履歴として返す。現在の文案として再使用しない。局所無認証の `VITE_OE02_LOCAL_NO_AUTH=true` ではViteが全POSTを405で拒否し、画面も保存操作を無効にする。書込を伴う確認は本人限定入口と独立した隔離DBで行う。固定行の一致は資料の真偽、自由文全体の意味、契約適合を証明しない。

実モデルが正常完了したrunでは、`AgentResponse.text`を`agent_runs.model_final_reply`へ保存し、GETの`modelFinalReply`で返す。このSDKプロパティはrun中のテキスト内容を集約する。列名とAPIキーは互換性のため維持するが、最後のassistant発話だけかは現行の保存証拠から判定できない。`draft.body`は業務APIが構造化案から作る別の文章である。旧run、時間切れ、資料更新で失敗したrun、固定試験の`modelFinalReply`は`null`。画面は保存テキストを記号のない本文に投影する前に「事実・契約判断は未検証」と示す。2026-11-09の局所実モデルrunは保存テキストに不足と契約の根拠外の断定があったため、業務判断には業務APIの状態と人の確認を使う。保存テキストと案文の違いは実行証拠（候補外の開発記録：`../../../docs/ai-platform-v38/oe02-final-reply-evidence-20261109-1005.json`）に残した。
