"""Synchronous OpenShift Template processing and stateless resource lifecycle."""

import re
from base64 import b64encode
from collections.abc import Mapping
from contextlib import contextmanager
from copy import deepcopy
from math import isfinite
from typing import Any, Dict, Iterator, List, Optional, Tuple, Union

from kubernetes import client, config
from kubernetes.dynamic import DynamicClient
from kubernetes.dynamic.exceptions import NotFoundError
from kubernetes.dynamic.resource import Resource

API_VERSION = "template.openshift.io/v1"
RequestTimeout = Union[float, Tuple[float, float]]
Parameters = Mapping[str, Union[str, bool, int, float]]
Manifest = Dict[str, Any]
REQUEST_TIMEOUT: RequestTimeout = (10, 60)


class OperationError(RuntimeError):
    """Mutation failures: operation name, successful references, and (reference, error) pairs.

    Messages contain identities only; original exceptions can include response bodies.
    """

    def __init__(
        self,
        operation: str,
        resources: List[Manifest],
        failures: List[Tuple[Manifest, Exception]],
    ) -> None:
        """Keep successful references and original failures; format an identity-only message."""
        self.operation = operation
        self.resources = resources
        self.failures = failures
        labels = [f"{ref['kind']}/{ref['metadata']['name']}" for ref, _ in failures]
        super().__init__(f"{operation} failed for {', '.join(labels)}")


def _validate_timeout(request_timeout: RequestTimeout) -> None:
    """Reject invalid or unbounded timeout settings before opening a connection."""
    values = request_timeout if isinstance(request_timeout, tuple) else (request_timeout,)
    if (isinstance(request_timeout, tuple) and len(values) != 2) or any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(value)
        or value <= 0
        for value in values
    ):
        raise ValueError("request_timeout must be positive seconds or a (connect, read) pair")


def _validate_inputs(
    template_name: Optional[str],
    parameters: Optional[Parameters],
    namespace: str,
    template_namespace: Optional[str],
    request_timeout: RequestTimeout,
    resources: Optional[List[Manifest]] = None,
) -> None:
    """
    Check namespaces, timeout, and either template inputs or references before connecting.
    “DNS subdomain” describes the name’s format—it doesn’t require a real domain or DNS record.
    """
    namespaces = {"namespace": namespace}
    if template_namespace is not None:
        namespaces["template_namespace"] = template_namespace
    for argument, value in namespaces.items():
        if (
            not isinstance(value, str)
            or len(value) > 63
            or not re.fullmatch(r"[a-z0-9](?:[-a-z0-9]*[a-z0-9])?", value)
        ):
            raise ValueError(f"{argument} must be a nonempty DNS label of at most 63 characters")
    _validate_timeout(request_timeout)
    # get/delete have two exclusive input modes. An empty reference list is valid;
    # None selects template replay, which requires a template_name below.
    if resources is not None:
        if template_name is not None or parameters is not None or template_namespace is not None:
            raise ValueError("Provide resources OR template inputs, not both")
        if not isinstance(resources, list) or any(not isinstance(obj, dict) for obj in resources):
            raise ValueError("resources must be a list of manifest dictionaries")
        return
    if (
        not isinstance(template_name, str)
        or len(template_name) > 253
        or not re.fullmatch(
            r"[a-z0-9](?:[-a-z0-9]*[a-z0-9])?(?:\.[a-z0-9](?:[-a-z0-9]*[a-z0-9])?)*", template_name
        )
    ):
        raise ValueError("template_name must be a nonempty DNS subdomain")
    if parameters is not None:
        if not isinstance(parameters, Mapping) or any(
            not isinstance(name, str) or not name for name in parameters
        ):
            raise ValueError("parameters must be a mapping with nonempty string names")
        for name, value in parameters.items():
            if value is None:
                raise ValueError(f"Parameter {name} cannot be None")
            if not isinstance(value, (str, bool, int, float)) or (
                isinstance(value, float) and not isfinite(value)
            ):
                raise ValueError(
                    f"Parameter {name} must be a string, boolean, integer, or finite float."
                )


