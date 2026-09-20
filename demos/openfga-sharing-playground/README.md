# OpenFGA Sharing Playground

## What I want to understand

User → Team → Folder → Documentの間接関係と継承を、グラフの線をつないだり切ったりして理解するDemoです。左側で共有tupleを編集し、右側で認可checkを行います。認証はmock user selectionのみで、認可に集中します。

## Architecture

```text
React / Material UI / React Flow → demo-local Node API
                             ├─ MOCK: local model evaluator + in-memory tuples
                             └─ LIVE: OpenFGA HTTP API (Docker)
                                       store → authorization model → tuples → check
```

`model.fga`をofficial syntax transformerでJSONへ変換してserverへ登録します。User / Team / Folder / Documentとowner / editor / viewer / member / parentを定義します。ownerはeditor、editorはviewerを含み、folderのeditor/viewerが子へ継承されます。

liveのdecisionはOpenFGA `/check`の結果です。explanationは現在のtuplesをdemo固有のgraph walkerでたどった**教育用のpath**であり、OpenFGA内部traceではありません。条件付きtupleやdenyなど汎用モデルは扱いません。

User / Team / Folder / Documentを別々のnodeとして表示し、tupleをラベル付きの線で結びます。Checkで見つかった経路の実tupleをAPIが返し、対応する線とnodeを緑で強調します。nodeを動かす操作は見た目だけ、線の追加・削除はserverへ保存される関係の変更です。

## Run

```sh
pnpm install
pnpm demo:dev openfga-sharing-playground
```

http://localhost:5176 。最初は**MOCK**で、Docker/credential不要です。

実OpenFGAで試す場合はDocker Engine/Desktopを起動して:

```sh
docker compose -f demos/openfga-sharing-playground/compose.yaml up -d
```

UIの **Connect real OpenFGA** を押します。専用のstore/modelを作成し、初期tuplesをseedします。mock編集内容はliveへコピーしません。liveに接続できない場合はerrorを表示し、判定をmockへ自動fallbackしません。

API endpoint変更はdemo内の`.env`で`FGA_API_URL`を指定します（`.env.example`参照）。デフォルトは`http://127.0.0.1:8080`。Dockerはmemory datastoreで認証不要、localhostのみ公開です。

```sh
docker compose -f demos/openfga-sharing-playground/compose.yaml down
```

## Things to try

1. 右側でBob → Edit → Document 1をCheck。ALLOWEDとなり、Bob → Team X → Folder A → Document 1の経路が緑になる。
2. Bob → Team Xのmember線をクリックし、グラフ下の**この関係を削除**を押す。再CheckするとDENIEDになる。
3. Bobのnode右側の丸からTeam Xの左側の丸へドラッグし、memberを選んで追加する。**Add relationship**ボタンからも同じ操作ができる。再Checkで権限が戻る。
4. Carol → Document 1のviewerを追加し、Viewは許可、Editは不許可であることを確認する。人とresourceのnodeをクリックすると右側のcheck対象を選べる。
5. Document 1へ向かうFolder Aのparent線を削除し、Folder Bからparent線を追加する。permission継承先が変わる。
6. Folder A → Folder Bのparentを追加して、多段継承を確認する。
7. Folder A上のTeam X editorを削除しても、AliceはownerとしてEdit可能なことを確認する。

線が選びにくい場合は**Relationship tuples**を開き、対象tupleの「選択」から削除できます。Teamからresourceへの共有は`team:x#member`、つまりTeamのmembers全員への共有として扱います。

## Findings

- global RBAC比較はAlice=editor / Bob=viewer / Carol=noneの意図的に単純なbaselineです。resource-scoped RBAC全般が表現不可能だと主張するものではありません。
- tuple追加・削除後は以前の判定をクリアし、再checkを要求します。
- mockはこのDemo modelのsubsetを実装した学習用evaluatorで、OpenFGAの代替ではありません。local traversalはcycleで無限再帰しません。
- real storeはdemo server processでIDを保持します。Node再起動後の接続で新規storeを作り、Docker再起動でmemory dataが消えます。永続化はv0.1の対象外です。
- explanationのreadとcheckはtransactionではありません。外部から同時編集すると差が出る可能性があります。
- 固定の3 users / 1 team / 2 folders / 2 documents。関係を自由に変えてmodelの特徴を学ぶ範囲に限定します。
- グラフの配置は保存しません。削除した関係は再追加でき、権限の変化を繰り返し観察できます。Checkの強調表示は見つかった1つの経路で、成立する全経路の一覧ではありません。

## References

2026-09-20確認。

- [OpenFGA parent-child modeling](https://openfga.dev/docs/modeling/parent-child)
- [Configure authorization model](https://openfga.dev/docs/getting-started/configure-model)
- [Perform a check](https://openfga.dev/docs/getting-started/perform-check)
- [Docker setup](https://openfga.dev/docs/getting-started/setup-openfga/docker)
