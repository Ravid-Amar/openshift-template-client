# Package contract and first upgrade

## Goal

Provide a small synchronous library for a mini scheduler to manage resources
produced by existing OpenShift templates. The scheduler decides when to call it;
the package only processes, applies, and deletes.

Repository: `openshift-template-client`.
Import: `openshift_templates`.
Python: 3.9 or later.
Only direct runtime dependency: `kubernetes`.

## Starting point

`src/openshift_templates/_client.py` contains the previous `ocp_templates.py`
implementation, relocated into an installable package. Formatting and examples
may be adjusted, but its starting runtime behavior is preserved.

The starter already supports processing, server-side apply, template-based
deletion, and optional deletion using returned references. The initial tests
check that baseline. They do not establish that every requirement below is
already implemented.

Known work for Codex:

- Make stateless template-based deletion the primary documented workflow.
- Align generator handling: the baseline allows random generation during create
  but rejects it during template-based delete. Reject unresolved generators in
  both paths for this first release.
- Validate namespace arguments before the first network request.
- Replace the fixed request timeout with a small configurable interface.
- Add public type hints and useful docstrings.
- Expand tests and update examples to the final behavior.
- Review how Secrets containing `stringData` behave with server-side apply.
  If necessary, normalize that field using the standard library and test it.

## Public operations

| Function | Responsibility |
| --- | --- |
| `connect()` | Configure a reusable client using kubeconfig/context or in-cluster credentials. |
| `process()` | Read a stored Template, override parameters, call the processing API, and return rendered objects. |
| `create()` | Process and server-side apply rendered resources. Existing resources may be updated. |
| `delete()` | Reprocess the template and delete rendered resource identities; no inventory required. |

Keep the existing public names. Adjust optional arguments only when it improves
clarity; explain any signature change. Preserve separate template and target
namespaces and client injection. Return simple results or references and expose
partial failures without requiring callers to persist anything.

## Stateless means

Each operation can run in a fresh process with credentials, namespaces, template
name, and parameters. No lifecycle inventory file, database, or retained return
value is required. Authentication configuration is not lifecycle inventory.
An API discovery cache must not become a requirement for reconstructing targets.

The same template revision and parameters must reproduce the same kinds, names,
and namespaces. Require fixed names and explicit values for generated parameters.
Do not attempt to guess whether a generator influences an object's name.

Templates may be changed between runs only if they preserve all relevant
resource identities. Removing/renaming an object in a template prevents replay
from identifying the old object. This limitation must be explicit in README.

Name-based deletion cannot identify the original object UID after replacement.
Use unique names for every scheduler job, avoid names shared between jobs, and
document this assumption. Do not describe stateless deletion as UID-protected.

## Error and security contract

- Validate all rendered targets before mutating any of them.
- Reject cluster-scoped objects and objects in another namespace.
- Unknown parameter names are errors; preserve unspecified template defaults.
- Missing resources during delete are successful no-ops.
- Surface permission errors, field conflicts, timeouts, and partial failures.
- Mutation timeouts can have an unknown outcome. Do not imply that an object was
  definitely unchanged because the client did not receive a response.
- Keep TLS verification enabled and avoid leaking secrets in errors/logging.
- Applying a multi-object template is not a transaction.
- Success means API acceptance, not workload readiness or completed deletion.
- Applying does not automatically prune objects removed from a template.

## Acceptance checks

1. Editable install and wheel/sdist build work using `pyproject.toml`.
2. Public imports work without a cluster connection.
3. Processing requests use the source namespace for GET and target namespace
   for POST; the stored Template is not modified.
4. Apply uses server-side apply with a stable field manager and no forced conflicts.
5. Delete works with a fresh client, template/parameters, and no create inventory.
6. Random generators and `generateName` fail before resource mutations.
7. All error and namespace rules above have meaningful offline tests.
8. README contains working local-auth and in-cluster examples for create/delete,
   minimal namespace-scoped RBAC guidance, and the stateless limitations.
9. pytest, Ruff lint/format checks, and build pass.
10. Final report distinguishes offline verification from live OCP validation.

## Out of scope for this first version

A scheduler engine, operator/CRD, REST service, CLI, Helm wrapper, persistent
inventory, background watcher, automatic pruning, readiness waiting, retries
framework, or automatic rollback.