def connect(
    *,
    in_cluster: bool = False,
    kubeconfig: Optional[str] = None,
    context: Optional[str] = None,
    verify_ssl: bool = True,
    request_timeout: RequestTimeout = REQUEST_TIMEOUT,
) -> DynamicClient:
    """Return a reusable DynamicClient using local kubeconfig or pod credentials.

    kubeconfig/context select local credentials; in_cluster selects the ServiceAccount.
    request_timeout is seconds or (connect, read) seconds for discovery/direct calls.
    verify_ssl enables certificate verification by default; False disables it for
    discovery and all requests through this client. Transport retries are disabled. The caller
    must close api.client. Invalid settings raise ValueError; authentication and
    API errors propagate. Discovery failures close the transport before raising.

    Example: api = connect(kubeconfig="/path/to/config", context="dev")"""
    _validate_timeout(request_timeout)
    if in_cluster and (kubeconfig is not None or context is not None):
        raise ValueError("in_cluster cannot be combined with kubeconfig or context")
    configuration = client.Configuration()
    if in_cluster:
        config.load_incluster_config(client_configuration=configuration)
    else:
        config.load_kube_config(
            config_file=kubeconfig,
            context=context,
            client_configuration=configuration,
            persist_config=False,
        )
    configuration.verify_ssl = verify_ssl
    configuration.debug = False
    # A lost mutation response does not prove failure; retrying could mutate again.
    configuration.retries = 0
    transport = client.ApiClient(configuration)
    # DynamicClient's discovery calls do not accept a timeout argument. Set a
    # transport default, while allowing explicit per-operation overrides.
    original_call = transport.call_api

    def call_api(*args: Any, **kwargs: Any) -> Any:
        """Give discovery requests a timeout when DynamicClient passes None."""
        if kwargs.get("_request_timeout") is None:
            kwargs["_request_timeout"] = request_timeout
        return original_call(*args, **kwargs)

    transport.call_api = call_api
    try:
        return DynamicClient(transport)
    except Exception:
        transport.close()
        raise


@contextmanager
def _using_api(
    api: Optional[DynamicClient], request_timeout: RequestTimeout
) -> Iterator[DynamicClient]:
    """Reuse an injected client; open and always close a local client when omitted."""
    owned = api is None
    active = connect(request_timeout=request_timeout) if owned else api
    try:
        yield active
    finally:
        if owned:
            active.client.close()


def process(
    template_name: str,
    parameters: Optional[Parameters] = None,
    *,
    namespace: str,
    template_namespace: Optional[str] = None,
    api: Optional[DynamicClient] = None,
    for_delete: bool = False,
    request_timeout: RequestTimeout = REQUEST_TIMEOUT,
) -> List[Manifest]:
    """Return manifests rendered by OpenShift, without applying them.

    template_name is fetched from template_namespace (defaults to namespace).
    parameters override matching defaults; unresolved generators are rejected.
    namespace selects the processing endpoint. api is caller-owned; if omitted,
    a local client is opened/closed. request_timeout is seconds or (connect, read).
    for_delete is retained for compatibility; deterministic checks always apply.
    Raises ValueError for invalid inputs/parameters; API and transport errors
    propagate. Returned dictionaries can contain Secret data.

    Example: process("worker", {"VM_NAME": "job-001"}, namespace="workers")"""
    _validate_inputs(template_name, parameters, namespace, template_namespace, request_timeout)
    with _using_api(api, request_timeout) as active:
        templates = active.resources.get(api_version=API_VERSION, kind="Template", name="templates")
        # Read from the source namespace, but process into the target namespace.
        # All overrides affect this copy; the stored Template stays unchanged.
        template = deepcopy(
            templates.get(
                name=template_name,
                namespace=template_namespace if template_namespace is not None else namespace,
                _request_timeout=request_timeout,
            ).to_dict()
        )
        # Source UID, namespace, and resourceVersion do not belong on a processing POST.
        template["metadata"] = {"name": template_name}
        template.pop("status", None)
        definitions = {p["name"]: p for p in template.get("parameters", [])}
        unknown = set(parameters or {}) - set(definitions)
        if unknown:
            raise ValueError(f"Unknown template parameters: {', '.join(sorted(unknown))}")
        for name, value in (parameters or {}).items():
            parameter = definitions[name]
            parameter["value"] = str(value).lower() if isinstance(value, bool) else str(value)
            # Even an explicit empty string is deterministic and disables generation.
            parameter.pop("generate", None)
            parameter.pop("from", None)
        # Apply the same rule to every operation so replay after restart cannot
        # silently generate different names or values. OpenShift does substitution.
        generated = [
            p["name"] for p in definitions.values() if p.get("generate") and not p.get("value")
        ]
        if generated:
            raise ValueError(
                "Unresolved parameter generators: "
                + ", ".join(generated)
                + ". Supply explicit values for deterministic replay."
            )
        processor = active.resources.get(
            api_version=API_VERSION, kind="Template", name="processedtemplates"
        )
        objects = (
            processor.create(body=template, namespace=namespace, _request_timeout=request_timeout)
            .to_dict()
            .get("objects", [])
        )
        if not isinstance(objects, list):
            raise ValueError("Processed Template objects must be a list")
        return objects


