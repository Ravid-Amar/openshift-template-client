"""Process an existing OpenShift Template and apply/delete its resources.

Dependency: pip install kubernetes
Python: 3.9+

Example (local kubeconfig):
    import openshift_templates as ocp

    api = ocp.connect()  # Inside an OCP pod: connect(in_cluster=True)
    params = {"VM_NAME": "worker-001", "IMAGE_TAG": "1.2.3"}
    refs = ocp.create("worker-template", params, namespace="workers", api=api)

    # Save refs as JSON in your scheduler's persistent state, then later:
    ocp.delete(namespace="workers", resources=refs, api=api)

    # Alternatively, reprocess the SAME template and parameters for deletion:
    ocp.delete("worker-template", params, namespace="workers", api=api)

create() uses server-side apply: existing objects are updated, missing objects
are created. Conflicting fields owned by another manager raise an error.
It returns only resource identities (including UIDs), not Secret contents.

Prefer saved refs for deletion: they survive template changes and preserve
generated names. UID preconditions prevent deleting a replacement object.
Template-based deletion requires unchanged names and explicit values for any
parameters that would otherwise be generated randomly.

Only namespaced resources in the target namespace are accepted. Supply a
unique VM_NAME (or equivalent naming parameter) for each scheduler job. Objects
shared by jobs should be managed separately from per-job templates.

Operations are sequential, not transactional. On partial failure, OperationError
exposes .resources (successful operations) and .failures (resource + exception).
For create failures, save .resources for cleanup; automatic rollback is avoided
because an apply may have updated an existing object. Delete attempts all objects
in reverse order, ignores missing objects, and requests background deletion.
Success means API acceptance, not VM readiness or completed deletion.

RBAC: get templates in the template namespace; create processedtemplates in the
target namespace; create/patch/delete the rendered resource kinds there.
API discovery access is also required. No cluster-scoped mutations are used.
"""

from copy import deepcopy

from kubernetes import client, config
from kubernetes.dynamic import DynamicClient
from kubernetes.dynamic.exceptions import NotFoundError

API_VERSION = "template.openshift.io/v1"
REQUEST_TIMEOUT = (10, 60)  # Connection timeout, response timeout in seconds.


class OperationError(RuntimeError):
    """Partial failure; .resources and .failures let the scheduler recover."""

    def __init__(self, operation, resources, failures):
        self.resources = resources
        self.failures = failures
        labels = [f"{ref['kind']}/{ref['metadata']['name']}" for ref, _ in failures]
        super().__init__(f"{operation} failed for {', '.join(labels)}")


def connect(*, in_cluster=False, kubeconfig=None, context=None):
    """Create one reusable client from kubeconfig or the pod's ServiceAccount."""
    if in_cluster:
        configuration = client.Configuration()
        config.load_incluster_config(client_configuration=configuration)
        return DynamicClient(client.ApiClient(configuration))
    return DynamicClient(config.new_client_from_config(config_file=kubeconfig, context=context))


def process(
    template_name, parameters, *, namespace, template_namespace=None, api=None, for_delete=False
):
    """Fetch and process a stored Template; return rendered manifest dictionaries."""
    api = api if api is not None else connect()
    templates = api.resources.get(api_version=API_VERSION, name="templates")
    template = templates.get(
        name=template_name,
        namespace=template_namespace or namespace,
        _request_timeout=REQUEST_TIMEOUT,
    ).to_dict()
    template = deepcopy(template)
    # Stored-object metadata is unnecessary when posting to processedtemplates.
    template["metadata"] = {"name": template_name}
    template.pop("status", None)

    parameters = parameters or {}
    definitions = {p["name"]: p for p in template.get("parameters", [])}
    unknown = set(parameters) - set(definitions)
    if unknown:
        raise ValueError(f"Unknown template parameters: {', '.join(sorted(unknown))}")

    for name, value in parameters.items():
        if value is None:
            raise ValueError(f"Parameter {name} cannot be None")
        parameter = definitions[name]
        parameter["value"] = str(value).lower() if isinstance(value, bool) else str(value)
        parameter.pop("generate", None)
        parameter.pop("from", None)

    if for_delete:
        generated = [
            p["name"] for p in definitions.values() if p.get("generate") and not p.get("value")
        ]
        if generated:
            raise ValueError(
                "Deletion would regenerate parameters: "
                + ", ".join(generated)
                + ". Supply their original values or use saved resource references."
            )

    processor = api.resources.get(api_version=API_VERSION, name="processedtemplates")
    return processor.create(
        body=template,
        namespace=namespace,
        _request_timeout=REQUEST_TIMEOUT,
    ).to_dict()["objects"]


