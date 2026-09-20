# Decision Workbench

ModernBERT、GLiClass、既存のCodex環境または任意のLLM APIへ同じ問い・判断基準・選択肢を渡し、「小さな判断」を比較するDemoです。LLM接続にはTypeSafe公式のSystem One Adapterを使います。日本語と英語の6組、計12件のfixtureから始め、本文や選択肢を編集して比較します。

## What I want to understand

- 「鶏がらスープは動物由来」という一般知識を使って、ヴィーガン基準への違反を選べるか。
- 「だし」としか書かれていないとき、由来を補わずに情報不足を選べるか。
- 「CSVエクスポートボタンを追加」という依頼を機能追加に分類できても、実装に必要な条件は不足していると区別できるか。
- 受け入れ基準を本文へ追加した場合や、同じ内容を日本語・英語で入力した場合に、順位と選択がどう変わるか。
- ローカル分類モデルと、System One AdapterのChoice APIから呼ぶLLMで、結果と待ち時間がどう違うか。

「分類」「基準判定」「情報の十分性」は問いの作り方が違います。専用の論理検証器が動くわけではありません。選択肢に「情報不足」を置くことも、モデルによる正しい保留判断を保証しません。

## Architecture

```text
React + Material UI editor / fixture comparison
  → loopback Python HTTP API :5177
    → task validation: question / criteria / 2–4 options
      → local ModernBERT inference
      → local GLiClass inference
      → optional System One Adapter → existing Codex CLI / configured LLM API
    → per-model scores, selected option, timing, run JSON
```

Portalとは独立したvertical sliceです。Python依存とモデルweightsはこのDemo用に任意で導入し、rootのpnpm install/build/typecheckではダウンロードしません。HTTP serverはローカル接続用です。

ModernBERT側は`ModernBERT-Large-Instruct`のmasked-language-model headに選択肢付きの問いを渡します。GLiClass側は`gliclass-instruct-base-v1.0`へ候補ラベルを与えて分類します。

| API上のid | Public checkpoint | 固定revision |
| --- | --- | --- |
| modernbert | answerdotai/ModernBERT-Large-Instruct | 9943452941e79c8c35ede72e78a38a8175a79bb5 |
| gliclass | knowledgator/gliclass-instruct-base-v1.0 | 4f6a108b08a5537f395521d19b5073e197923dd3 |

