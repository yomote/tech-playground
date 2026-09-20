# Repository instructions for DevOps Agent

Tech Playground is a flat collection of independent vertical-slice demos. Demo
runtime/framework choices are free. Existing React Portal and four React demo
frontends share optional @playground/ui Material UI components; preserve their
navy/light-gray design, but do not require this package or React for future demos.
Keep filesystem loading outside browser code and share metadata through the Zod
contract in packages/demo-schema.

Run every registered baseline command: root build, typecheck, lint, full root
tests, Python mock-runner tests, Decision Workbench contract tests and CI evidence-gate tests. The small suite always
runs, including initial-commit baselines. Do not replace it with name filters or
omit tests on configuration-only changes. A real planner may add specific
validation; required catalog tests cannot be weakened. Preserve failures and
unknown results, including manual tests without executable evidence.

UI changes also need browser smoke evidence for affected screens. Root helper
tests cover graph connections, authorization paths, trajectory/replay and MCP
forms; they do not prove rendering, accessibility or browser interaction.
For shared theme changes inspect Portal and all existing React demo screens.
Add required manual validation to a real agent's plan if no registered runnable
browser check covers the change; do not manufacture passing evidence.

MCP protocol/server/UI-resource changes need smoke.ts against a running real MCP
server as well as the local-host interaction loop. Mock experiment results are
not a mock protocol. Explicit experiment handles must work across connections.

MAF workflow changes require python -m unittest -v test_runner. SDK/live changes
also need test_sdk and appropriate live validation. Scripted mock outcomes do not
establish actual Magentic agent performance, selection or convergence.

OpenFGA model/API changes need real-server regression evidence, not only the local
educational evaluator. Preserve allowed and denied checks, grant/revoke behavior,
group membership and folder inheritance. The explanation graph is local model
traversal, not a server-native decision trace. tests/integration.ts currently
couples OpenFGA and MAF HTTP checks; split it before registering independent jobs.

Mock review and planning do not perform semantic analysis. Report this limitation
explicitly. The readiness CI job gates automated evidence only. Never treat its
green status as release approval or silently dismiss a non-mock warning/blocker.
The CLI v0.1.1 does not support importing manual evidence; required manual items
remain blocked until a real executable check is registered and the plan rerun.

Use failures and concrete friction to improve the smallest relevant harness
layer: registered commands, focused regression tests, repository instructions,
reusable skills, or MCP adapters. Avoid adding shared backend/runtime coupling.
Record why a check exists, its limitations, and the observed failure it prevents.
