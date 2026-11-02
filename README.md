# openshift-template-client

A small Python package for fetching existing OpenShift templates, processing
parameters through OpenShift, and applying/deleting the resulting resources.
Designed for use by a separate mini scheduler.

**This is a runnable starter for a Codex upgrade.** The previous module is
packaged under `src/openshift_templates/`. The intended improvements are in
[docs/requirements.md](docs/requirements.md); they are not all implemented yet.

Start with [START_HERE.md](START_HERE.md). Project instructions live in
[AGENTS.md](AGENTS.md), and the initial task is [CODEX_PROMPT.md](CODEX_PROMPT.md).

## Install for development

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

Python 3.9+. The only direct runtime dependency is `kubernetes`.

## Current baseline usage

Use explicit values for all template parameters that have random generators.
The template in this example already exists in OpenShift.

```python
import openshift_templates as ocp

api = ocp.connect()  # Local kubeconfig; accepts kubeconfig= and context=.
params = {"VM_NAME": "worker-001", "IMAGE_TAG": "1.2.3"}

# Process and apply. Missing objects are created; existing ones may be updated.
resources = ocp.create(
    "worker-template",
    params,
    namespace="workers",
    template_namespace="infra",
    api=api,
)
```

Later, even in another process, delete using the same inputs:

```python
import openshift_templates as ocp

api = ocp.connect()
ocp.delete(
    "worker-template",
    {"VM_NAME": "worker-001", "IMAGE_TAG": "1.2.3"},
    namespace="workers",
    template_namespace="infra",
    api=api,
)
```

Inside an OpenShift pod, use `ocp.connect(in_cluster=True)` instead. Its
ServiceAccount must have the appropriate namespace-scoped permissions.

For processing only, call `ocp.process()` with the same template/namespace/client
arguments. It returns rendered manifest dictionaries without applying them.

The baseline also supports `ocp.delete(namespace="workers", resources=resources,
api=api)`. This optional path can use returned UIDs; it is not required for the
template-based workflow.

## Stateless assumptions

- Template-based deletion needs the same resource kinds, names, and namespaces
  as the original run. Keep the template revision compatible and reuse inputs.
- Renaming/removing a resource in the source template can leave the old object
  outside the set reconstructed by delete. Apply does not automatically prune.
- Use unique names per scheduler job. Do not place shared resources in templates
  deleted independently by each job.
- Without saved UIDs, template-based deletion is by name and does not distinguish
  an original object from a later replacement with the same identity.
- The baseline permits generation during create but blocks unresolved generators
  during template-based delete. The initial Codex task aligns both behaviors.

## Failures and completion

`OperationError.resources` contains successful operations, and `.failures`
contains resource references paired with exceptions. Apply stops at its first
failed mutation; delete attempts all targets. Missing delete targets are ignored.
No automatic rollback is performed.

Success means API acceptance, not VM readiness or completed background deletion.
Multi-object operations are not transactional. The baseline timeout is fixed at
10 seconds to connect and 60 seconds to read; making it configurable is part
of the upgrade. A mutation timeout can leave the result unknown.

## Permissions

Source namespace: `get` on `templates.template.openshift.io`.
Target namespace: `create` on `processedtemplates.template.openshift.io` and
`create`, `patch`, and `delete` on the resource kinds in the template.
API discovery access is also needed. Cluster-scoped objects are rejected.

See [docs/api-notes.md](docs/api-notes.md) for endpoint and reference details.

## Validation

```bash
python -m pytest
python -m ruff check .
python -m ruff format --check .
python -m build
```

The tests mock the HTTP transport while exercising the actual Kubernetes
dynamic client. Live OpenShift integration remains a separate verification step.

The starter was checked with Python 3.12 and kubernetes 34.1.0. Package/test
syntax was also checked against Python 3.9 grammar; execution on a Python 3.9
interpreter remains a target-environment check.
