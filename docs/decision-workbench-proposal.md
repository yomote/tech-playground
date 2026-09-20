# Decision Workbench — 第4 Demoの設計と初版

**状態: 初版を`demos/decision-workbench/`へ実装済み。2026-09-21に固定revisionの両モデルで3件ずつCPU推論を確認しました。誤判定を含む実結果と実行条件は[Demo READMEのFindings](../demos/decision-workbench/README.md#findings)に記録しています。これはbenchmarkではありません。**

この文書は提案時の狙いと、初版で採用した範囲を残します。起動方法と固定モデルrevisionは[Demo README](../demos/decision-workbench/README.md)を参照してください。

目的は、事前学習済み言語モデルを「文章を生成するもの」ではなく、質問と基準に応じて答えを選ぶ汎用の判定器として使い、どこまで判断できるかを触って理解することです。Jevへのアクセスを前提とせず、公開モデルをローカルで比較します。モデルルーティングは応用例の一つに留めます。

## 試す3つのモード

| モード | 入力・基準の例 | 観察すること |
| --- | --- | --- |
| 分類 | CSVエクスポートの依頼を「機能追加／不具合修正／説明の改善」に分類。候補の説明を編集する | 学習し直さず候補と定義を変更したとき、結果がどう変わるか |
| 条件への適合 | 鶏がらスープ・由来不明のだし・明示された野菜だしを、ヴィーガンの基準で判定する | 条件を満たす／違反する／本文から判断できない、を区別できるか |
| 情報不足 | 「この依頼には実装可能な受け入れ条件があるか」を判定する | 情報を足したときだけ判定が変わるか。書かれていない事実を補っていないか |

最初のプリセットは「鶏がらスープはヴィーガン基準を満たすか」です。開発依頼の比較には同一のCSVエクスポート依頼を使い、変更種別は分類できても着手条件は不足する、という問いの違いを観察します。対象行・列順・文字コード/BOM・ヘッダー・0件・保存失敗の条件を追記したfixtureと比較できます。

提案時に挙げた、退会時のデータ削除と保存期間・復元条件・後方互換性の判定は、次に追加できる課題です。初版の収録fixtureと混同しません。

## 比較するモデル

| モデル | このDemoでの役割 | 確認できた性質 |
| --- | --- | --- |
| `answerdotai/ModernBERT-Large-Instruct` | 自然文の質問と選択肢を渡す判定器 | 約0.4Bのinstruction-tuned encoder。公式例ではMLM headを使い、`ANSWER: [unused0] [MASK]`の位置からA〜Dの回答を得る。CPU実行例あり、Apache-2.0 |
| `knowledgator/gliclass-instruct-base-v1.0` | 同じ入力・基準を別方式で判定する比較対象 | 約187M。任意label、task prompt、自然文のlabel説明を使うzero-shot分類。追加学習なしで試せる。CPU対応、Apache-2.0 |

通常のModernBERTの基礎モデルに、そのまま汎用の判定機能が備わっていると扱わないようにします。ここでは用途に合わせてinstruction tuningされたモデルを使います。[ModernBERT-Large-Instruct公式モデルカード](https://huggingface.co/answerdotai/ModernBERT-Large-Instruct)

実装したGLiClass adapterでは、各候補の**labelとdescriptionを連結した自然文**を`labels`へ渡し、質問と判断基準を`prompt`へ渡します。候補のidは結果とUIの対応付けに使用します。入力文も含め、それぞれのモデルに合わせて組み立てた実際のpromptを結果で確認できます。人が書いた期待値と根拠は推論promptへ含めません。[GLiClass-Instructモデルカード](https://huggingface.co/knowledgator/gliclass-instruct-base-v1.0)・[公式使用ガイド](https://docs.knowledgator.com/docs/frameworks/gliclass/usage/)

将来の比較対象として、NLI向けに調整された[ModernBERT-base-zeroshot-v2.0](https://huggingface.co/MoritzLaurer/ModernBERT-base-zeroshot-v2.0)も候補です。初版のモデル数は上記2つに絞ります。

## 実装した画面と操作

- 左: 入力文、質問、判断基準、2〜4個の選択肢と各説明を編集。日英プリセットから始め、自由に変更できる。
- 中央: モデルごとの選択結果と候補scoreを並べる。モデル単体または両方で実行する。
- 右: 前回の入力・基準との差分、結果の変化、実測した推論時間を表示する。
- 下: fixtureの期待結果との一致、誤判定、情報不足の判定漏れを確認する。

「高いscoreだから正しい」と見せないよう、**モデル出力score**と**期待結果への一致**を別の項目にしています。モデル読み込み時間と推論時間も分けて記録します。実測前に速度の優劣は決めません。

## 判定能力を切り分けるfixture

**本文に根拠がある課題**と**一般知識を必要とする課題**を区別し、`basis: text | knowledge`を付けています。「モデルが知識を持っていない」と「書かれた条件を読めていない」を混同しにくくするためです。初版は意味を揃えた日本語・英語の6組、合計12件を収録しました。

| 日英ペア | mode / basis | 人が定めた期待値 |
| --- | --- | --- |
| 鶏がらスープとヴィーガン基準 | criteria / knowledge | 違反する |
| だしの原材料が未記載 | criteria / text | 情報不足 |
| 100%植物由来の野菜だしと全材料を明記 | criteria / text | 満たす |
| CSVエクスポート依頼の種類 | classification / text | 機能追加 |
| 同一CSV依頼の着手条件 | sufficiency / text | 情報不足 |
| 6項目の受け入れ条件を追記したCSV依頼 | sufficiency / text | 必要な情報が揃う |

だしの不明・明示ペア、およびCSVの条件追加前後は、問い・判断基準・選択肢を固定して本文を比較できます。今後は否定表現や矛盾する記述、一般知識を本文でも明示した場合を追加します。

期待結果には選択肢だけでなく、人が書いた根拠と判定基準を添えています。入力や選択肢を編集すると元fixtureとの期待値比較を解除し、古い正解を引き継ぎません。追加する問いで複数の解釈が成立する場合は期待値を未設定にし、正解率の集計から分けて観察します。モデルに説明文を生成させたものを、正解の根拠には使いません。

日本語と意味を揃えた英語のfixtureを対にします。ModernBERT-Large-Instructは英語とcodeを主な学習対象とし、その言語で最もよく動くと公式に記載されています。GLiClass-Instructも今回確認した資料では日本語性能を保証していません。日本語対応の程度そのものを実験対象とします。

## scoreの扱いとJevとの違い

初版は候補のlogitをsoftmaxで正規化したscoreと、元の`rawScore`を返します。ModernBERTの回答token scoreとGLiClassの分類scoreを、同じ意味のconfidenceとして直接比較しません。候補間で正規化した値も、その答えが現実に正しい確率を保証するものではありません。

情報不足は明示的な選択肢として判定させます。低いscoreだけを情報不足と同一視せず、scoreが高い誤答もfixtureで観察します。ModernBERTで想定した回答token以外が出た場合も、成功した判定へ黙って変換しません。

これらはJevのChoice・Noul・Scoreと同一のAPIや校正方法を再現するものではありません。初版は選択式の判定に集中し、rubricの段階を連続値へ集約する機能は後の実験にします。公開encoderがJevや大規模生成LLMと同等の知識・推論能力を持つとは仮定しません。

## 初版の実行範囲

`demos/decision-workbench/`にReact + Material UI、ローカルPython HTTP API、推論adapter、fixtureと契約テストをまとめました。Demo専用venvとモデルcacheを使い、他のDemoやPortalのruntimeへPython・推論モデルの依存を持ち込みません。

初回は公開モデルと依存packageのダウンロードが必要です。`setup_models.py`で固定revisionのweightsを明示的に取得し、推論adapterはローカルcacheを使用します。モデル未取得時はsetup案内を表示し、Playground全体のbuildは継続できます。API keyは不要で、mock値へのfallbackは実装していません。コードの完成と実モデルによる動作確認は区別し、確認結果はDemo側へ記録します。

初版の範囲は、3つのモード、2モデルの切り替え、編集可能な基準、日英fixture、差分比較、結果のJSON保存までです。最大12入力を順次実行し、入力長は各モデルのDemo上限を超えればエラーにします。fine-tuning、複雑なworkflow、自動実行agentは後の実験とします。最初のCPU確認では、日本語の鶏がらスープを両モデルが誤判定し、GLiClassの候補scoreは0.941でした。この観察からもscoreを正しさと同一視せず、判断基準や表現を変えた結果を残して比較します。

参考: [GLiClass公式依存定義](https://github.com/Knowledgator/GLiClass/blob/main/pyproject.toml)・[ModernBERT-Instruct公式cookbook](https://github.com/AnswerDotAI/ModernBERT-Instruct-mini-cookbook)