def _targets(
    api: DynamicClient, objects: List[Manifest], namespace: str
) -> List[Tuple[Resource, Manifest]]:
    """Return discovered resources paired with copied, namespace-normalized manifests.

    Validate every identity and reject generated names, cross-namespace targets,
    and cluster-scoped kinds before any target resource is read or mutated.
    API discovery may contact the server during validation."""
    if not isinstance(objects, list):
        raise ValueError("resources must be a list of manifest dictionaries")
    targets = []
    for original in objects:
        obj = deepcopy(original)
        if not isinstance(obj, dict) or not isinstance(obj.get("metadata"), dict):
            raise ValueError("Every resource must be a manifest with a metadata dictionary")
        metadata = obj["metadata"]
        if "generateName" in metadata:
            raise ValueError("metadata.generateName is unsupported; require stable metadata.name")
        for field in ("apiVersion", "kind"):
            if not isinstance(obj.get(field), str) or not obj[field]:
                raise ValueError(f"Every resource must have a nonempty {field}")
        if not isinstance(metadata.get("name"), str) or not metadata["name"].strip():
            raise ValueError("Every resource must have a nonempty metadata.name")
        if metadata["name"] in (".", "..") or any(c in metadata["name"] for c in "/%"):
            raise ValueError("metadata.name must be a safe API path segment")
        if metadata.get("namespace") not in (None, "", namespace):
            raise ValueError(f"Resource {metadata['name']} targets a different namespace")
        resource = api.resources.get(api_version=obj["apiVersion"], kind=obj["kind"])
        if not resource.namespaced:
            raise ValueError(f"Cluster-scoped resource is unsupported: {obj['kind']}")
        # An omitted/empty namespace inherits the target; an explicit other one fails.
        metadata["namespace"] = namespace
        targets.append((resource, obj))
    return targets


def _reference(obj: Manifest) -> Manifest:
    """Return only API version, kind, name, namespace, and any UID; exclude payload data."""
    metadata = obj["metadata"]
    return {
        "apiVersion": obj["apiVersion"],
        "kind": obj["kind"],
        "metadata": {key: metadata[key] for key in ("name", "namespace", "uid") if key in metadata},
    }


