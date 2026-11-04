# openshift-template-client

A minimal synchronous Python library for a separate mini scheduler to process
existing OpenShift Templates and create, inspect, or delete their namespaced resources.
Python 3.9+; `kubernetes>=30,<35` is the only direct runtime dependency.

It uses Kubernetes `DynamicClient` discovery, including installed APIs such as
KubeVirt `VirtualMachine`. OpenShift performs parameter substitution; no local
template engine or `oc`/`kubectl` subprocess is used. Scheduling stays in the
caller. Importing the package does not authenticate or contact a cluster.

The Template request contract follows the
[OpenShift Container Platform 4.20 Template API](https://docs.redhat.com/en/documentation/openshift_container_platform/4.20/html/template_apis/template-template-openshift-io-v1).
On OpenShift 4.20, the API version is still `template.openshift.io/v1`;
`4.20` is the platform release. Live-cluster compatibility must be verified in
your target environment; the automated tests use mocked transport.

## Install

After the first successful PyPI release, install remotely without cloning:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install openshift-template-client
```

To select a particular published release, use
`python -m pip install 'openshift-template-client==0.1.1'` (once `0.1.1` is
published). To upgrade later, use `python -m pip install --upgrade
openshift-template-client`. Import the installed package as `openshift_templates`.

Before publishing to PyPI, or to use the GitHub source, install directly from
the repository after pushing the code there. This requires Git:

```bash
python -m pip install 'openshift-template-client @ git+https://github.com/Ravid-Amar/openshift-template-client.git@main'
```

Replace `@main` with a pushed release tag such as `@v0.1.1` to select that
revision. For development from a local clone:

```bash
git clone https://github.com/Ravid-Amar/openshift-template-client.git
cd openshift-template-client
python -m pip install -e '.[dev]'
```

Both pip/venv and the existing `src/` layout are supported. No uv migration is
required. See [START_HERE.md](START_HERE.md) for wheelhouse installation offline.

Pushing a `v*` tag runs the [PyPI publishing workflow](.github/workflows/publish.yml).
The tag must equal `v` plus the version in `pyproject.toml`. See
[Publishing releases](docs/publishing.md) for the one-time GitHub/PyPI setup and
release commands. PyPI installation becomes available after a successful upload;
creating this workflow alone does not publish a package.

## Local kubeconfig: create, then delete after restart

The `worker-template` Template must already exist in `infra`. The target
namespace `workers` and required namespaced APIs must also exist. Adapt the
parameter names to your Template, including explicit values for every unresolved
generator. This example assumes `VM_NAME` and `IMAGE_TAG` are its parameters.

```python
import openshift_templates as ocp

api = ocp.connect(kubeconfig="/path/to/kubeconfig", context="dev", request_timeout=(5, 30))
try:
    refs = ocp.create(
        "worker-template",
        {"VM_NAME": "worker-001", "IMAGE_TAG": "1.2.3"},
        namespace="workers",
        template_namespace="infra",
        api=api,
        request_timeout=(5, 30),
    )
finally:
    api.client.close()
```

Later, in a fresh process, use the same template and inputs. No create result,
resource inventory, or discovery cache needs to be retained:

```python
import openshift_templates as ocp

api = ocp.connect(kubeconfig="/path/to/kubeconfig", context="dev")
try:
    deleted = ocp.delete(
        "worker-template",
        {"VM_NAME": "worker-001", "IMAGE_TAG": "1.2.3"},
        namespace="workers",
        template_namespace="infra",
        api=api,
    )
finally:
    api.client.close()
```

`connect()` without kubeconfig/context uses the Kubernetes client's usual local
kubeconfig search and current context. Omitting `api` from an operation opens a
local client and closes it on success or failure. Injected clients remain open
for reuse; their owner must close `api.client`. `connect()` closes its transport
if discovery fails. TLS certificate verification defaults to `True`; configure
the cluster CA in kubeconfig. To disable verification explicitly, use
`connect(verify_ssl=False)` and pass that client as `api` to operations. This
setting applies to discovery and all resource requests, with either auth mode
(including `connect(in_cluster=True, verify_ssl=False)`):

```python
params = {"VM_NAME": "worker-001", "IMAGE_TAG": "1.2.3"}
api = ocp.connect(kubeconfig="/path/to/kubeconfig", context="dev", verify_ssl=False)
try:
    objects = ocp.get(
        "worker-template", params, namespace="workers", template_namespace="infra", api=api
    )
finally:
    api.client.close()
```

With verification disabled, the server certificate is not authenticated.
Kubeconfig updates and HTTP debug output are disabled by `connect()`.

## In-cluster ServiceAccount

Inside a pod with ServiceAccount credentials and cluster CA mounted, use:

```python
import openshift_templates as ocp

params = {"VM_NAME": "worker-002", "IMAGE_TAG": "1.2.3"}
api = ocp.connect(in_cluster=True)
try:
    ocp.create("worker-template", params, namespace="workers", template_namespace="infra", api=api)
finally:
    api.client.close()

# Later, including in a new pod/process with the same stored job inputs:
api = ocp.connect(in_cluster=True)
try:
    ocp.delete("worker-template", params, namespace="workers", template_namespace="infra", api=api)
finally:
    api.client.close()
```

Do not combine `in_cluster=True` with kubeconfig/context. Grant this
ServiceAccount the permissions below in the source and target namespaces.

## Public API

Import as `openshift_templates`. All operations are synchronous and have type
hints and docstrings with parameters, returns, errors, and examples.

| Function | What it does | Returns |
| --- | --- | --- |
| `connect()` | Loads local kubeconfig/context or in-cluster credentials and discovers APIs. Close its transport with `api.client.close()`. | A reusable `DynamicClient`. |
| `list_templates()` | Lists all stored Templates in a source `namespace`, including continuation pages. | Full Template dictionaries; `[]` when none exist. |
| `list_template_parameters()` | Reads the named Template in a source `namespace`, without processing it. | Parameter definition dictionaries in stored order; `[]` if absent. |
| `process()` | Fetches a stored Template, overrides parameters in a copy, and asks OpenShift to render it. No resources are deployed. | Rendered manifest dictionaries. |
| `create()` | Calls `process()`, validates every target, then applies them in order with server-side apply. `field_manager` defaults to `mini-scheduler`; conflicts are preserved. | Identity references, including returned UIDs. |
| `get()` | Reconstructs template identities and reads the currently deployed objects in order. It takes the same template arguments as `create()`, or `resources=refs`. | Full live dictionaries, including each kind's `status` when present. |
| `delete()` | Reconstructs template identities and deletes them in reverse order. Missing objects are already absent. It also accepts `resources=refs`. | Successful identity references. |

`process`, `create`, `get`, and `delete` accept `template_name`, optional
`parameters`, required target `namespace`, optional `template_namespace`,
optional injected `api`, and `request_timeout`. `get`/`delete` accept either
template inputs or `resources`, never both. Their `template_name` defaults to
`None` to allow the reference mode; it is required when `resources` is omitted.
Supplying neither mode is invalid. All public functions have examples
and explanations in their docstrings; internal helper functions explain their
single responsibility where they are defined.

`template_namespace` defaults to the target `namespace` only when omitted.
Explicitly invalid/empty namespaces fail before authentication or API requests.
Parameters are string-keyed mappings of strings, booleans, integers, or finite
floats; booleans become `true`/`false`, other scalar values become strings.
Unknown names and `None` values are errors. Unspecified Template defaults remain
intact. Overrides remove the parameter's `generate` and `from` fields in a copy;
the stored Template is never changed.

Parameters marked `required: true` must have nonempty values after overrides.
A nonempty default satisfies this check. Missing values and explicit empty
overrides raise `ValueError` before the processing POST, naming all missing
required parameters and the source Template. For example:
`Missing required template parameters for infra/worker-template: VM_NAME. Supply
nonempty values in parameters or define nonempty Template defaults.`
Boolean `False` and numeric `0` overrides are valid (`false` and `0` strings).
Optional parameters may remain empty. Required generated parameters still need
a nonempty fixed default or explicit override for deterministic replay.

Each operation accepts positive seconds or a `(connect, read)` seconds tuple;
the default is `(10, 60)`. This is a per-request timeout, not a whole-operation
deadline. `connect(request_timeout=...)` configures discovery and direct client
calls. Pass the operation's `request_timeout` separately for its Template GET,
processing POST, and resource mutations. With an injected client, discovery uses
that client's configuration. Clients constructed by `connect()` disable
transport retries; injected clients should also disable mutation retries.

`process(..., for_delete=True)` remains accepted for compatibility. Deterministic
parameter checks now apply regardless of that flag, including processing only.
To inspect rendering:

```python
objects = ocp.process(
    "worker-template", params, namespace="workers", template_namespace="infra", api=api
)
```

### Discover Templates and their parameters

These read-only functions use `namespace` for the source namespace; they need no
target namespace and never process Templates. They accept `api` and
`request_timeout` with the same ownership and timeout rules as other operations.

```python
import openshift_templates as ocp

api = ocp.connect()  # Or connect(in_cluster=True) in a pod.
try:
    templates = ocp.list_templates(namespace="infra", api=api)
    names = [template["metadata"]["name"] for template in templates]
    definitions = ocp.list_template_parameters("worker-template", namespace="infra", api=api)
    required_names = [p["name"] for p in definitions if p.get("required", False)]
finally:
    api.client.close()
```

Parameter definitions preserve supplied defaults (`value`), descriptions, display
names, required flags, and generator fields. Omitted fields remain omitted.
Inspection works even when required inputs are missing or generators unresolved.
Full Templates and defaults may contain secrets, so avoid logging the results.
API errors and timeouts propagate directly; listing returns no partial results
if a later page fails.

### Resource UIDs and optional deletion protection

OpenShift assigns each resource a UID and stores it in the live object's
`metadata.uid`. `create()` copies that UID from the server-side apply response
into its returned references. The package does not save those references or UIDs
to disk; retaining them is optional.

Template-based deletion works after a pod/process restart using the same job
inputs. It reprocesses the Template and deletes by name and namespace, without
fetching the live resources or their UIDs. No saved `create()` result is needed.

The optional reference mode skips Template processing and uses any supplied UID
as a deletion precondition:

```python
import openshift_templates as ocp

params = {"VM_NAME": "worker-001", "IMAGE_TAG": "1.2.3"}
api = ocp.connect()  # Or connect(in_cluster=True) in a pod.
try:
    refs = ocp.create(
        "worker-template", params, namespace="workers", template_namespace="infra", api=api
    )
    # template_name defaults to None here because resources supplies the identities.
    # A saved UID protects against deleting a replacement object with the same name.
    ocp.delete(namespace="workers", resources=refs, api=api)
finally:
    api.client.close()
```

If a live object's UID differs from the supplied UID, the server rejects that
deletion with 409, exposed through `OperationError`. References without UIDs are
also accepted and delete by name/namespace. Omit `template_name`, `parameters`,
and `template_namespace` when passing `resources`. An empty reference list is a
valid no-op. A UID embedded in a rendered Template is never used as a deletion
precondition.

`get()` returns current live UIDs, but reading one before deletion cannot prove
that it belongs to the original object: a replacement may already exist. Only
retaining the original UID provides that additional identity protection.

## Inspect deployed resources

Use the same job inputs after restart, or pass `resources=refs` from create.
`get()` reads the main resource endpoints, including KubeVirt VirtualMachines;
it does not poll, wait, or decide whether a workload is ready.

```python
import openshift_templates as ocp

api = ocp.connect()  # Or connect(in_cluster=True) in a pod.
try:
    objects = ocp.get(
        "worker-template",
        {"VM_NAME": "worker-001", "IMAGE_TAG": "1.2.3"},
        namespace="workers",
        template_namespace="infra",
        api=api,
    )
    for obj in objects:
        print(obj["kind"], obj["metadata"]["name"], obj.get("status", {}))
finally:
    api.client.close()
```

These are live responses, unlike the desired manifests from `process()`. A kind
such as ConfigMap may have no `status`. Read errors, including 404 for a missing
resource, 403, and timeouts, propagate immediately; a failed call returns no
partial list. Responses include the full object, so avoid logging Secret objects.
Each object is read sequentially; the list is not an atomic cluster snapshot.

## Stateless replay and namespace boundaries

- Both create and template-based delete reject unresolved parameter generators,
  even if a generator appears unrelated to an object's name. Supply explicit
  values, or use a nonempty fixed default already in the Template. No random
  value is regenerated by these operations.
- Every rendered object needs a stable `metadata.name`. `metadata.generateName`
  is rejected even when a name is also supplied.
- All rendered targets are discovered and validated before any resource apply
  or delete. Only namespaced resources in the requested target namespace are
  accepted. An omitted/empty manifest namespace becomes the target namespace;
  another namespace, or any cluster-scoped kind, is rejected. Namespace and CRD
  creation are outside this library.
- Stateless replay depends on compatible Template revisions and the same
  parameters reproducing identical API resource kinds, names, and namespaces.
  Keep that revision available. Renaming/removing an object can leave the old
  object undiscoverable by replay. Apply does not prune removed objects.
- Use unique names per scheduler job. Manage shared resources separately.
  Template-based deletion is by name: it can delete a replacement with the same
  identity and provides no original-UID protection after restart.

For core `v1` Secrets, create converts UTF-8 `stringData` to base64 `data` before
server-side apply, preserving existing keys and giving `stringData` precedence
on collisions. This follows the API's merge behavior and avoids the documented
[Secret stringData limitations with server-side apply](https://kubernetes.io/docs/concepts/configuration/secret/#working-with-secrets).
Normalization uses only the standard library, works on copies, and does not
change `process()` output. Base64 is not encryption.

## Failures and completion

Invalid arguments and unsafe rendered identities raise `ValueError`. Failures
before resource mutations (authentication, discovery, Template GET/POST,
processing timeouts) propagate directly from the Kubernetes client/transport.
Other schema validation remains the server's responsibility.

Mutation failures raise `OperationError`. Its `.operation` is `Apply` or `Delete`,
`.resources` contains successful identity references, and `.failures` contains
`(reference, original_exception)` pairs. Apply stops at its first failure;
delete attempts every validated target in reverse order. Only an individual
delete's 404 is a successful no-op. Permission errors (403), field/UID conflicts
(409), validation errors, and timeouts remain visible. No rollback or automatic
mutation retry is performed.

```python
try:
    ocp.create("worker-template", params, namespace="workers", template_namespace="infra", api=api)
except ocp.OperationError as error:
    accepted = error.resources
    failed_references = [ref for ref, exception in error.failures]
    # Let your scheduler decide how to reconcile the outcome.
```

Multi-object operations are not transactional. A mutation timeout has an unknown
outcome: the server may have accepted it. Success means API acceptance, not VM
readiness or completed background deletion. The package does not log tokens,
parameter values, or rendered Secrets. References and the `OperationError`
message contain identities only; raw exceptions/tracebacks may include server
response bodies, so avoid logging them wholesale.

## Namespace-scoped permissions

Grant only the API groups/kinds actually used by your Templates. Ordinary API
discovery access is needed; it grants no cluster-scoped mutation permission.
`get()` additionally needs target resource `get` permission. No operation needs
target resource `list` or `watch` permission.

| Namespace | API group / resource | Verbs |
| --- | --- | --- |
| Source (`infra`) | `template.openshift.io` / `templates` | `get` (can restrict to the Template's name); add `list` for `list_templates()` |
| Target (`workers`) | `template.openshift.io` / `processedtemplates` | `create` |
| Target (`workers`) | Each rendered resource, e.g. core `configmaps`, `secrets`; `kubevirt.io` / `virtualmachines` | `create`, `patch`, `delete`; add `get` for inspection |

Example Roles for a Template producing ConfigMaps, Secrets, and VirtualMachines:

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: worker-template-reader
  namespace: infra
rules:
  - apiGroups: [template.openshift.io]
    resources: [templates]
    resourceNames: [worker-template]
    verbs: [get]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: worker-template-manager
  namespace: workers
rules:
  - apiGroups: [template.openshift.io]
    resources: [processedtemplates]
    verbs: [create]
  - apiGroups: [""]
    resources: [configmaps, secrets]
    verbs: [create, patch, delete, get]
  - apiGroups: [kubevirt.io]
    resources: [virtualmachines]
    verbs: [create, patch, delete, get]
```

Have namespace owners bind each Role with a RoleBinding in its namespace to
your user or the pod's ServiceAccount (specify the ServiceAccount's actual
namespace, even for a binding in another namespace). The library does not create
these Roles/bindings. No ClusterRole, CRD installation, or cluster-admin grant is
required by this workflow. KubeVirt must already be installed if used.