3つ目の実行対象は`llm-adapter`です。TypeSafe公式`SystemOneAdapterClient`のChoice評価を、設定したOpenAI互換・Anthropic API、またはDemo独自providerを介した既存Codex CLIへ接続します。Codex接続部分はこのDemoの実装です。**同じ型付きAPIを使うためのadapterであり、Jev/System Oneモデル本体、Jevの速度、校正を再現するものではありません。** 初版で使うのはChoiceです。Noul/Scoreへ実験を広げる場合は別途問いと評価方法を設計します。[公式adapter](https://github.com/typesafe-ai/system-one-adapter-python)

ローカル2モデルの表示scoreは、その実行で提示した選択肢間のsoftmax値です。`rawScore`はlogitです。LLM adapterのscoreは、生成LLMが回答した候補分布に由来し、logitでもJevの校正済み確率でもありません。**これらは正解確率として扱えず、モデル間で同じ数値を直接比較できません。** まず選択された候補、順位、fixtureの期待値との差を見ます。ModernBERTが全語彙から選んだ回答tokenが提示した選択肢の文字に一致しない場合は、無理に候補へ丸めず`invalid`として記録します。

`fixtures.json`の`expectedOptionId`と`rationale`は人間が書いた比較用の参考回答です。モデルが生成した説明や検証済みのbenchmark結果ではありません。`basis: text`は本文の明示情報、`basis: knowledge`は一般知識が必要な問いを表します。本文・問い・基準・選択肢を変更した場合、元fixtureの期待値をそのまま正解として扱いません。

1回に最大12入力、各2〜4候補、最大3実行対象を指定できます。処理は順番に実行します。推論中にローカルモデルは自動ダウンロードしません。導入不足や推論失敗は各モデルのエラーとして扱い、架空の成功結果へ置き換えません。Demoの入力上限はModernBERT 1,024 tokens、GLiClass 512 tokensで、長すぎる入力は黙って切り詰めずエラーにします。実行記録は`runs/`へJSONで保存します。

## Run

Node.js/pnpmはrepository rootの要件に従います。ローカル2モデルの推論環境はPython **3.11**、CPU用PyTorchです。モデルweightsを保存できるディスク・メモリが必要です。公開モデルの初回取得にはネット接続が必要です。ローカル推論にAPI keyは不要です。

Windows PowerShell:

```powershell
cd demos/decision-workbench
python -m venv .venv
.venv/Scripts/python -m pip install torch==2.10.0 --index-url https://download.pytorch.org/whl/cpu
.venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python setup_models.py
cd ../..
pnpm demo:dev decision-workbench
```

Linux:

```sh
cd demos/decision-workbench
python3.11 -m venv .venv
.venv/bin/python -m pip install torch==2.10.0 --index-url https://download.pytorch.org/whl/cpu
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python setup_models.py
cd ../..
pnpm demo:dev decision-workbench
```

`setup_models.py`は両モデルを明示的に取得します。`--model modernbert`または`--model gliclass`で片方だけも取得できます。cacheはこのDemoの`.cache/huggingface/`で、gitへ含めません。

固定snapshotの合計は約1.45 GiBです。今回のWindows環境ではsymlinkを使えずcacheに重複が生じ、`.cache/huggingface/`全体は約2.18 GiBでした。`.venv`の容量は別に必要です。

UI: http://localhost:5177 （接続できなければ http://127.0.0.1:5177）。初回読み込みとwarmな推論は所要時間が異なります。メモリを抑えるため、モデル切り替え時は前のweightsを解放します。CPU、候補数、文章の長さにも左右されるため、異なる条件の時間をそのまま性能比較にしないでください。

### 任意: System One Adapterと既存LLM

Python 3.11のDemo用venvで、adapter用依存だけを追加できます。LLM adapterだけを使う場合はPyTorchやローカルweightsは不要です。

```powershell
cd demos/decision-workbench
.venv/Scripts/python -m pip install -r requirements-adapter.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

Linuxでは`.venv/bin/python`を使い、`.env`がなければ`.env.example`からコピーします。既存の`.env`がある場合は必要な項目を追記してください。

| 設定 | 用途 |
| --- | --- |
| DEMO_LLM_PROVIDER | codex、openai、anthropic のいずれか |
| DEMO_LLM_MODEL | 接続先で利用できるモデル名 |
| DEMO_LLM_BASE_URL | OpenAI互換APIで必要な場合のendpoint |
| DEMO_LLM_API_KEY | openai/anthropicで使用するcredential。codexでは不要 |

既存のCodexを使う場合は`DEMO_LLM_PROVIDER=codex`と利用可能なモデル名を設定します。実行環境にあるCodex CLIの既存ログインを使い、API keyを新しく渡す必要はありません。ChatGPTログインの場合はそのCodex利用枠を消費します。認証ファイルをこのrepositoryへコピーしたり、cloud/CIへ転送したりしません。

OpenAI互換/Anthropicを使う場合は使用するservice/modelとDemo専用credentialを設定します。keyはPython側だけが読み、ブラウザ・git・ログ・run JSONへ含めません。認証不要のローカル互換APIでも、明示的なplaceholderを`DEMO_LLM_API_KEY`へ設定します。設定を変えたらserverを再起動してください。

実行すると入力文・質問・基準・候補が選択したLLMへ送信されます。クラウドAPIは契約に応じた利用料金が発生し得ます。fixtureの期待値・人の根拠は判定要求へ送りません。未設定時はsetup不足として表示し、ローカル2モデルとrepositoryのbuildは利用できます。**Codex経由は`gpt-6-astra`で実推論を確認しました。OpenAI互換/Anthropic API providerの実接続は未検証です。**

モデルや外部APIを導入せずに契約・fixture・adapter境界を検証できます:

```sh
cd demos/decision-workbench
python -m unittest -v test_contract test_adapter test_codex_provider
```

## Things to try

1. **鶏がらスープ**を両モデルへ送る。参考回答は`violates`。同じ意味の英語fixtureでも比較する。
2. **由来が不明なだし**と**100%植物由来の野菜だし**を比較する。同じ問い・基準・選択肢を使い、本文の情報が増えると`insufficient`から`meets`へ変わるかを見る。
3. **CSV依頼の分類**と**着手条件の十分性**で、本文は同じまま問いを変える。機能追加に分類できることと、受け入れ条件が揃っていることを分けて観察する。
4. **受け入れ基準を追加したCSV依頼**を試す。対象行、列順、文字コード/BOM、ヘッダー、0件、保存失敗の扱いを一つずつ消し、情報不足へ戻るか確認する。
5. 候補のlabelだけでなくdescriptionを編集する。長い説明で判定が改善するか、別の意味へ引っ張られるかを見る。
6. 期待値が外れたrunのJSONを残す。入力言語、選択肢、モデル、初回/再実行を揃えて再現し、`Findings`へ観察条件とともに追記する。
7. LLM APIを設定したら`llm-adapter`で同じfixtureを試す。adapterのChoice分布とローカルのsoftmaxを区別し、選択結果・待ち時間・エラーを比較する。

## Findings

2026-09-21、Windows / Python 3.11.9 / PyTorch 2.10.0+cpu / CPU 4 threadsで、上記固定revisionのローカル2モデルを実行しました。Transformers 5.17.0、GLiClass 0.1.20を使用しています。以下は3件の動作確認であり、精度・速度benchmarkではありません。数値は選ばれた候補のscoreです。**System One Adapter経由のLLMの結果は、この表には含みません。**

| fixture | 参考回答 | ModernBERT | GLiClass |
| --- | --- | --- | --- |
| 鶏がらスープ・日本語 | 違反する | 満たす / 0.422（誤り） | 満たす / 0.941（誤り） |
| 鶏がらスープ・英語 | 違反する | 満たす / 0.442（誤り） | 情報不足 / 0.554（誤り） |
| 条件を明記したCSV依頼・日本語 | 情報が揃う | 情報が揃う / 0.875 | 情報が揃う / 0.99946 |

GLiClassは日本語の鶏がらスープを0.941で誤判定しました。**高い候補scoreは、正しい判断の確率ではありません。** この結果だけから、言語やモデル全体の優劣を結論づけません。

この3件の推論時間はModernBERT約1.82〜6.43秒、GLiClass約1.30〜2.28秒でした。初回読み込みはそれぞれ約35.3秒、27.1秒で、Pythonのライブラリimportを含みます。端末の負荷、cache、入力長、実行順に依存する観測値です。全12fixtureをGLiClass tokenizerで確認し、最長の日本語CSV条件付き依頼は335 tokensで、Demoの512-token上限内でした。

同日、公式System One Adapter → DemoのCodex provider → **Codex CLI 0.154.0-alpha.6.2 / gpt-6-astra**でも`chicken-vegan-ja`を実行しました。選択は`violates`で参考回答と一致し、LLM報告分布は`meets: 0 / violates: 1 / insufficient: 0`でした。adapter側の実測は約7.62秒、CLI報告usageはinput 9,462 / output 31 tokens、Adapter側のretryは0回です。これは既存Codexログインを使った1件の動作確認であり、API provider全般の検証やモデル精度・速度の比較benchmarkではありません。`1`という回答分布も校正された正解確率を意味しません。

画面とrun JSONのRetriesはAdapter側のprovider呼び出しの再試行回数を数えます。Codex CLI内部の通信再試行は未集計です。

Adapterの要求は60秒でタイムアウトします。Codexのキャンセル時は、その要求のプロセスツリー終了と回収に追加で最大約5秒を使います。ローカルのCLIは一時ディレクトリ、read-only sandbox、project設定・shell・MCP連携等を持ち込まない設定で実行します。確認済みのCLI versionは上記のみです。

このCLI versionでは、機能を無効化した初期化時に非致命的な`item.completed / error`通知が出た後、正常に`turn.completed`へ進みました。bridgeは通知だけで成功とせず、正常終了と有効なusageを必須にしています。この扱いと、キャンセル時のprocess終了、入力をshellへ渡さない境界を`test_codex_provider.py`で検証します。

ブラウザから`unknown-stock-vegan-ja`も実行し、`insufficient`（情報不足）で参考回答と一致しました。LLM報告分布は`0 / 0 / 1`、CLI usageはinput 9,589 / output 47 tokens、Adapter側retry 0回、Demo全体の呼び出し時間は約9.97秒でした。

続けて植物由来の原材料を明記したfixtureでは`meets`へ変わりました。画面のusageはinput 9,587 / output 31 tokens、Adapter側retry 0回、呼び出し時間は約4.32秒でした。この3種類の動作確認を、一般的な分類精度の保証には使いません。

6組だけで日本語性能、一般知識、推論能力全体を評価することはできません。追加実験では、否定表現、紛らわしい選択肢、明示された例外、文中の相反する情報、選択肢の順序を変えた場合も確認します。

## References

- [ModernBERT official repository](https://github.com/AnswerDotAI/ModernBERT)
- [ModernBERT-Large-Instruct official model card](https://huggingface.co/answerdotai/ModernBERT-Large-Instruct)
- [GLiClass official repository](https://github.com/Knowledgator/GLiClass)
- [GLiClass usage documentation](https://docs.knowledgator.com/docs/frameworks/gliclass/usage/)
- [GLiClass-Instruct Base official model card](https://huggingface.co/knowledgator/gliclass-instruct-base-v1.0)
- [TypeSafe official System One Adapter](https://github.com/typesafe-ai/system-one-adapter-python)
- [Codex non-interactive execution](https://developers.openai.com/codex/noninteractive)
- [Codex authentication](https://developers.openai.com/codex/auth)

モデルの対応言語、学習条件、ライセンスは利用するcheckpointのmodel cardを確認します。ModernBERT-Large-Instructは英語・コード中心の学習を説明しており、日本語fixtureは探索的な比較です。このDemoの独自fixtureは公式benchmarkではありません。