def _prepare_apply(obj: Manifest) -> None:
    """Remove server-managed fields and normalize core Secret stringData in a copy.

    stringData values become UTF-8/base64 data and override matching data keys.
    Invalid Secret maps raise ValueError before any resource is applied."""
    obj.pop("status", None)
    # A Template may contain exported objects. Their old server-assigned fields
    # must not be submitted as the identity/version of the object being applied.
    fields = (
        "uid resourceVersion managedFields creationTimestamp generation "
        "deletionTimestamp deletionGracePeriodSeconds"
    )
    for key in fields.split():
        obj["metadata"].pop(key, None)
    # stringData does not work well with SSA; normalize it to the persisted data
    # representation, with stringData winning collisions as in the Secret API.
    if obj["apiVersion"] == "v1" and obj["kind"] == "Secret" and "stringData" in obj:
        strings, data = obj.pop("stringData"), obj.setdefault("data", {})
        if not isinstance(strings, dict) or not isinstance(data, dict):
            raise ValueError("Secret stringData and data must be dictionaries")
        for key, value in strings.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise ValueError("Secret stringData must map string keys to string values")
            data[key] = b64encode(value.encode("utf-8")).decode("ascii")


def create(
    template_name: str,
    parameters: Optional[Parameters] = None,
    *,
    namespace: str,
    template_namespace: Optional[str] = None,
    api: Optional[DynamicClient] = None,
    field_manager: str = "mini-scheduler",
    request_timeout: RequestTimeout = REQUEST_TIMEOUT,
) -> List[Manifest]:
    """Process and server-side apply a Template; return identity references in order.

    References include the UID returned by the server. Retaining these references
    is optional; template-based delete needs only the original job inputs.

    template_name/parameters identify the Template and scalar overrides; namespace
    bounds all targets; template_namespace defaults to namespace. api is caller-owned
    (or a local client is opened/closed). request_timeout is seconds or (connect, read).
    field_manager names the SSA owner; conflicts are never forced. All identities
    are checked before applying. Invalid targets raise ValueError; processing errors
    propagate. First apply failure raises OperationError with earlier successes.
    There is no rollback or readiness wait.

    Example: create("worker", {"VM_NAME": "job-001"}, namespace="workers")"""
    _validate_inputs(template_name, parameters, namespace, template_namespace, request_timeout)
    if not isinstance(field_manager, str) or not field_manager.strip() or len(field_manager) > 128:
        raise ValueError("field_manager must be a nonempty string of at most 128 characters")
    with _using_api(api, request_timeout) as active:
        objects = process(
            template_name,
            parameters,
            namespace=namespace,
            template_namespace=template_namespace,
            api=active,
            request_timeout=request_timeout,
        )
        # Check every target and normalize every Secret before the first mutation.
        targets = _targets(active, objects, namespace)
        for _, obj in targets:
            _prepare_apply(obj)
        applied = []
        for resource, obj in targets:
            try:
                result = active.server_side_apply(
                    resource,
                    name=obj["metadata"]["name"],
                    body=obj,
                    namespace=namespace,
                    field_manager=field_manager,
                    force_conflicts=False,
                    _request_timeout=request_timeout,
                ).to_dict()
                # The apply response supplies the live UID, including when updating
                # an existing object. No separate GET or local UID storage is needed.
                applied.append(_reference(result))
            except Exception as error:
                raise OperationError("Apply", applied, [(_reference(obj), error)]) from error
        return applied


