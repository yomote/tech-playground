# SO-24018 出荷例外の隔離試行

固定の合成受注 SO-24018 で、Agent が受注・在庫と便・契約を別々の Tool で読み、通常便0円と特急便78,000円の案を保存する。通常便は倉庫Bの11月17日10時着となり、希望納期11月16日10時を過ぎる。合成顧客役が分納と遅れる日時を明示承諾し、別認証の合成Bob役が出荷を承認するまで、業務APIは出荷指示を保存しない。

このディレクトリは、SO-24017用の`demos/order-exception-saas/trial/`を上書きせず、SO-24018の稼働候補から実行コードと固定合成資料だけを移した独立したデモである。採否と稼働版の関係は`demos/order-exception-saas/BACKLOG.md`に記録する。`backend/scenario-order18.json`の希望納期、`source_fixtures.py`の便・契約、SQLiteのfixture識別子を一組として扱う。旧受注のDBと混用しない。

## 起動

Node 22で`ui/`の`npm ci && npm run typecheck && npm run build`を実行する。Pythonはリポジトリの固定SDK環境`.artifacts/rq03-baseline-20261101/venv/bin/python`を使う。局所実モデルの推論を試す場合は`ORDER_AGENT_MODEL_ID`、`ORDER_AGENT_API_KEY`、`ORDER_AGENT_BASE_URL`、`ORDER_AGENT_ROUTE=direct`、`ORDER_AGENT_PROTOCOL=chat-completions`を設定し、別のSQLiteファイルを指定して起動する。

```sh
.artifacts/rq03-baseline-20261101/venv/bin/python demos/order-exception-saas-order18/backend/server.py \
  --single-agent --port <未使用のloopbackポート> \
  --db <隔離したDBファイル> \
  --dist demos/order-exception-saas-order18/ui/dist \
  --operation-auth-config <非公開の認証設定JSON>
```

合成Bobの担当認証には既存の局所Keycloak、隔離OpenFGA、合成利用者のID対応表が必要で、設定JSONはGitへ入れない。`owner_entry.py`は既存`apps/webhost/tailnet_access.py`を利用し、Tailscale Serveが付けるログインヘッダーを受けるloopback境界の背後だけで起動する。`TP_TAILNET_ORIGIN`と空でない`TP_TAILNET_ALLOWED_LOGINS`を指定する。Keycloakのissuerと画面のredirect URIはこの試行の`/trial/auth`と`/trial-order18/`に固定しているため、別originでは設定と認証境界の再確認が必要である。

保存runの表示は新しい推論を起こさない。合成資料と人役の操作は実顧客・実社員・実在庫の確認ではない。画面で保存案、モデル自由文、業務APIの状態を別々に表示する。`backend/test_second_order.py`は固定業務規則の焦点検査であり、実モデル・認証・本人限定Serveの実走に代わらない。生成した`ui/dist/`、SQLite、認証設定、証明書、実行ログはソースに含めない。
