# Change verification

DevOps Agent **v0.1.1** is pinned to its GitHub release archive by package.json and
pnpm-lock.yaml. Node.js 22+, pnpm 11.19.0, Git and Python 3.11+ are required.

```sh
pnpm install --frozen-lockfile
pnpm exec devops-agent --version
# Run from a clean, committed feature branch against main:
pnpm devops:check
pnpm devops:report
# An explicit immutable comparison is also supported:
pnpm exec devops-agent run --base <base-sha> --head HEAD --json
```

`config.yaml` registers the exact executable commands. All six baseline checks
run on every change: root build, typecheck, lint (including metadata validation),
all root tests, Python mock-runner tests, and the CI evidence-gate regression
suite. They need neither Docker nor LLM credentials. Tests execute for real;
review and test planning currently use **mock** executors. Mock does not inspect
code semantically and does not replace human or real-agent review.

The lifecycle persists revision-bound review, plan, command/exit-code evidence,
and deterministic release assessment under `runs/` (ignored by Git). Verification
requires HEAD to match the source revision and a clean checkout. No commit yet:
create the first commit before running the lifecycle. First-push CI compares HEAD
with itself and executes the full baseline, explicitly reporting that no change
diff was reviewed. Ordinary PRs compare head/base SHAs; pushes use the prior SHA.

`.github/workflows/devops-agent.yml` runs on PRs, pushes to main and manual dispatch.
The required job name is **readiness**. Its scope is automated evidence, not
release approval. The separate `github-settings-check` validates and mock-tests
Terraform without applying repository settings or using credentials.

| Agent result | readiness job |
| --- | --- |
| ready, exit 0, complete matching passed evidence | Pass |
| needs-review, exit 3, only the exact mock advisory, all required evidence passed | Automated checks pass; release stays needs-review, prominently shown in summary |
| Required manual/missing/stale/failed evidence, blockers, unknown warnings, errors or skipped stages | Fail |

`ci-readiness.mjs` checks evidence by test ID, command, cwd and both revisions;
it never rewrites the original release assessment. Raw report, CI interpretation
and persisted evidence are uploaded for 14 days. A green job is not permission to
release. Browser, real OpenFGA, MCP host/protocol and live Magentic coverage remain
separate evidence requirements when relevant; a mock planner cannot discover all
missing scenarios. The v0.1.1 CLI cannot import manual test evidence. Do not mark
unperformed validation as passed or automate `release-check --approve-by`.

Real review/planning and incremental harness improvements: [DevOps workflow](../docs/devops.md).
Upstream: https://github.com/yomote/devops-agent/tree/v0.1.1
