# B2B受注の出荷例外対応

卸売会社の合成受注で欠品または配送締切の問題が起きたとき、受注担当者が対処案を検討し、顧客の回答と出荷責任者の承認を経て、引当と出荷指示を保存するデモ。業務と3役の技術検証条件はシナリオ（候補外の開発記録：`../../docs/ai-platform-v38/b2b-order-exception-scenario-20261019.md`）、実装の受入状況は[BACKLOG](BACKLOG.md)に記録する。

2026-10-19時点で、合成受注の顧客回答、模擬役割での出荷承認、引当・出荷指示の保存と再読込を業務APIとReact/MUI画面で確認した。対処案の初期データは合成fixtureであり、AI Agentが生成した案ではない。M0の実測（候補外の開発記録：`../../docs/ai-platform-v38/order-exception-m0-20261019.md`）に範囲を記録した。

受注画面から実MAF Magenticの調査runを開始・再表示できる。ローカルOllamaへ直接接続した実API run（候補外の開発記録：`../../docs/ai-platform-v38/order-exception-run-boundary-20261019.md`）では二つの専門Agentによる7資料の読取りと案の提出を保存したが、管理Agentの終了判定に達せず180秒で時間切れになった。完成案の保存と出荷への採用は未受入。認証、OpenFGA、Agentgateway、Langfuse、Gooseはこの受注runへ未接続である。

## ローカルで操作する

リポジトリのルートで画面をビルドし、loopbackの業務APIを起動する。

```sh
pnpm install
pnpm --filter @playground/order-exception-saas build
python3 demos/order-exception-saas/server.py --port 5340
```

ブラウザーで`http://127.0.0.1:5340/`を開く。受注一覧から`SO-24017`を選ぶと、AI調査が主操作になり、固定の対処案は手作業用の欄にある。AI調査にはMAFをインストールしたPython環境と`ORDER_AGENT_MODEL_ID`、`ORDER_AGENT_API_KEY`、`ORDER_AGENT_BASE_URL`、`ORDER_AGENT_ROUTE`、`ORDER_AGENT_PROTOCOL`の設定が必要である。未設定なら開始を拒否し、手作業で顧客回答、出荷承認、出荷指示を続けられる。データは`demos/order-exception-saas/.runtime/orders.sqlite3`に保存する。最初から試す場合は新しいSQLiteファイルを`--db`へ指定する。画面には合成データと認可未接続を表示する。出荷承認の`actorRole`は試験用の自己申告であり、業務利用者の実権限ではない。

## 局所実モデルの隔離試行

上記の固定案画面とは別に、[隔離試行のソースと起動条件](trial/README.md)を置いた。合成受注でAgentが受注・在庫と便・契約をToolで読み、合成倉庫担当者の在庫補正後に旧調査を停止し、現行条件の案2件を保存した実モデルrunを扱う。本人限定の[旧run画面](https://omote-llm.tail47e89e.ts.net:12443/trial/?run=order-run-68ed697905084e27aeb8a89696e5ab14)から現行の保存案へ進める。画面は保存済み結果を読むため、新しい推論は起こさない。

2026-11-09に隔離版の画面と業務APIをこのリポジトリの`trial/`へ移し、別ポートと複製DBで旧runの停止理由から現行2案までを390pxブラウザーで読んだ。複製DBの受注第6版・倉庫B18箱、顧客回答・承認・出荷なしは前後で同じだった。局所ソースの確認であり、本人限定入口の新しい配備、実資料・実担当者での受入、旧固定案UIとの契約統合ではない。経緯と境界は[BACKLOG](BACKLOG.md)に記録する。
