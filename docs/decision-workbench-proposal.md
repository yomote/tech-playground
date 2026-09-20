# Decision Workbench — 第4 Demo案

**状態: 提案。まだ実装していません。モデルのインストール・取得・実行も未実施です。**

目的は、事前学習済み言語モデルを「文章を生成するもの」ではなく、質問と基準に応じて答えを選ぶ汎用の判定器として使い、どこまで判断できるかを触って理解することです。Jevへのアクセスを前提とせず、公開モデルをローカルで比較します。モデルルーティングは応用例の一つに留めます。

## 試す3つのモード

| モード | 入力・基準の例 | 観察すること |
| --- | --- | --- |
| 分類 | 開発依頼を「不具合修正／機能追加／調査／その他」に分類。候補の説明を編集する | 学習し直さず候補と定義を変更したとき、結果がどう変わるか |
| 条件への適合 | 「既存データを削除しない」「後方互換性を維持する」という条件と変更計画を渡す | 条件を満たす／違反する／本文から判断できない、を区別できるか |
| 情報不足 | 「この依頼には実装可能な受け入れ条件があるか」を判定する | 情報を足したときだけ判定が変わるか。書かれていない事実を補っていないか |

最初のプリセットは同じ開発依頼を3つの質問で見る形にします。例えば「退会時に保存データを完全削除できるようにして。来週公開したい」に対し、変更種別・指定条件への適合・要件の十分さを判定します。保存期間や復元条件、受け入れ条件を追記して結果を比較できます。

## 比較するモデル

| モデル | このDemoでの役割 | 確認できた性質 |
| --- | --- | --- |
| `answerdotai/ModernBERT-Large-Instruct` | 自然文の質問と選択肢を渡す判定器 | 約0.4Bのinstruction-tuned encoder。公式例ではMLM headを使い、`ANSWER: [unused0] [MASK]`の位置からA〜Dの回答を得る。CPU実行例あり、Apache-2.0 |
| `knowledgator/gliclass-instruct-base-v1.0` | 同じ入力・基準を別方式で判定する比較対象 | 約187M。任意label、task prompt、自然文のlabel説明を使うzero-shot分類。追加学習なしで試せる。CPU対応、Apache-2.0 |

通常のModernBERTの基礎モデルに、そのまま汎用の判定機能が備わっていると扱わないようにします。ここでは用途に合わせてinstruction tuningされたモデルを使います。[ModernBERT-Large-Instruct公式モデルカード](https://huggingface.co/answerdotai/ModernBERT-Large-Instruct)

GLiClassでは候補の識別子を`labels`へ、候補の意味や判定基準を`prompt`へ渡します。両モデルで同じ課題を試しつつ、それぞれの公式の入力形式に合わせます。[GLiClass-Instructモデルカード](https://huggingface.co/knowledgator/gliclass-instruct-base-v1.0)・[公式使用ガイド](https://docs.knowledgator.com/docs/frameworks/gliclass/usage/)

将来の比較対象として、NLI向けに調整された[ModernBERT-base-zeroshot-v2.0](https://huggingface.co/MoritzLaurer/ModernBERT-base-zeroshot-v2.0)も候補です。初版のモデル数は上記2つに絞ります。

## 画面と操作の案

- 左: 入力文、質問、選択肢、各選択肢の説明を編集。プリセットから始め、自由に変更できる。
- 中央: モデルごとの選択結果と候補scoreを並べる。モデル単体または両方で実行する。
- 右: 前回の入力・基準との差分、結果の変化、実測した推論時間を表示する。
- 下: fixtureの期待結果との一致、誤判定、情報不足の判定漏れを確認する。

「高いscoreだから正しい」と見せないよう、**モデル出力score**と**期待結果への一致**を別の項目にします。初回のモデル読み込み時間と、その後の推論時間も分けます。実測前に速度の優劣は決めません。

## 判定能力を切り分けるfixture

**本文に根拠がある課題**と**一般知識を必要とする課題**を分けて用意します。前者では同じ文章の情報を追加・削除・否定し、後者では必要な知識を本文にも与えた場合と比較します。「モデルが知識を持っていない」と「書かれた条件を読めていない」を混同しにくくするためです。

期待結果には選択肢だけでなく、人が書いた根拠と判定基準を添えます。複数の解釈が成立する問題は、正解率の集計から分けて観察します。モデルに説明文を生成させたものを、正解の根拠には使いません。

日本語と意味を揃えた英語のfixtureを対にします。ModernBERT-Large-Instructは英語とcodeを主な学習対象とし、その言語で最もよく動くと公式に記載されています。GLiClass-Instructも今回確認した資料では日本語性能を保証していません。日本語対応の程度そのものを実験対象とします。

## scoreの扱いとJevとの違い

候補scoreの計算方法はモデルごとに表示し、必要に応じて元のlogits等も確認できるようにします。ModernBERTの回答token scoreとGLiClassの分類scoreを、同じ意味のconfidenceとして直接比較しません。候補間で正規化した値も、その答えが現実に正しい確率を保証するものではありません。

情報不足は明示的な選択肢として判定させます。低いscoreだけを情報不足と同一視せず、scoreが高い誤答もfixtureで観察します。ModernBERTで想定した回答token以外が出た場合も、成功した判定へ黙って変換しません。

これらはJevのChoice・Noul・Scoreと同一のAPIや校正方法を再現するものではありません。初版は選択式の判定に集中し、rubricの段階を連続値へ集約する機能は後の実験にします。公開encoderがJevや大規模生成LLMと同等の知識・推論能力を持つとは仮定しません。

## 初版の実行範囲

実装する場合は`demos/decision-workbench/`にUI、Python推論処理、fixtureをまとめます。Demo専用venvとモデルcacheを使い、他のDemoやPortalのruntimeへ依存を持ち込みません。

初回は公開モデルと依存packageのダウンロードが必要です。取得後はローカル推論を行い、Jevのallowlistや外部推論APIのcredentialを不要にします。モデル未取得時はsetup案内を表示し、Playground全体のbuildは継続できる設計にします。推論はmock値ではなく実モデルを使う予定です。

最初の範囲は、3つのモード、2モデルの切り替え、編集可能な基準、日英fixture、結果のJSON保存まで。fine-tuning、複雑なworkflow、自動実行agentは後の実験とします。必要メモリ、Windows上の依存関係、CPUでの待ち時間は実装時に確認します。

参考: [GLiClass公式依存定義](https://github.com/Knowledgator/GLiClass/blob/main/pyproject.toml)・[ModernBERT-Instruct公式cookbook](https://github.com/AnswerDotAI/ModernBERT-Instruct-mini-cookbook)