def _targets(api, objects, namespace):
    """Validate every object before applying/deleting any of them."""
    if not namespace:
        raise ValueError("namespace is required")
    targets = []
    for original in objects:
        obj = deepcopy(original)
        metadata = obj.setdefault("metadata", {})
        if not metadata.get("name"):
            raise ValueError("Every resource must have metadata.name; generateName is unsupported")
        resource = api.resources.get(api_version=obj["apiVersion"], kind=obj["kind"])
        if not resource.namespaced:
            raise ValueError(f"Cluster-scoped resource is unsupported: {obj['kind']}")
        if metadata.get("namespace") not in (None, "", namespace):
            raise ValueError(f"Resource {metadata['name']} targets a different namespace")
        metadata["namespace"] = namespace
        targets.append((resource, obj))
    return targets


def _reference(obj):
    metadata = obj["metadata"]
    return {
        "apiVersion": obj["apiVersion"],
        "kind": obj["kind"],
        "metadata": {key: metadata[key] for key in ("name", "namespace", "uid") if key in metadata},
    }


def create(
    template_name,
    parameters=None,
    *,
    namespace,
    template_namespace=None,
    api=None,
    field_manager="mini-scheduler",
):
    """Process + apply; return JSON-serializable resource references for cleanup."""
    api = api if api is not None else connect()
    objects = process(
        template_name,
        parameters,
        namespace=namespace,
        template_namespace=template_namespace,
        api=api,
    )
    targets = _targets(api, objects, namespace)
    applied = []
    for resource, obj in targets:
        # Apply desired configuration, not server-maintained fields.
        obj.pop("status", None)
        for key in (
            "uid",
            "resourceVersion",
            "managedFields",
            "creationTimestamp",
            "generation",
            "deletionTimestamp",
            "deletionGracePeriodSeconds",
        ):
            obj["metadata"].pop(key, None)
        try:
            result = api.server_side_apply(
                resource,
                body=obj,
                namespace=namespace,
                field_manager=field_manager,
                force_conflicts=False,
                _request_timeout=REQUEST_TIMEOUT,
            ).to_dict()
            applied.append(_reference(result))
        except Exception as error:
            raise OperationError("Apply", applied, [(_reference(obj), error)]) from error
    return applied


def delete(
    template_name=None,
    parameters=None,
    *,
    namespace,
    template_namespace=None,
    resources=None,
    api=None,
):
    """Delete saved references OR reprocess a template and delete its resources.

    Saved references are preferred. If passed, template_name/parameters must be
    omitted. Missing resources are treated as successfully deleted. UID conflicts
    and other API errors remain visible through OperationError.failures.
    """
    api = api if api is not None else connect()
    if resources is None:
        if not template_name:
            raise ValueError("Provide template_name or resources")
        resources = process(
            template_name,
            parameters,
            namespace=namespace,
            template_namespace=template_namespace,
            api=api,
            for_delete=True,
        )
    elif template_name is not None or parameters is not None:
        raise ValueError("Provide resources OR template_name/parameters, not both")

    targets = _targets(api, resources, namespace)
    deleted, failures = [], []
    for resource, obj in reversed(targets):
        ref = _reference(obj)
        options = {
            "apiVersion": "v1",
            "kind": "DeleteOptions",
            "propagationPolicy": "Background",
        }
        if ref["metadata"].get("uid"):
            options["preconditions"] = {"uid": ref["metadata"]["uid"]}
        try:
            resource.delete(
                name=ref["metadata"]["name"],
                namespace=namespace,
                body=options,
                _request_timeout=REQUEST_TIMEOUT,
            )
            deleted.append(ref)
        except NotFoundError:
            deleted.append(ref)
        except Exception as error:
            failures.append((ref, error))
    if failures:
        raise OperationError("Delete", deleted, failures) from failures[0][1]
    return deleted
