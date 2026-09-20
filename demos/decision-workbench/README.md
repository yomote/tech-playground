# Decision Workbench

ModernBERTとGLiClassへ同じ問い・判断基準・選択肢を渡し、分類モデルを「小さな判断」に使うと何が起きるかを観察するDemoです。日本語と英語の6組、計12件のfixtureから始め、本文や選択肢を編集して比較します。

## What I want to understand

- 「鶏がらスープは動物由来」という一般知識を使って、ヴィーガン基準への違反を選べるか。
- 「だし」としか書かれていないとき、由来を補わずに情報不足を選べるか。
- 「CSVエクスポートボタンを追加」という依頼を機能追加に分類できても、実装に必要な条件は不足していると区別できるか。
- 受け入れ基準を本文へ追加した場合や、同じ内容を日本語・英語で入力した場合に、順位と選択がどう変わるか。

「分類」「基準判定」「情報の十分性」は問いの作り方が違います。専用の論理検証器が動くわけではありません。選択肢に「情報不足」を置くことも、モデルによる正しい保留判断を保証しません。

## Architecture

```text
React + Material UI editor / fixture comparison
  → loopback Python HTTP API :5177
    → task validation: question / criteria / 2–4 options
      → local ModernBERT inference
      → local GLiClass inference
    → per-model scores, selected option, timing, run JSON
```

Portalとは独立したvertical sliceです。Python依存とモデルweightsはこのDemo用に任意で導入し、rootのpnpm install/build/typecheckではダウンロードしません。HTTP serverはローカル接続用です。

ModernBERT側は`ModernBERT-Large-Instruct`のmasked-language-model headに選択肢付きの問いを渡します。GLiClass側は`gliclass-instruct-base-v1.0`へ候補ラベルを与えて分類します。

| API上のid | Public checkpoint | 固定revision |
| --- | --- | --- |
| modernbert | answerdotai/ModernBERT-Large-Instruct | 9943452941e79c8c35ede72e78a38a8175a79bb5 |
| gliclass | knowledgator/gliclass-instruct-base-v1.0 | 4f6a108b08a5537f395521d19b5073e197923dd3 |

表示scoreは、その実行で提示した選択肢間のsoftmax値です。`rawScore`はlogitです。**どちらも校正された正解確率ではなく、モデル間で同じ数値を直接比較できません。** まず選択された候補、順位、fixtureの期待値との差を見ます。ModernBERTが全語彙から選んだ回答tokenが提示した選択肢の文字に一致しない場合は、無理に候補へ丸めず`invalid`として記録します。

`fixtures.json`の`expectedOptionId`と`rationale`は人間が書いた比較用の参考回答です。モデルが生成した説明や検証済みのbenchmark結果ではありません。`basis: text`は本文の明示情報、`basis: knowledge`は一般知識が必要な問いを表します。本文・問い・基準・選択肢を変更した場合、元fixtureの期待値をそのまま正解として扱いません。

1回に最大12入力、各2〜4候補、最大2モデルを指定できます。重い処理は順番に実行します。推論中にモデルは自動ダウンロードしません。導入不足や推論失敗は各モデルのエラーとして扱い、架空の成功結果へ置き換えません。Demoの入力上限はModernBERT 1,024 tokens、GLiClass 512 tokensで、長すぎる入力は黙って切り詰めずエラーにします。実行記録は`runs/`へJSONで保存します。

## Run

Node.js/pnpmはrepository rootの要件に従います。推論環境はPython **3.11**、CPU用PyTorchです。モデルweightsを保存できるディスク・メモリが必要です。公開モデルの初回取得にはネット接続が必要です。外部LLM API keyは使用しません。

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

モデルを導入せずに契約とfixtureを検証できます:

```sh
cd demos/decision-workbench
python -m unittest -v test_contract
```

## Things to try

1. **鶏がらスープ**を両モデルへ送る。参考回答は`violates`。同じ意味の英語fixtureでも比較する。
2. **由来が不明なだし**と**100%植物由来の野菜だし**を比較する。同じ問い・基準・選択肢を使い、本文の情報が増えると`insufficient`から`meets`へ変わるかを見る。
3. **CSV依頼の分類**と**着手条件の十分性**で、本文は同じまま問いを変える。機能追加に分類できることと、受け入れ条件が揃っていることを分けて観察する。
4. **受け入れ基準を追加したCSV依頼**を試す。対象行、列順、文字コード/BOM、ヘッダー、0件、保存失敗の扱いを一つずつ消し、情報不足へ戻るか確認する。
5. 候補のlabelだけでなくdescriptionを編集する。長い説明で判定が改善するか、別の意味へ引っ張られるかを見る。
6. 期待値が外れたrunのJSONを残す。入力言語、選択肢、モデル、初回/再実行を揃えて再現し、`Findings`へ観察条件とともに追記する。

## Findings

2026-09-21、Windows / Python 3.11.9 / PyTorch 2.10.0+cpu / CPU 4 threadsで、上記固定revisionの両モデルを実行しました。Transformers 5.17.0、GLiClass 0.1.20を使用しています。以下は3件の動作確認であり、精度・速度benchmarkではありません。数値は選ばれた候補のscoreです。

| fixture | 参考回答 | ModernBERT | GLiClass |
| --- | --- | --- | --- |
| 鶏がらスープ・日本語 | 違反する | 満たす / 0.422（誤り） | 満たす / 0.941（誤り） |
| 鶏がらスープ・英語 | 違反する | 満たす / 0.442（誤り） | 情報不足 / 0.554（誤り） |
| 条件を明記したCSV依頼・日本語 | 情報が揃う | 情報が揃う / 0.875 | 情報が揃う / 0.99946 |

GLiClassは日本語の鶏がらスープを0.941で誤判定しました。**高い候補scoreは、正しい判断の確率ではありません。** この結果だけから、言語やモデル全体の優劣を結論づけません。

この3件の推論時間はModernBERT約1.82〜6.43秒、GLiClass約1.30〜2.28秒でした。初回読み込みはそれぞれ約35.3秒、27.1秒で、Pythonのライブラリimportを含みます。端末の負荷、cache、入力長、実行順に依存する観測値です。全12fixtureをGLiClass tokenizerで確認し、最長の日本語CSV条件付き依頼は335 tokensで、Demoの512-token上限内でした。

6組だけで日本語性能、一般知識、推論能力全体を評価することはできません。追加実験では、否定表現、紛らわしい選択肢、明示された例外、文中の相反する情報、選択肢の順序を変えた場合も確認します。

## References

- [ModernBERT official repository](https://github.com/AnswerDotAI/ModernBERT)
- [ModernBERT-Large-Instruct official model card](https://huggingface.co/answerdotai/ModernBERT-Large-Instruct)
- [GLiClass official repository](https://github.com/Knowledgator/GLiClass)
- [GLiClass usage documentation](https://docs.knowledgator.com/docs/frameworks/gliclass/usage/)
- [GLiClass-Instruct Base official model card](https://huggingface.co/knowledgator/gliclass-instruct-base-v1.0)

モデルの対応言語、学習条件、ライセンスは利用するcheckpointのmodel cardを確認します。ModernBERT-Large-Instructは英語・コード中心の学習を説明しており、日本語fixtureは探索的な比較です。このDemoの独自fixtureは公式benchmarkではありません。