def delete(
    template_name: Optional[str] = None,
    parameters: Optional[Parameters] = None,
    *,
    namespace: str,
    template_namespace: Optional[str] = None,
    resources: Optional[List[Manifest]] = None,
    api: Optional[DynamicClient] = None,
    request_timeout: RequestTimeout = REQUEST_TIMEOUT,
) -> List[Manifest]:
    """Reprocess a Template and delete its identities in reverse order, without inventory.

    Use the same template_name/parameters and compatible revision as create.
    namespace bounds deletion; template_namespace defaults to namespace. Alternatively,
    resources accepts references instead: omit template_name (its default is None)
    and all other template inputs. One input mode is required; do not mix them.
    Supplied UIDs become deletion preconditions; references without UIDs are valid.
    Template replay deletes by name/namespace, without fetching live resource UIDs.
    api is caller-owned (or a local client is opened/closed). request_timeout is
    seconds or (connect, read) per request.
    Returns successful references, including 404s. Invalid targets raise ValueError;
    processing errors propagate. Other delete failures are collected in OperationError
    after attempting all targets. Name-based replay has no original-UID protection.

    Examples:
        delete("worker", {"VM_NAME": "job-001"}, namespace="workers")
        delete(namespace="workers", resources=refs)  # Optional references from create.
    """
    _validate_inputs(
        template_name, parameters, namespace, template_namespace, request_timeout, resources
    )
    with _using_api(api, request_timeout) as active:
        if resources is None:
            # Reconstruct identities from the stored Template and job inputs. This
            # works in a fresh process without a create result, inventory, or UID.
            resources = process(
                template_name,
                parameters,
                namespace=namespace,
                template_namespace=template_namespace,
                api=active,
                request_timeout=request_timeout,
            )
        # Validate the entire set before issuing the first DELETE.
        targets = _targets(active, resources, namespace)
        deleted, failures = [], []
        for resource, obj in reversed(targets):
            ref = _reference(obj)
            options = {
                "apiVersion": "v1",
                "kind": "DeleteOptions",
                "propagationPolicy": "Background",
            }
            # Validation permits template_name=None only in resources mode.
            # A caller-retained UID optionally protects against deleting a replacement
            # with the same name (the server rejects a mismatch with 409). Template
            # replay must not trust a UID embedded in a rendered/exported manifest.
            # No live UID is fetched: it could belong to a replacement object.
            if template_name is None and ref["metadata"].get("uid"):
                options["preconditions"] = {"uid": ref["metadata"]["uid"]}
            try:
                resource.delete(
                    name=ref["metadata"]["name"],
                    namespace=namespace,
                    body=options,
                    _request_timeout=request_timeout,
                )
                # API acceptance does not mean finalizers/background deletion finished.
                deleted.append(ref)
            except NotFoundError:
                # An absent target already satisfies deletion, including on replay.
                deleted.append(ref)
            except Exception as error:
                # Continue cleanup, then expose every failure and earlier success.
                failures.append((ref, error))
        if failures:
            raise OperationError("Delete", deleted, failures) from failures[0][1]
        return deleted


def get(
    template_name: Optional[str] = None,
    parameters: Optional[Parameters] = None,
    *,
    namespace: str,
    template_namespace: Optional[str] = None,
    resources: Optional[List[Manifest]] = None,
    api: Optional[DynamicClient] = None,
    request_timeout: RequestTimeout = REQUEST_TIMEOUT,
) -> List[Manifest]:
    """Read deployed resources and return their full live dictionaries.

    Supply template_name/parameters, or resources containing identity references.
    Omit template_name (default None) and other template inputs when using resources.
    Reads use name/namespace and return the current object; supplied UIDs are not
    checked, so get() cannot establish an original object's UID after replacement.
    namespace bounds all reads; template_namespace locates the source Template.
    api is caller-owned; without it a local client is opened and closed.
    request_timeout is seconds or (connect, read) seconds per request.
    Results follow rendered/reference order and include each kind's own status,
    if present. There is no readiness interpretation or polling. Results can
    contain Secret data. ValueError reports invalid inputs/targets; API errors
    (including 404/403) and timeouts propagate immediately, without partial results.

    Example: get("worker", {"VM_NAME": "job-001"}, namespace="workers")
    """
    _validate_inputs(
        template_name, parameters, namespace, template_namespace, request_timeout, resources
    )
    with _using_api(api, request_timeout) as active:
        if resources is None:
            resources = process(
                template_name,
                parameters,
                namespace=namespace,
                template_namespace=template_namespace,
                api=active,
                request_timeout=request_timeout,
            )
        targets = _targets(active, resources, namespace)
        return [
            resource.get(
                name=obj["metadata"]["name"], namespace=namespace, _request_timeout=request_timeout
            ).to_dict()
            for resource, obj in targets
        ]
