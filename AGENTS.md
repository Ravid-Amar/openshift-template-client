# Project instructions

Build a minimal, synchronous Python package named `openshift-template-client`,
imported as `openshift_templates`. It wraps OpenShift Template processing and
resource apply/delete for a separate mini scheduler.

Read `docs/requirements.md` and `docs/api-notes.md` before changing behavior.
Read `CODEX_PROMPT.md` for the initial upgrade task. These project requirements
take precedence over generic skill preferences.

## Keep it small

- Support Python 3.9+. Do not raise the minimum version as part of this work.
- Use the existing `src/` layout and `pyproject.toml`.
- Keep `kubernetes` as the only direct runtime dependency. Use its DynamicClient.
- Prefer straightforward functions, dictionaries, and a small number of modules.
- Add an abstraction only when it removes demonstrated complexity.
- Keep scheduling, task queues, background reconciliation, and web APIs outside
  this package. Do not introduce an operator, framework, plugin system, or CLI.
- pip/venv must remain supported; uv may be used as optional development tooling.

## Stateless lifecycle

- `create()` and `delete()` must work using the stored template name, template
  namespace, target namespace, and parameters, including after a process restart.
- Do not require a saved inventory, database, or previous `create()` return value.
- Returning resource references/results in memory is fine. The caller need not
  persist them. An existing optional reference-based deletion path may remain
  if it stays small, but it must not become required.
- For the first release, reject unresolved random parameter generators in BOTH
  create and template-based delete. Explicit parameter values may override them.
  This intentionally conservative rule makes replay predictable.
- Require stable `metadata.name`; reject `generateName`.
- Document that stateless replay depends on a compatible template revision and
  identical resource identities. Do not claim recovery after arbitrary template
  changes or original-UID protection without retaining that UID.

## OpenShift behavior

- Fetch an existing Template and submit a copy to `processedtemplates`.
- Let OpenShift substitute parameters. Do not implement a local template engine.
- Use direct API calls; no `oc`/`kubectl` subprocesses.
- Keep template and target namespaces separate and explicit.
- Accept only namespaced resources in the requested target namespace. Validate
  all rendered targets before applying or deleting any of them.
- Do not create CRDs, Namespaces, ClusterRoles, or other cluster-scoped resources.
- Apply with server-side apply; preserve field conflicts rather than forcing them.
- Delete in reverse rendered order. Treat 404 as already absent. Preserve 403,
  409, validation failures, timeouts, and partial failures for the caller.
- Do not add automatic rollback or blind retries of mutations.
- Support explicit kubeconfig/context and in-cluster ServiceAccount auth.
- Enable TLS verification by default, with explicit `connect(verify_ssl=False)` opt-out.
  Make request timeouts configurable.
- Never log tokens, parameter values, full rendered Secrets, or kubeconfig content.
- Importing the package must not authenticate, contact a cluster, or write files.

## Documentation and validation

- Document public functions with type hints, concise docstrings, parameters,
  return values, raised errors, and small usable examples.
- Keep README usage synchronized with actual code. Distinguish current behavior
  from requested behavior until implementation is complete.
- Test the observable API contract and error paths. Mock the transport or inject
  a client; ordinary tests must not require a real OpenShift cluster.
- Include a test that deletes with a fresh client and no previous create result.
- Explicitly test generated-parameter rejection, both namespace boundaries,
  repeated deletion, field conflicts, partial failures, and request timeouts.
- Tests must not mirror private implementation details or require arbitrary
  coverage percentages.
- Keep the package dependency bounds unless evidence justifies a change.

Run before finishing:

```bash
python -m pytest
python -m ruff check .
python -m ruff format --check .
python -m build
```

Report what changed, verification results, and any remaining live-cluster checks.
Continue through implementation and validation; do not stop after proposing a plan.
