# PR #16 の同一 HTTP 入力比較（2026-11-16）

`body_parser_oracle.py` を、GitHub main `f715deb90150d3fcfe510fd2e7b8f373ca0a8d5f` と PR #16 head `289e575279b3e6c7f666f0dc328c786d3d982525` の別々の清潔な checkout で実行した。両 checkout には事前に `pnpm install --frozen-lockfile` で依存を導入した。実行時にプログラムが検査するのは、Git head、checkout の清潔さ、実際に導入された版、同一 `server.ts` と Express 版である。導入コマンド自体は出力から証明できない。

同じ OpenFGA デモの局所 HTTP `POST /api/check` へ、正常 JSON、正常 gzip、末尾余剰バイト付き gzip を送った。両版とも問題入力に HTTP 200 を返した後、旧 checkout のプロセスだけが未処理の `Z_DATA_ERROR` で exit 1 となった。PR #16 head は生存し、次の正常 POST にも HTTP 200 を返し、mock 状態は変わらなかった。8 条件の結果、入力 SHA-256、両ログ SHA-256 は [機械可読結果](bt16-body-parser-oracle-20261116.json)に保存した。別主体が入力とログの hash、固定 head、結果を照合した。

この比較で確認したのは、`body-parser 2.3.0` を含む旧依存構成と、`2.4.0` および下位の `qs` 更新を含む PR #16 構成の**局所 mock HTTP 挙動差**である。単一ライブラリだけの因果、配備版の到達性・実影響、人による実影響判定、GitHub の正式レビュー、保護 main への反映、反映後の再走査は確認していない。実行器は局所検証用で、Goose や GitHub の自動ワークフローからはまだ呼ばない。

使い方：清潔な二 checkout に依存を導入し、両方の head を固定してから、未使用の出力ディレクトリを指定する。

```bash
python3 demos/blue-team-agent/body_parser_oracle.py \
  --baseline-repo /path/to/main-checkout \
  --candidate-repo /path/to/pr16-checkout \
  --baseline-head f715deb90150d3fcfe510fd2e7b8f373ca0a8d5f \
  --candidate-head 289e575279b3e6c7f666f0dc328c786d3d982525 \
  --output-dir /path/to/new-ignored-evidence-dir
```
