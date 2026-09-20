# MAF Magentic Scrum

## What I want to understand

Microsoft Agent FrameworkのMagentic managerが、Planner / Developer / Reviewer / Testerをどう選び、失敗にどう対応するか。成果物そのものより、選択・round・stall・replan・tool calls・最終結果までのtrajectoryを観察します。Handoff orchestrationは使いません。

## Architecture

```text
React / Material UI / React Flow → Python stdlib HTTP server → Run (runs/<UUID>/)
                                             ├─ mock: scripted manager / agents
                                             └─ live: MAF MagenticBuilder + 4 specialists
                                                  └─ read_fixture / write_solution / run_tests
```

taskは`clamp(value, low, high)`の実装です。各runが独立した`solution.py`と固定unittestを持ちます。Developerだけが書き込みtoolを持ち、Reviewerはread-only、Testerはread/testのみです。fixture toolは単一の純粋関数にsyntaxを限定し、任意shellは提供しません。

**Mock:** manager判断とagent応答はscripted。fixture編集と5件のunittest実行は本物です。happy path / review failure / test failure → stall → replanを選べます。

**Live:** SDKの`MagenticBuilder`とstandard managerが順番を動的に決めます。progress ledgerをJSONで保存し、`REPLANNED` eventだけをreplan数に数えます。roundはprogress ledger更新数です。modelが成功を報告しても、最後に別途unittestが通らなければrunはfailedになります。

グラフではManagerからspecialistへの線がagent選択、Fixtureへの線が記録されたtool callを表します。現在の担当nodeを強調し、選択回数・round・replan・tool数を表示します。右側にはManager decisionとAgent history、下部には選択eventのJSONと最終結果を表示します。Magenticの実行エンジンをグラフで編集するUIではありません。

## Run

Python **3.11+**が必要です。mockはPython標準ライブラリのみです。

```sh
pnpm install
pnpm demo:dev maf-magentic-scrum
```

http://localhost:5175 でStart experiment。root build/typecheckはPython SDKやcredentialなしで実行可能です。

Live modeを使う場合、demo directoryで:

```sh
python -m venv .venv
# Windows
.venv/Scripts/python -m pip install -r requirements.txt
# macOS / Linux
.venv/bin/python -m pip install -r requirements.txt
```

`.env.example`を`.env`へコピーし、`OPENAI_API_KEY`と利用可能な`OPENAI_CHAT_MODEL_ID`を設定。起動commandを再実行してください。launcherはdemo内のvenvを優先します。UIでLIVEを選択したときだけAPIを呼び、通常のAPI利用料金が発生します。Azureは不要です。

**Plan & final approval**を有効にするとplan作成時（liveではMAFのplan review request）とfinal report前に停止します。Approveで再開、Rejectで終了。15分でtimeoutします。final approvalはreport承認で、deployはしません。

## Things to try

1. test failureでStart experiment。濃く表示される担当nodeとManager decisionを追う。
2. 終了後に**Replay**でゆっくり再生する。**Step**、slider、右側のevent選択でも任意の時点へ移動できる。**Follow latest**で最新eventの表示に戻る。
3. review failureとtest failureを比較する。前者のDeveloper再選択はreplan countに加算されないことを確認する。
4. Tool eventを選び、Selected eventのJSONで実際に書いたcode、unittest出力とexit codeを読む。Fixtureへ向かう線が対応する。
5. Plan approvalを有効にし、承認前にDeveloperが動かないことを確認する。
6. JSONをexportする。`runs/<id>/events.jsonl`には各eventが逐次保存される。
7. Liveで同じtaskを複数回実行し、選択順序、round数、不要な繰り返しを比較する。

## Findings

- mockはMagenticそのものの挙動を測定しません。観察UIを学ぶための対照シナリオです。
- liveは12 rounds / 2 consecutive stalls / 2 resetsの上限。特定の順番やfailure/replan発生は保証されません。
- review指摘→Developer再選択と、managerによるplan resetを区別して記録します。
- 同時実行は1件。server再起動後はUIのrun一覧を復元しません。JSONLとfixtureはdiskに残ります。
- approvalはapprove/rejectのみ。自然言語のplan revision入力は次の実験です。
- Replayは既存eventの表示位置を変えるだけで、agent・tool・テストを再実行しません。Mockは短時間で終わるため、細かい判断はReplayで追うと観察しやすくなります。
- グラフは記録されたeventの可視化です。LLM内部の思考や未記録の処理を表すものではありません。
- credentialがない環境ではliveの実LLM応答までは検証できません。missing credentialは当該runのerrorとなり、Portalやmockは継続できます。

## References

2026-09-20の公式ドキュメント・installed SDKのsignatureで確認。

- [Official Magentic orchestration](https://learn.microsoft.com/en-us/agent-framework/workflows/orchestrations/magentic)
- [Microsoft Agent Framework source / samples](https://github.com/microsoft/agent-framework)
