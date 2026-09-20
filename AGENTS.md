# Working on Tech Playground

## Architecture

- Add experiments as flat `demos/<id>` vertical slices. Keep runtime dependencies inside each demo.
- `demos/*/demo.yaml` is the metadata source of truth; reuse `packages/demo-schema` in tools and Portal.
- Portal is React + Vite and must not import filesystem APIs. Keep it small and usable without demo credentials.
- Existing React demos use the optional `@playground/ui` theme: navy primary, light gray surfaces, compact controls. Check text contrast in real rendered screens.
- Decision Workbench uses real optional local models. Never replace unavailable inference with fabricated predictions; keep candidate scores separate from correctness, and clear fixture expectations when their input or criteria changes.
- Preserve clear mock/live labels. Never present mock agent decisions, scores, or authorization checks as real service evidence.

## Changes and evidence

- Use the DevOps Agent configuration in `.devops-agent/` to plan and collect verification. Its mock executor does not replace a real code review.
- Run checks appropriate to the change; root commands are `pnpm build`, `pnpm typecheck`, `pnpm lint`, and `pnpm test`.
- Check changed UI in a browser, including graphs, iframe content, disabled states, and narrow layouts where relevant.
- Consult current official specifications before changing MCP Apps, Microsoft Agent Framework, OpenFGA, or provider APIs. Do not guess SDK names or resource schemas.
- Keep secrets, Terraform state, downloaded models, and runtime logs out of Git. Azure infrastructure changes require a reviewed plan/what-if; do not treat passing application tests as deployment authorization.
- Azure Portal access must use the existing Entra tenant, a dedicated single-tenant app with required direct user assignments, and a nonempty Easy Auth user allowlist. Bootstrap with external ingress closed; verify the future public callback and deployed auth before publishing. Keep credentials in Key Vault and never exempt external routes from authentication.
- Share only the Entra tenant with agent-world. Provision dedicated Tech Playground Container Apps resources in the app resource group and Key Vault / Log Analytics in the management resource group. Keep runtime identities in the app group with vault-scoped read access; do not reuse agent-world runtime resources.

## Evolving the agent workflow

- Capture repeated failures as a small reproducible check, fixture, or precise instruction close to the affected demo.
- Promote a repeated workflow to a skill only after it has proved useful; document its inputs, outputs, dependencies, and verification.
- Add MCP integrations when they remove concrete manual work. Document their permissions and use explicit resource handles instead of hidden session assumptions.
- Keep harness changes reviewable alongside code. Do not automatically grant broader permissions, install global agent configuration, or enable deployment because a workflow suggests it.
- Prefer a few reliable checks over a large framework. Separate real failures, missing credentials, manual review needs, and unavailable optional tools in reports.
