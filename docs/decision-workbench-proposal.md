# Decision Workbench — 第4 Demoの設計と初版

**状態: 初版を`demos/decision-workbench/`へ実装済み。2026-09-21に固定revisionの両モデルで3件ずつCPU推論を確認しました。誤判定を含む実結果と実行条件は[Demo READMEのFindings](../demos/decision-workbench/README.md#findings)に記録しています。これはbenchmarkではありません。**

追加の比較対象として、TypeSafe公式System One Adapterから既存Codex環境またはLLM APIへ接続する`llm-adapter`の経路を用意しました。Codex CLI 0.154.0-alpha.6.2 / `gpt-6-astra`経由で、日本語の鶏がらスープfixtureが参考回答の「違反する」と一致する実推論を確認しました。OpenAI互換/Anthropic API providerの実接続は未検証です。上記のCPU観測値はModernBERT/GLiClassだけの結果で、Codex側の観測はDemo READMEに分けて記録しています。

この文書は提案時の狙いと、初版で採用した範囲を残します。起動方法と固定モデルrevisionは[Demo README](../demos/decision-workbench/README.md)を参照してください。

さらに、公式TypeSafe SDKでJevを直接呼ぶ`jev`経路を追加しました。これはSystem One Adapter経由の一般LLMとは別の実行対象です。**返却モデル`jev-1.13.0`で、日本語6fixtureが参考回答と6 / 6一致する実接続を確認しました。** 単問API往復時間は506.06〜616.19 ms、中央値544.28 msで、それ以前の初回単問は3,436.77 msでした。SDK準備・通信を含む動作確認であり、精度・速度benchmarkではありません。設定場所は`demos/decision-workbench/.env`で、`TYPESAFE_API_KEY`と`TYPESAFE_MODEL=jev-latest`を指定します。既存Codex用の`DEMO_LLM_*`設定は独立しています。

目的は、事前学習済み言語モデルを、質問と基準に応じて答えを選ぶ汎用の判定器として使い、どこまで判断できるかを触って理解することです。公開モデルのローカル推論と任意のLLM APIから始め、アクセス可能であればJevも比較します。Jevの認証がなくても他の実行対象は使えます。モデルルーティングは応用例の一つに留めます。

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
| `llm-adapter` / 設定したLLM | 公式System One AdapterのChoice APIを使う比較対象 | 使用API/modelに依存。Jevモデルのweights、速度、校正を再現するものではなく、型付き評価APIの互換経路。Codex経由を実確認、OpenAI互換/Anthropic API接続は未検証 |
| `jev` / `TYPESAFE_MODEL`で指定したJev | 公式TypeSafe SDKから直接呼ぶ比較対象 | TypeSafeのAPIキーとモデルへのアクセスが必要。Adapter/Codexを介さずに呼ぶ。日本語6fixtureで実接続を確認 |

通常のModernBERTの基礎モデルに、そのまま汎用の判定機能が備わっていると扱わないようにします。ここでは用途に合わせてinstruction tuningされたモデルを使います。[ModernBERT-Large-Instruct公式モデルカード](https://huggingface.co/answerdotai/ModernBERT-Large-Instruct)

実装したGLiClass adapterでは、各候補の**labelとdescriptionを連結した自然文**を`labels`へ渡し、質問と判断基準を`prompt`へ渡します。候補のidは結果とUIの対応付けに使用します。入力文も含め、それぞれのモデルに合わせて組み立てた実際のpromptを結果で確認できます。人が書いた期待値と根拠は推論promptへ含めません。[GLiClass-Instructモデルカード](https://huggingface.co/knowledgator/gliclass-instruct-base-v1.0)・[公式使用ガイド](https://docs.knowledgator.com/docs/frameworks/gliclass/usage/)

将来の比較対象として、NLI向けに調整された[ModernBERT-base-zeroshot-v2.0](https://huggingface.co/MoritzLaurer/ModernBERT-base-zeroshot-v2.0)も候補です。現在の実行対象はローカル2モデル、任意のLLM adapter、Jevの4つです。

## 公式System One Adapterを使う理由

ユーザーがすでに利用できるLLM APIで、TypeSafeの型付きChoice評価の使い方を試すためです。[TypeSafe公式System One Adapter](https://github.com/typesafe-ai/system-one-adapter-python)を使用し、独自に似た名前のAPIを作ってJevと同一と扱うことを避けます。

本文を`state`、問いと判断基準を`instructions`、候補idからlabel・descriptionへの対応をChoiceの`criteria`へ渡します。fixtureの期待値・人の根拠は入力へ含めません。返答の選択肢idと分布を検証し、未知id、非有限値、未実行やprovider errorを成功に変換しません。

`DEMO_LLM_PROVIDER`は`codex`、`openai`、`anthropic`から選び、`DEMO_LLM_MODEL`でモデルを指定します。CodexはこのDemoのcustom providerから既存CLIとそのログインを使用し、API keyを追加する必要はありません。ChatGPTログインでは既存のCodex利用枠を消費します。認証ファイルをrepositoryやcloud/CIへコピーしません。Codex provider自体をTypeSafe公式実装と称するものではありません。

OpenAI互換/Anthropic APIではDemo専用`.env`に必要な`DEMO_LLM_API_KEY`を設定し、OpenAI互換endpointには任意の`DEMO_LLM_BASE_URL`を使います。依存は任意の`requirements-adapter.txt`へ分け、未設定でもrepositoryのbuildやローカルモデルは利用できます。keyはPython側だけが読み、ブラウザ・git・ログへ渡しません。実行時は入力を設定先へ送り、クラウドAPIでは契約に応じて利用料金が発生し得ます。

## 実装した画面と操作

Jevの直接接続は`requirements-jev.txt`（`typesafe-sdk==0.7.0`、`python-dotenv==1.2.3`）で任意に導入します。`AsyncTypeSafeClient`へモデル名を明示し、公式endpointへ要求します。APIキーはPython側だけが読み、ブラウザやrun JSONへ含めません。未設定時はsetup案内を表示します。表示される呼び出し時間はネットワークを含むAPI往復時間であり、Jev内部のモデル推論時間ではありません。[TypeSafe公式ドキュメント](https://docs.typesafe.ai/)

- 左: 入力文、質問、判断基準、2〜4個の選択肢と各説明を編集。日英プリセットから始め、自由に変更できる。
- 中央: 実行対象ごとの選択結果と候補scoreを並べる。単体または複数を選んで実行する。
- 右: 前回の入力・基準との差分、結果の変化、実測した推論時間を表示する。
- 下: fixtureの期待結果との一致、誤判定、情報不足の判定漏れを確認する。

「高いscoreだから正しい」と見せないよう、**モデル出力score**と**期待結果への一致**を別の項目にしています。モデル読み込み時間と推論時間も分けて記録します。実測前に速度の優劣は決めません。

## 判定能力を切り分けるfixture

### Issue triageへの拡張

単問の汎用判定に加え、一つのIssue本文を共有し、独立した16個のChoiceを評価する実験を追加します。初期入力は「昨日の更新後から、チームメンバーが共有フォルダのCSVを出力できません。管理者では成功します。月末処理が止まっています。手順とエラーログは添付していません。」です。本文と判断基準を編集し、1 / 4 / 8 / 16問から選択します。

`batch`は選択した質問を1要求へまとめ、`sequential`は1問ずつN要求、`compare`は同じ質問で両方を実行します。`sweep`はbatchの質問数を1 / 4 / 8 / 16へ変えて4回呼びます。Jev直接接続と既存のSystem One Adapter接続先へ同じ本文・質問・候補を渡し、選択結果、呼び出し回数、待ち時間を比較します。前の回答を次の質問へ渡すworkflowではありません。

これは[TypeSafe公式のspeculative fan-out](https://docs.typesafe.ai/patterns/fan-out)にある、同じ入力への複数質問をまとめて評価する使い方を観察するためのものです。必要な結果を後から選べる構造を試しますが、Issueを自動的に更新・担当割り当てする機能は加えません。

時間にはAPI通信、SDK準備、Codex利用時のCLI起動を含みます。batch内の質問ごとの内部時間は取得できないため表示しません。初回準備、実行順、ネットワーク、サービスの混雑があるので、1回の結果や単問の中央値から16問の速度を予測しません。

2026-09-21にブラウザからJev `jev-1.13.0`で16問の比較を実行しました。batchは1回で2,307.78 ms、sequentialは16回で合計9,581.68 msでした。両方式とも参考回答12問中11問に一致し、4問の判断課題は採点対象外です。期限の記載がない問いで、期待する`unknown`ではなく「期限を定めないと明記」を意味する`no`を選びました。batchにはserver再起動後の初回SDK準備を含みます。これは1回の動作確認であり、純粋な推論時間、一般的な速度比、精度の保証には使いません。先の6 / 6一致は既存の単問fixtureの別結果です。

その後の単回sweepでは、1 / 4 / 8 / 16問のJevが順に584.45 / 547.86 / 516.39 / 490.90 ms、Adapter → Codex `gpt-6-astra`が5,758.81 / 8,221.05 / 12,767.58 / 16,168.85 msでした。各回1 SDK呼び出しで、すべて正常完了しています。16問時は参考回答12問中Jevが11問、Codexが12問に一致し、4問は採点対象外でした。Jev側のSDK importは完了済みで、各要求のSDK準備・API通信とCodex起動を含みます。初回状態や順序、混雑の影響を分離したbenchmarkではなく、Jevの一定時間や一般的な優劣を保証しません。詳細はDemo READMEのFindingsに記録しました。

既定の参考回答は人が作った比較用です。本文や質問・基準を編集すると参考回答との比較を解除します。サーバーは任意の参考回答の上書きを受け付けず、元のfixtureの答えを編集後にも流用しません。

### 単問の日英fixture

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

ローカル2モデルは候補のlogitをsoftmaxで正規化したscoreと、元の`rawScore`を返します。LLM adapterは生成LLMが回答した候補分布を返し、logitを捏造しません。ModernBERTの回答token score、GLiClassの分類score、LLMの回答分布を、同じ意味のconfidenceとして直接比較しません。いずれも、その答えが現実に正しい確率を保証するものではありません。

情報不足は明示的な選択肢として判定させます。低いscoreだけを情報不足と同一視せず、scoreが高い誤答もfixtureで観察します。ModernBERTで想定した回答token以外が出た場合も、成功した判定へ黙って変換しません。

ローカルencoderの経路は独自の比較用APIです。追加した公式adapterの経路ではSystem Oneの型付きChoice APIを使いますが、Jev/System Oneモデル本体の知識・速度・校正を再現しません。LLMが出した分布を「Jevの校正済みconfidence」と表示しません。初版は選択式の判定に集中し、Noul/Scoreやrubricの段階を連続値へ集約する機能は後の実験とします。

## 初版の実行範囲

`demos/decision-workbench/`にReact + Material UI、ローカルPython HTTP API、推論adapter、fixtureと契約テストをまとめました。Demo専用venvとモデルcacheを使い、他のDemoやPortalのruntimeへPython・推論モデルの依存を持ち込みません。

ローカルモデルは初回に公開weightsと依存packageのダウンロードが必要です。`setup_models.py`で固定revisionを明示的に取得し、推論時はローカルcacheを使用します。ローカル推論のAPI keyは不要です。LLM adapterは設定したAPIの認証・実行環境を使用します。未取得・未設定時はsetup案内を表示し、Playground全体のbuildは継続できます。mock値へのfallbackは実装していません。コードの完成と実推論の動作確認は区別し、確認結果はDemo側へ記録します。

現在の範囲は、3つのモード、ローカル2モデルと任意LLM adapter・Jev、編集可能な基準、日英fixture、差分比較、結果のJSON保存までです。最大12入力を順次実行し、入力長は各モデルのDemo上限を超えればエラーにします。fine-tuning、複雑なworkflow、自動実行agentは後の実験とします。最初のCPU確認では、日本語の鶏がらスープを両ローカルモデルが誤判定し、GLiClassの候補scoreは0.941でした。この観察をLLM adapterやJevへそのまま転用せず、同じ課題を実行して比較します。

参考: [GLiClass公式依存定義](https://github.com/Knowledgator/GLiClass/blob/main/pyproject.toml)・[ModernBERT-Instruct公式cookbook](https://github.com/AnswerDotAI/ModernBERT-Instruct-mini-cookbook)