For `list_templates()`, add a separate source Role rule granting `list` on
`templates` without `resourceNames`. The named `get` rule above already permits
parameter inspection; listing Templates is optional for create/get/delete.

## Development and validation

```bash
python -m pytest
python -m ruff check .
python -m ruff format --check .
python -m build
```

Tests block real credential loading/network access and mock transport while
exercising the real DynamicClient's HTTP construction. They cover replay with a
fresh client, optional UID preconditions, live status reads, namespace/scope
boundaries, generators, reverse/repeated deletion, SSA conflicts, partial
failures, Template listing/parameter inspection, required-parameter validation,
timeouts (including discovery), Secret
normalization, and client ownership. Builds use `pyproject.toml` and produce a
wheel and sdist; isolated builds may fetch build dependencies.

A clean-environment wheel import can be checked without cluster credentials:

```bash
python -m venv /tmp/openshift-template-wheel-check
/tmp/openshift-template-wheel-check/bin/python -m pip install dist/*.whl
/tmp/openshift-template-wheel-check/bin/python -I -c 'import openshift_templates'
```

For a fully offline install/build, prepare a wheelhouse as described in
[START_HERE.md](START_HERE.md). Python 3.9 syntax compatibility is maintained;
runtime checks here use Python 3.12 and kubernetes 34.1.0. Actual Python 3.9
execution and live OpenShift validation remain target-environment checks.
Live checks should confirm deployed-version processing, RBAC, SSA/Secret and
KubeVirt behavior, and admission/finalizer effects. See
[docs/api-notes.md](docs/api-notes.md) for endpoint details and official sources.

## License

Licensed under the [MIT License](LICENSE).
