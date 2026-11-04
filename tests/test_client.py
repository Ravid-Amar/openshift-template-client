"""Exercise the real Kubernetes dynamic client's HTTP construction offline."""

import json
import ssl
import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from kubernetes import client
from kubernetes.client.exceptions import ApiException
from kubernetes.dynamic import DynamicClient
from kubernetes.dynamic.exceptions import ResourceNotFoundError
from kubernetes.dynamic.resource import Resource
from urllib3.exceptions import ReadTimeoutError

import openshift_templates as ocp


def manifest(kind, name, **extra):
    return {
        "apiVersion": "kubevirt.io/v1" if kind == "VirtualMachine" else "v1",
        "kind": kind,
        "metadata": {"name": name},
        **extra,
    }


class FakeDiscovery:
    def __init__(self, dynamic, _cache_file):
        self.dynamic = dynamic

    def get(self, *, api_version, name=None, kind=None):
        group, _, version = api_version.rpartition("/")
        by_name = {
            "templates": "Template",
            "processedtemplates": "Template",
            "configmaps": "ConfigMap",
            "virtualmachines": "VirtualMachine",
            "namespaces": "Namespace",
            "clusterroles": "ClusterRole",
            "customresourcedefinitions": "CustomResourceDefinition",
            "secrets": "Secret",
        }
        if name is None:
            name = next((n for n, k in by_name.items() if k == kind), None)
            if name is None:
                raise ResourceNotFoundError("Requested API is not installed")
        return Resource(
            prefix="apis" if group else "api",
            group=group,
            api_version=version,
            name=name,
            kind=by_name[name],
            namespaced=by_name[name]
            not in ("Namespace", "ClusterRole", "CustomResourceDefinition"),
            client=self.dynamic,
        )


class ModuleTests(unittest.TestCase):
    def setUp(self):
        self.template = {
            "apiVersion": "template.openshift.io/v1",
            "kind": "Template",
            "metadata": {"name": "worker", "namespace": "infra", "uid": "old"},
            "parameters": [
                {"name": "VM_NAME", "value": "default-worker"},
                {"name": "IMAGE", "value": "registry/image:base"},
                {"name": "PASSWORD", "generate": "expression", "from": "[a-z]{12}"},
            ],
            "objects": [],
        }
        self.objects = [
            manifest("ConfigMap", "worker-config"),
            manifest("VirtualMachine", "worker-01"),
        ]
        self.live_objects = deepcopy(self.objects)
        self.live_objects[-1]["status"] = {"printableStatus": "Running"}
        self.calls = []
        self.errors = {}
        self.template_pages = None
        self.api = self.new_api()

    def new_api(self):
        transport = client.ApiClient()
        self.addCleanup(transport.close)
        transport.call_api = Mock(side_effect=self.http)
        return DynamicClient(transport, discoverer=FakeDiscovery)

    def http(self, path, method, path_params, query, headers, **kwargs):
        call = {
            "path": path,
            "method": method,
            "query": dict(query),
            "headers": deepcopy(headers),
            "body": deepcopy(kwargs.get("body")),
            "timeout": kwargs.get("_request_timeout"),
        }
        self.calls.append(call)
        error = self.errors.get((method, path.rsplit("/", 1)[-1]))
        if isinstance(error, Exception):
            raise error
        if error:
            raise ApiException(status=error, reason="simulated API error")
        if method == "GET":
            if path.endswith("/templates"):
                page = (
                    self.template_pages[call["query"].get("continue")]
                    if self.template_pages is not None
                    else {"items": [self.template]}
                )
                result = {
                    "apiVersion": "template.openshift.io/v1",
                    "kind": "TemplateList",
                    **deepcopy(page),
                }
            elif "/templates/" in path:
                result = deepcopy(self.template)
            else:
                result = deepcopy(
                    next(
                        obj
                        for obj in self.live_objects
                        if obj["metadata"]["name"] == path.rsplit("/", 1)[-1]
                    )
                )
        elif method == "POST":
            result = {**deepcopy(self.template), "objects": deepcopy(self.objects)}
        elif method == "PATCH":
            result = deepcopy(kwargs["body"])
            result["metadata"]["uid"] = "uid-" + result["metadata"]["name"]
        else:
            result = {"apiVersion": "v1", "kind": "Status", "status": "Success"}
        return SimpleNamespace(data=json.dumps(result).encode())

    def calls_for(self, method):
        return [c for c in self.calls if c["method"] == method]

    def create(self, params=None):
        return ocp.create(
            "worker",
            {"VM_NAME": "worker-01", "PASSWORD": "explicit", **(params or {})},
            namespace="workers",
            template_namespace="infra",
            api=self.api,
        )

    def test_fetch_process_and_actual_apply_http_contract(self):
        original = deepcopy(self.template)
        refs = self.create()
        self.assertEqual(self.template, original)
        get = self.calls_for("GET")[0]
        post = self.calls_for("POST")[0]
        patch = self.calls_for("PATCH")[-1]
        self.assertEqual(
            get["path"], "/apis/template.openshift.io/v1/namespaces/infra/templates/worker"
        )
        self.assertEqual(
            post["path"], "/apis/template.openshift.io/v1/namespaces/workers/processedtemplates"
        )
        self.assertEqual(post["body"]["metadata"], {"name": "worker"})
        self.assertEqual(post["body"]["parameters"][0]["value"], "worker-01")
        self.assertEqual(post["body"]["parameters"][1]["value"], "registry/image:base")
        self.assertEqual(
            patch["path"], "/apis/kubevirt.io/v1/namespaces/workers/virtualmachines/worker-01"
        )
        self.assertEqual(patch["headers"]["Content-Type"], "application/apply-patch+yaml")
        self.assertEqual(patch["query"], {"fieldManager": "mini-scheduler", "force": False})
        self.assertEqual(
            refs[-1]["metadata"],
            {"name": "worker-01", "namespace": "workers", "uid": "uid-worker-01"},
        )
        json.dumps(refs)

    def test_unknown_parameter_is_rejected_before_processing(self):
        with self.assertRaisesRegex(ValueError, "Unknown template parameters: TYPO"):
            self.create({"TYPO": "value"})
        self.assertEqual(self.calls_for("POST"), [])

    def test_overridden_generator_is_cleared_and_none_rejected(self):
        self.create({"PASSWORD": "original-value"})
        parameter = self.calls_for("POST")[0]["body"]["parameters"][2]
        self.assertEqual(parameter, {"name": "PASSWORD", "value": "original-value"})
        with self.assertRaisesRegex(ValueError, "cannot be None"):
            self.create({"VM_NAME": None})

    def test_all_targets_prevalidated_before_any_apply(self):
        self.objects.append(manifest("Namespace", "forbidden"))
        with self.assertRaisesRegex(ValueError, "Cluster-scoped"):
            self.create()
        self.assertEqual(self.calls_for("PATCH"), [])

    def test_other_namespace_and_generated_names_rejected(self):
        self.objects[1]["metadata"]["namespace"] = "elsewhere"
        with self.assertRaisesRegex(ValueError, "different namespace"):
            self.create()
        del self.objects[1]["metadata"]["namespace"]
        self.objects[1]["metadata"] = {"generateName": "worker-"}
        with self.assertRaisesRegex(ValueError, "metadata.name"):
            self.create()
        self.assertEqual(self.calls_for("PATCH"), [])

    def test_apply_failure_preserves_completed_refs(self):
        self.errors[("PATCH", "worker-01")] = 409
        with self.assertRaises(ocp.OperationError) as caught:
            self.create()
        self.assertEqual(len(caught.exception.resources), 1)
        self.assertEqual(caught.exception.failures[0][1].status, 409)

    def test_delete_saved_refs_reverse_order_uid_preconditions_no_template_read(self):
        refs = self.create()
        self.calls.clear()
        self.template = None  # The source template can be changed/deleted.
        deleted = ocp.delete(namespace="workers", resources=refs, api=self.api)
        self.assertEqual(deleted, list(reversed(refs)))
        self.assertEqual([c["method"] for c in self.calls], ["DELETE", "DELETE"])
        first = self.calls[0]
        self.assertTrue(first["path"].endswith("/virtualmachines/worker-01"))
        self.assertEqual(first["body"]["preconditions"], {"uid": "uid-worker-01"})
        self.assertEqual(first["body"]["propagationPolicy"], "Background")

    def test_missing_deletion_ignored_but_other_errors_do_not_stop_cleanup(self):
        refs = self.create()
        self.errors[("DELETE", "worker-01")] = 409
        self.errors[("DELETE", "worker-config")] = 404
        with self.assertRaises(ocp.OperationError) as caught:
            ocp.delete(namespace="workers", resources=refs, api=self.api)
        self.assertEqual(len(self.calls_for("DELETE")), 2)
        self.assertEqual(caught.exception.resources, [refs[0]])
        self.assertEqual(caught.exception.failures[0][1].status, 409)

    def test_template_deletion_rejects_random_regeneration(self):
        with self.assertRaisesRegex(ValueError, "Unresolved parameter generators: PASSWORD"):
            ocp.delete("worker", {"VM_NAME": "worker-01"}, namespace="workers", api=self.api)
        self.assertEqual(self.calls_for("POST"), [])
        self.assertEqual(self.calls_for("DELETE"), [])

    def test_template_deletion_with_explicit_generated_values(self):
        deleted = ocp.delete(
            "worker",
            {"VM_NAME": "worker-01", "PASSWORD": "original"},
            namespace="workers",
            api=self.api,
        )
        self.assertEqual(len(deleted), 2)
        self.assertNotIn("preconditions", self.calls_for("DELETE")[0]["body"])

    def test_server_metadata_removed_and_refs_do_not_contain_secrets(self):
        secret = manifest("Secret", "credentials", data={"password": "c2VjcmV0"})
        secret["metadata"].update(uid="old-uid", resourceVersion="123", managedFields=[])
        self.objects = [secret]
        refs = self.create()
        body = self.calls_for("PATCH")[0]["body"]
        self.assertNotIn("uid", body["metadata"])
        self.assertNotIn("resourceVersion", body["metadata"])
        self.assertNotIn("data", refs[0])

    def test_process_returns_objects_without_mutations(self):
        objects = ocp.process(
            "worker",
            {"PASSWORD": "explicit", "VM_NAME": False},
            namespace="workers",
            api=self.api,
        )
        self.assertEqual(objects, self.objects)
        self.assertEqual([c["method"] for c in self.calls], ["GET", "POST"])
        self.assertEqual(self.calls[1]["body"]["parameters"][0]["value"], "false")
        self.assertIn("/namespaces/workers/templates/", self.calls[0]["path"])

    def test_fresh_client_template_delete_without_create_or_inventory(self):
        fresh = self.new_api()
        deleted = ocp.delete(
            "worker",
            {"PASSWORD": "explicit"},
            namespace="workers",
            template_namespace="infra",
            api=fresh,
        )
        self.assertEqual(
            [ref["metadata"]["name"] for ref in deleted], ["worker-01", "worker-config"]
        )
        self.assertEqual([c["method"] for c in self.calls], ["GET", "POST", "DELETE", "DELETE"])
        self.assertIn("/namespaces/infra/templates/worker", self.calls[0]["path"])
        for call in self.calls_for("DELETE"):
            self.assertIn("/namespaces/workers/", call["path"])
            self.assertNotIn("preconditions", call["body"])

    def test_repeated_template_delete_treats_404_as_success(self):
        kwargs = dict(namespace="workers", template_namespace="infra", api=self.api)
        first = ocp.delete("worker", {"PASSWORD": "explicit"}, **kwargs)
        for name in ("worker-01", "worker-config"):
            self.errors[("DELETE", name)] = 404
        second = ocp.delete("worker", {"PASSWORD": "explicit"}, **kwargs)
        self.assertEqual(first, second)

    def test_secret_string_data_normalized_before_ssa_with_utf8_and_precedence(self):
        self.objects = [
            manifest(
                "Secret",
                "credentials",
                data={"keep": "a2VlcA==", "password": "b2xk"},
                stringData={"password": "sëcret\n", "empty": ""},
            )
        ]
        original = deepcopy(self.objects)
        refs = self.create()
        body = self.calls_for("PATCH")[0]["body"]
        self.assertNotIn("stringData", body)
        self.assertEqual(
            body["data"], {"keep": "a2VlcA==", "password": "c8OrY3JldAo=", "empty": ""}
        )
        self.assertEqual(self.objects, original)
        self.assertNotIn("data", refs[0])
        self.assertNotIn("stringData", refs[0])

    def test_bad_later_secret_fails_before_first_apply(self):
        self.objects.append(manifest("Secret", "bad", stringData={"password": 42}))
        with self.assertRaisesRegex(ValueError, "Secret stringData"):
            self.create()
        self.assertEqual(self.calls_for("PATCH"), [])

    def test_mutation_timeouts_preserve_original_error_and_partial_results(self):
        error = ReadTimeoutError(None, "/simulated", "timeout")
        self.errors[("PATCH", "worker-01")] = error
        with self.assertRaises(ocp.OperationError) as caught:
            self.create()
        self.assertEqual(len(caught.exception.resources), 1)
        self.assertIs(caught.exception.failures[0][1], error)
        self.assertEqual(len(self.calls_for("PATCH")), 2)
        self.errors[("DELETE", "worker-01")] = error
        with self.assertRaises(ocp.OperationError) as caught:
            ocp.delete("worker", {"PASSWORD": "explicit"}, namespace="workers", api=self.api)
        self.assertIs(caught.exception.failures[0][1], error)
        self.assertEqual(len(caught.exception.resources), 1)
        self.assertEqual(len(self.calls_for("DELETE")), 2)

    def test_custom_timeouts_reach_processing_and_mutations(self):
        kwargs = dict(namespace="workers", api=self.api, request_timeout=(2, 7))
        ocp.create("worker", {"PASSWORD": "explicit"}, **kwargs)
        ocp.delete("worker", {"PASSWORD": "explicit"}, **kwargs)
        self.assertTrue(all(c["timeout"] == (2, 7) for c in self.calls))

    def test_owned_clients_closed_after_success_and_failure(self):
        for operation in (ocp.process, ocp.create, ocp.delete, ocp.get):
            for fail in (False, True):
                with self.subTest(operation=operation.__name__, fail=fail):
                    api = self.new_api()
                    with patch("openshift_templates._client.connect", return_value=api) as connect:
                        with patch.object(api.client, "close") as close:
                            if fail:
                                self.errors[("GET", "worker")] = 403
                                with self.assertRaises(ApiException):
                                    operation(
                                        "worker", {"PASSWORD": "explicit"}, namespace="workers"
                                    )
                            else:
                                self.errors.clear()
                                operation("worker", {"PASSWORD": "explicit"}, namespace="workers")
                            close.assert_called_once_with()
                            connect.assert_called_once_with(request_timeout=(10, 60))

    def test_injected_client_remains_open_on_success_and_failure(self):
        with patch.object(self.api.client, "close") as close:
            self.create()
            self.errors[("PATCH", "worker-01")] = 409
            with self.assertRaises(ocp.OperationError):
                self.create()
            close.assert_not_called()

    def test_empty_template_has_empty_results(self):
        self.objects = []
        self.assertEqual(self.create(), [])
        self.assertEqual(
            ocp.delete("worker", {"PASSWORD": "explicit"}, namespace="workers", api=self.api), []
        )


@pytest.fixture
def harness():
    case = ModuleTests()
    case.setUp()
    try:
        yield case
    finally:
        case.doCleanups()


@pytest.mark.parametrize("operation", [ocp.process, ocp.create, ocp.delete, ocp.get])
@pytest.mark.parametrize("argument", ["namespace", "template_namespace"])
@pytest.mark.parametrize("value", ["", " ", "Bad", "a/b", "a" * 64, 42])
def test_invalid_namespaces_fail_before_connect(harness, operation, argument, value):
    kwargs = {"namespace": "workers", "template_namespace": "infra", argument: value}
    with patch("openshift_templates._client.connect") as connect:
        with pytest.raises(ValueError, match=argument):
            operation("worker", {"PASSWORD": "explicit"}, **kwargs)
        connect.assert_not_called()
    assert harness.calls == []


@pytest.mark.parametrize("operation", [ocp.process, ocp.create, ocp.delete, ocp.get])
@pytest.mark.parametrize(
    "timeout", [None, 0, -1, True, float("nan"), float("inf"), (1,), (1, 0), [1, 2]]
)
def test_invalid_timeout_before_connect(operation, timeout):
    with patch("openshift_templates._client.connect") as connect:
        with pytest.raises(ValueError, match="request_timeout"):
            operation("worker", namespace="workers", request_timeout=timeout)
        connect.assert_not_called()


@pytest.mark.parametrize("operation", [ocp.process, ocp.create, ocp.delete, ocp.get])
def test_unresolved_generators_rejected_for_every_operation(harness, operation):
    with pytest.raises(ValueError, match="Unresolved parameter generators: PASSWORD"):
        operation("worker", namespace="workers", api=harness.api)
    assert [c["method"] for c in harness.calls] == ["GET"]


@pytest.mark.parametrize("operation", [ocp.create, ocp.delete])
@pytest.mark.parametrize(
    "bad",
    [
        manifest("Namespace", "forbidden"),
        manifest("ClusterRole", "forbidden"),
        manifest("CustomResourceDefinition", "forbidden"),
        {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {"name": "bad", "namespace": "other"},
        },
        {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {"name": "fixed", "generateName": "bad-"},
        },
        {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {}},
        {"apiVersion": "v1", "metadata": {"name": "bad"}},
        {"kind": "ConfigMap", "metadata": {"name": "bad"}},
        {"apiVersion": "v1", "kind": "ConfigMap", "metadata": None},
        None,
    ],
)
def test_all_rendered_targets_validated_before_any_mutation(harness, operation, bad):
    harness.objects.append(bad)
    with pytest.raises(ValueError):
        operation("worker", {"PASSWORD": "explicit"}, namespace="workers", api=harness.api)
    assert harness.calls_for("PATCH") == []
    assert harness.calls_for("DELETE") == []


@pytest.mark.parametrize("status", [403, 409, 422, 504])
@pytest.mark.parametrize("operation,method", [(ocp.create, "PATCH"), (ocp.delete, "DELETE")])
def test_mutation_errors_preserve_status_and_successes(harness, status, operation, method):
    harness.errors[(method, "worker-01")] = status
    with pytest.raises(ocp.OperationError) as caught:
        operation("worker", {"PASSWORD": "explicit"}, namespace="workers", api=harness.api)
    assert len(caught.value.resources) == 1
    assert caught.value.failures[0][0]["metadata"]["name"] == "worker-01"
    assert caught.value.failures[0][1].status == status
    assert len(harness.calls_for(method)) == 2


@pytest.mark.parametrize("method,endpoint", [("GET", "worker"), ("POST", "processedtemplates")])
def test_processing_timeout_surfaces_without_resource_mutations(harness, method, endpoint):
    error = ReadTimeoutError(None, "/simulated", "timeout")
    harness.errors[(method, endpoint)] = error
    with pytest.raises(ReadTimeoutError) as caught:
        harness.create()
    assert caught.value is error
    assert harness.calls_for("PATCH") == []
    assert harness.calls_for("DELETE") == []


@pytest.mark.parametrize(
    "parameters", [{"VM_NAME": None}, {"VM_NAME": {}}, {"VM_NAME": float("nan")}, {3: "bad"}, []]
)
def test_bad_parameters_before_connect(parameters):
    with patch("openshift_templates._client.connect") as connect:
        with pytest.raises(ValueError):
            ocp.create("worker", parameters, namespace="workers")
        connect.assert_not_called()


@pytest.mark.parametrize("value", ["explicit", ""])
def test_explicit_generator_override_even_empty_is_deterministic(harness, value):
    ocp.create("worker", {"PASSWORD": value}, namespace="workers", api=harness.api)
    assert harness.calls_for("POST")[0]["body"]["parameters"][2] == {
        "name": "PASSWORD",
        "value": value,
    }


def test_generator_with_stored_nonempty_value_preserved(harness):
    harness.template["parameters"][2]["value"] = "stored"
    ocp.create("worker", namespace="workers", api=harness.api)
    assert harness.calls_for("POST")[0]["body"]["parameters"][2]["value"] == "stored"


def test_delete_does_not_take_uid_from_rendered_template(harness):
    harness.objects[0]["metadata"]["uid"] = "stale-template-uid"
    ocp.delete("worker", {"PASSWORD": "explicit"}, namespace="workers", api=harness.api)
    assert all("preconditions" not in c["body"] for c in harness.calls_for("DELETE"))


def test_delete_references_without_uids_needs_no_template_or_create_result(harness):
    refs = [manifest("ConfigMap", "worker-config"), manifest("VirtualMachine", "worker-01")]
    original = deepcopy(refs)
    harness.template = None

    deleted = ocp.delete(namespace="workers", resources=refs, api=harness.new_api())

    assert [ref["metadata"]["name"] for ref in deleted] == ["worker-01", "worker-config"]
    assert all(ref["metadata"]["namespace"] == "workers" for ref in deleted)
    assert all("uid" not in ref["metadata"] for ref in deleted)
    assert [call["method"] for call in harness.calls] == ["DELETE", "DELETE"]
    assert all("preconditions" not in call["body"] for call in harness.calls)
    assert refs == original


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"template_name": "worker", "resources": []},
        {"parameters": {}, "resources": []},
        {"template_namespace": "infra", "resources": []},
    ],
)
def test_invalid_delete_modes_fail_before_connect(kwargs):
    with patch("openshift_templates._client.connect") as connect:
        with pytest.raises(ValueError):
            ocp.delete(namespace="workers", **kwargs)
        connect.assert_not_called()


@pytest.mark.parametrize("operation", [ocp.get, ocp.delete])
@pytest.mark.parametrize(
    "resources",
    [
        {},
        (),
        "invalid",
        [None],
        ["invalid"],
        [42],
        [False],
        [[]],
        [()],
        [manifest("ConfigMap", "valid"), None],
    ],
)
def test_invalid_resources_fail_before_connect(operation, resources):
    with patch("openshift_templates._client.connect") as connect:
        with pytest.raises(ValueError, match="resources must be a list of manifest dictionaries"):
            operation(namespace="workers", resources=resources)
        connect.assert_not_called()


@pytest.mark.parametrize("operation", [ocp.get, ocp.delete])
def test_empty_resource_list_is_valid(harness, operation):
    assert operation(namespace="workers", resources=[], api=harness.api) == []
    assert harness.calls == []


def test_apply_stops_at_first_error_without_rollback(harness):
    harness.errors[("PATCH", "worker-config")] = 409
    with pytest.raises(ocp.OperationError) as caught:
        harness.create()
    assert caught.value.resources == []
    assert len(harness.calls_for("PATCH")) == 1
    assert harness.calls_for("DELETE") == []


@pytest.mark.parametrize("in_cluster", [False, True])
@pytest.mark.parametrize("verify_ssl", [None, True, False])
def test_connect_auth_tls_discovery_timeout_and_caller_cleanup(tmp_path, in_cluster, verify_ssl):
    calls = []

    def http(transport, path, method, *args, **kwargs):
        calls.append((path, kwargs["_request_timeout"]))
        if path == "/version":
            result = {"gitVersion": "v1.31.0"}
        elif path == "/apis":
            result = {"apiVersion": "v1", "kind": "APIGroupList", "groups": []}
        elif path.strip("/") == "api/v1":
            result = {
                "apiVersion": "v1",
                "kind": "APIResourceList",
                "resources": [
                    {
                        "name": "configmaps",
                        "kind": "ConfigMap",
                        "namespaced": True,
                        "verbs": ["get"],
                    }
                ],
            }
        else:
            result = manifest("ConfigMap", "example")
        return SimpleNamespace(data=json.dumps(result).encode())

    with (
        patch("kubernetes.config.load_kube_config") as local,
        patch("kubernetes.config.load_incluster_config") as pod,
        patch("kubernetes.dynamic.discovery.tempfile.gettempdir", return_value=str(tmp_path)),
        patch.object(client.ApiClient, "call_api", autospec=True, side_effect=http),
    ):
        kwargs = (
            {"in_cluster": True} if in_cluster else {"kubeconfig": "/fake/config", "context": "dev"}
        )
        if verify_ssl is not None:
            kwargs["verify_ssl"] = verify_ssl
        api = ocp.connect(**kwargs, request_timeout=(3, 9))
        try:
            expected_ssl = True if verify_ssl is None else verify_ssl
            assert api.configuration.verify_ssl is expected_ssl
            assert api.client.rest_client.pool_manager.connection_pool_kw["cert_reqs"] == (
                ssl.CERT_REQUIRED if expected_ssl else ssl.CERT_NONE
            )
            assert api.configuration.debug is False
            assert api.configuration.retries == 0
            resource = api.resources.get(api_version="v1", kind="ConfigMap")
            resource.get(name="example", namespace="workers")
            assert all(timeout == (3, 9) for _, timeout in calls)
            resource.get(name="example", namespace="workers", _request_timeout=4)
            assert calls[-1][1] == 4
            if in_cluster:
                pod.assert_called_once_with(client_configuration=api.configuration)
                local.assert_not_called()
            else:
                local.assert_called_once_with(
                    config_file="/fake/config",
                    context="dev",
                    client_configuration=api.configuration,
                    persist_config=False,
                )
                pod.assert_not_called()
        finally:
            api.client.close()


@pytest.mark.parametrize(
    "resource_names",
    [("templates", "processedtemplates"), ("processedtemplates", "templates")],
)
def test_process_discovers_distinct_template_endpoints(harness, tmp_path, resource_names):
    def http(path, method, *args, **kwargs):
        if path == "/version":
            result = {"gitVersion": "v1.31.0"}
        elif path == "/apis":
            version = {"groupVersion": "template.openshift.io/v1", "version": "v1"}
            result = {
                "kind": "APIGroupList",
                "groups": [
                    {
                        "name": "template.openshift.io",
                        "versions": [version],
                        "preferredVersion": version,
                    }
                ],
            }
        elif path.strip("/") == "apis/template.openshift.io/v1":
            result = {
                "kind": "APIResourceList",
                "groupVersion": "template.openshift.io/v1",
                "resources": [
                    {
                        "name": name,
                        "kind": "Template",
                        "namespaced": True,
                        "verbs": ["get"] if name == "templates" else ["create"],
                    }
                    for name in resource_names
                ],
            }
        else:
            return harness.http(path, method, *args, **kwargs)
        return SimpleNamespace(data=json.dumps(result).encode())

    with patch("kubernetes.dynamic.discovery.tempfile.gettempdir", return_value=str(tmp_path)):
        with client.ApiClient() as transport:
            transport.call_api = Mock(side_effect=http)
            api = DynamicClient(transport)
            result = ocp.process(
                "worker",
                {"PASSWORD": "explicit"},
                namespace="workers",
                template_namespace="infra",
                api=api,
            )

    assert result == harness.objects
    assert [(call["method"], call["path"]) for call in harness.calls] == [
        ("GET", "/apis/template.openshift.io/v1/namespaces/infra/templates/worker"),
        ("POST", "/apis/template.openshift.io/v1/namespaces/workers/processedtemplates"),
    ]


def test_connect_closes_transport_when_discovery_fails():
    with (
        patch("kubernetes.config.load_kube_config"),
        patch("kubernetes.client.ApiClient") as transport,
        patch(
            "openshift_templates._client.DynamicClient",
            side_effect=RuntimeError("discovery failed"),
        ),
    ):
        with pytest.raises(RuntimeError, match="discovery failed"):
            ocp.connect()
        transport.return_value.close.assert_called_once_with()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"in_cluster": True, "context": "dev"},
        {"in_cluster": True, "kubeconfig": "/fake"},
        {"request_timeout": 0},
    ],
)
def test_bad_connect_inputs_do_not_load_auth(kwargs):
    with (
        patch("kubernetes.config.load_kube_config") as local,
        patch("kubernetes.config.load_incluster_config") as pod,
    ):
        with pytest.raises(ValueError):
            ocp.connect(**kwargs)
        local.assert_not_called()
        pod.assert_not_called()


def test_import_does_not_load_credentials_connect_or_write_files():
    import subprocess
    import sys

    script = """
from unittest.mock import patch
from kubernetes import client, config
with patch.object(config, "load_kube_config") as local, \\
     patch.object(config, "load_incluster_config") as pod, \\
     patch.object(client, "ApiClient") as transport, \\
     patch("builtins.open") as files:
    import openshift_templates
    local.assert_not_called()
    pod.assert_not_called()
    transport.assert_not_called()
    files.assert_not_called()
"""
    subprocess.run([sys.executable, "-c", script], check=True, timeout=20)


@pytest.mark.parametrize("operation", [ocp.create, ocp.delete, ocp.get])
def test_unavailable_resource_api_prevents_all_mutations(harness, operation):
    harness.objects.append(manifest("UninstalledKind", "missing"))
    with pytest.raises(ResourceNotFoundError):
        operation("worker", {"PASSWORD": "explicit"}, namespace="workers", api=harness.api)
    assert harness.calls_for("PATCH") == []
    assert harness.calls_for("DELETE") == []


@pytest.mark.parametrize("name", [".", "..", "../other", "bad%2fname"])
def test_unsafe_rendered_names_rejected_before_delete(harness, name):
    harness.objects[-1]["metadata"]["name"] = name
    with pytest.raises(ValueError, match="safe API path"):
        ocp.delete("worker", {"PASSWORD": "explicit"}, namespace="workers", api=harness.api)
    assert harness.calls_for("DELETE") == []


@pytest.mark.parametrize("name", [None, "", "../other", "a..b"])
def test_bad_template_name_before_connect(name):
    with patch("openshift_templates._client.connect") as connect:
        with pytest.raises(ValueError, match="template_name"):
            ocp.create(name, namespace="workers")
        connect.assert_not_called()


@pytest.mark.parametrize("manager", ["", " ", None, "x" * 129])
def test_bad_field_manager_before_connect(manager):
    with patch("openshift_templates._client.connect") as connect:
        with pytest.raises(ValueError, match="field_manager"):
            ocp.create("worker", namespace="workers", field_manager=manager)
        connect.assert_not_called()


def test_delete_aggregates_all_failures_in_reverse_order(harness):
    harness.errors[("DELETE", "worker-01")] = 403
    harness.errors[("DELETE", "worker-config")] = 409
    with pytest.raises(ocp.OperationError) as caught:
        ocp.delete("worker", {"PASSWORD": "explicit"}, namespace="workers", api=harness.api)
    assert caught.value.resources == []
    assert [error.status for _, error in caught.value.failures] == [403, 409]
    assert caught.value.operation == "Delete"


def test_get_reads_live_status_with_fresh_client_and_current_values(harness):
    kwargs = dict(
        namespace="workers",
        template_namespace="infra",
        api=harness.new_api(),
        request_timeout=(2, 7),
    )
    result = ocp.get("worker", {"PASSWORD": "explicit"}, **kwargs)
    assert result == harness.live_objects
    assert result[1]["status"] == {"printableStatus": "Running"}
    assert [c["method"] for c in harness.calls] == ["GET", "POST", "GET", "GET"]
    assert (
        harness.calls[0]["path"]
        == "/apis/template.openshift.io/v1/namespaces/infra/templates/worker"
    )
    assert (
        harness.calls[-1]["path"]
        == "/apis/kubevirt.io/v1/namespaces/workers/virtualmachines/worker-01"
    )
    assert all(c["timeout"] == (2, 7) for c in harness.calls)
    harness.live_objects[1]["status"] = {"printableStatus": "Stopped"}
    assert ocp.get("worker", {"PASSWORD": "explicit"}, **kwargs)[1]["status"] == {
        "printableStatus": "Stopped"
    }


def test_get_references_skips_template_processing(harness):
    refs = harness.create()
    harness.calls.clear()
    harness.template = None
    result = ocp.get(namespace="workers", resources=refs, api=harness.api)
    assert result == harness.live_objects
    assert [c["method"] for c in harness.calls] == ["GET", "GET"]
    assert all("/templates/" not in c["path"] for c in harness.calls)
    assert "status" not in result[0]  # A ConfigMap has no workload status to invent.


@pytest.mark.parametrize("error", [404, 403, ReadTimeoutError(None, "/simulated", "timeout")])
def test_get_resource_errors_propagate_without_mutations(harness, error):
    harness.errors[("GET", "worker-01")] = error
    expected = ReadTimeoutError if isinstance(error, ReadTimeoutError) else ApiException
    with patch.object(harness.api.client, "close") as close:
        with pytest.raises(expected) as caught:
            ocp.get("worker", {"PASSWORD": "explicit"}, namespace="workers", api=harness.api)
        close.assert_not_called()
    if isinstance(error, int):
        assert caught.value.status == error
    else:
        assert caught.value is error
    assert harness.calls_for("PATCH") == []
    assert harness.calls_for("DELETE") == []


@pytest.mark.parametrize(
    "bad",
    [
        manifest("Namespace", "forbidden"),
        {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {"name": "bad", "namespace": "other"},
        },
    ],
)
def test_get_checks_all_targets_before_reading_any_resource(harness, bad):
    harness.objects.append(bad)
    with pytest.raises(ValueError):
        ocp.get("worker", {"PASSWORD": "explicit"}, namespace="workers", api=harness.api)
    assert [c["method"] for c in harness.calls] == ["GET", "POST"]


def test_get_resource_read_failure_closes_owned_client(harness):
    harness.errors[("GET", "worker-01")] = 404
    with patch("openshift_templates._client.connect", return_value=harness.api):
        with patch.object(harness.api.client, "close") as close:
            with pytest.raises(ApiException):
                ocp.get("worker", {"PASSWORD": "explicit"}, namespace="workers")
            close.assert_called_once_with()


def test_get_empty_template_returns_empty_list(harness):
    harness.objects = []
    assert ocp.get("worker", {"PASSWORD": "explicit"}, namespace="workers", api=harness.api) == []


@pytest.mark.parametrize(
    "operation,args,endpoint",
    [
        (ocp.list_templates, (), "templates"),
        (ocp.list_template_parameters, ("worker",), "templates/worker"),
    ],
)
def test_template_inspection_returns_stored_data_without_processing(
    harness, operation, args, endpoint
):
    harness.template["parameters"][0].update(
        required=True, displayName="Worker name", description="Stable resource name"
    )
    harness.template["parameters"].append({"name": "REQUIRED_WITHOUT_VALUE", "required": True})
    original = deepcopy(harness.template)
    with patch.object(harness.api.client, "close") as close:
        result = operation(*args, namespace="infra", api=harness.api, request_timeout=(2, 5))
        close.assert_not_called()
    expected = [original] if operation is ocp.list_templates else original["parameters"]
    assert result == expected
    assert len(harness.calls) == 1
    assert harness.calls[0]["method"] == "GET"
    assert harness.calls[0]["path"] == (
        f"/apis/template.openshift.io/v1/namespaces/infra/{endpoint}"
    )
    assert harness.calls[0]["timeout"] == (2, 5)
    result[0]["value"] = "changed locally"
    assert harness.template == original


def test_list_templates_collects_every_page_in_one_namespace(harness):
    second = deepcopy(harness.template)
    second["metadata"]["name"] = "second"
    harness.template_pages = {
        None: {"items": [harness.template], "metadata": {"continue": "next-page"}},
        "next-page": {"items": [second], "metadata": {}},
    }
    assert ocp.list_templates(namespace="infra", api=harness.api) == [harness.template, second]
    assert [call["query"] for call in harness.calls] == [{}, {"continue": "next-page"}]
    assert all(
        call["method"] == "GET"
        and call["path"] == "/apis/template.openshift.io/v1/namespaces/infra/templates"
        for call in harness.calls
    )


def test_list_templates_empty_namespace(harness):
    harness.template_pages = {None: {"items": []}}
    assert ocp.list_templates(namespace="infra", api=harness.api) == []


@pytest.mark.parametrize("parameters", [None, []])
def test_list_template_parameters_without_definitions(harness, parameters):
    if parameters is None:
        harness.template.pop("parameters")
    else:
        harness.template["parameters"] = parameters
    assert ocp.list_template_parameters("worker", namespace="infra", api=harness.api) == []


@pytest.mark.parametrize(
    "operation,args,endpoint",
    [
        (ocp.list_templates, (), "templates"),
        (ocp.list_template_parameters, ("worker",), "worker"),
    ],
)
@pytest.mark.parametrize("error", [403, 404, ReadTimeoutError(None, "/simulated", "timeout")])
def test_template_inspection_errors_propagate_and_close_owned_clients(
    harness, operation, args, endpoint, error
):
    harness.errors[("GET", endpoint)] = error
    expected = ReadTimeoutError if isinstance(error, ReadTimeoutError) else ApiException
    with patch("openshift_templates._client.connect", return_value=harness.api):
        with patch.object(harness.api.client, "close") as close:
            with pytest.raises(expected) as caught:
                operation(*args, namespace="infra")
            close.assert_called_once_with()
    if isinstance(error, int):
        assert caught.value.status == error
    else:
        assert caught.value is error
    assert [call["method"] for call in harness.calls] == ["GET"]


def test_list_templates_page_failure_propagates_without_partial_result(harness):
    harness.template_pages = {
        None: {"items": [harness.template], "metadata": {"continue": "next-page"}},
    }
    original_http = harness.http

    def http(path, method, path_params, query, headers, **kwargs):
        if dict(query).get("continue"):
            raise ApiException(status=410, reason="expired continuation")
        return original_http(path, method, path_params, query, headers, **kwargs)

    harness.api.client.call_api.side_effect = http
    with pytest.raises(ApiException) as caught:
        ocp.list_templates(namespace="infra", api=harness.api)
    assert caught.value.status == 410


@pytest.mark.parametrize(
    "operation,args",
    [(ocp.list_templates, ()), (ocp.list_template_parameters, ("worker",))],
)
def test_template_inspection_closes_owned_client_on_success(harness, operation, args):
    with patch("openshift_templates._client.connect", return_value=harness.api):
        with patch.object(harness.api.client, "close") as close:
            operation(*args, namespace="infra")
            close.assert_called_once_with()


@pytest.mark.parametrize(
    "operation,args",
    [(ocp.list_templates, ()), (ocp.list_template_parameters, ("worker",))],
)
@pytest.mark.parametrize("value", [None, "", "Bad", "a/b", "a" * 64])
def test_template_inspection_invalid_namespace_before_connect(operation, args, value):
    with patch("openshift_templates._client.connect") as connect:
        with pytest.raises(ValueError, match="namespace"):
            operation(*args, namespace=value)
        connect.assert_not_called()


@pytest.mark.parametrize(
    "operation,args",
    [(ocp.list_templates, ()), (ocp.list_template_parameters, ("worker",))],
)
@pytest.mark.parametrize("timeout", [None, 0, (1, 0)])
def test_template_inspection_invalid_timeout_before_connect(operation, args, timeout):
    with patch("openshift_templates._client.connect") as connect:
        with pytest.raises(ValueError, match="request_timeout"):
            operation(*args, namespace="infra", request_timeout=timeout)
        connect.assert_not_called()


@pytest.mark.parametrize("name", [None, "", "../other"])
def test_list_template_parameters_invalid_name_before_connect(name):
    with patch("openshift_templates._client.connect") as connect:
        with pytest.raises(ValueError, match="template_name"):
            ocp.list_template_parameters(name, namespace="infra")
        connect.assert_not_called()


@pytest.mark.parametrize("operation", [ocp.process, ocp.create, ocp.get, ocp.delete])
@pytest.mark.parametrize("default", [None, "", "nonempty-default"])
@pytest.mark.parametrize("override", [None, ""])
def test_missing_required_parameters_fail_before_processing(harness, operation, default, override):
    definition = {"name": "VM_NAME", "required": True, "description": "private-description"}
    if default is not None:
        definition["value"] = default
    harness.template["parameters"][0] = definition
    harness.template["parameters"].append({"name": "ANOTHER", "required": True})
    parameters = {"PASSWORD": "private-password"}
    if override is not None:
        parameters["VM_NAME"] = override
    original = deepcopy(harness.template)
    with pytest.raises(ValueError) as caught:
        operation(
            "worker", parameters, namespace="workers", template_namespace="infra", api=harness.api
        )
    missing = (
        "ANOTHER" if default == "nonempty-default" and override is None else "ANOTHER, VM_NAME"
    )
    assert str(caught.value) == (
        f"Missing required template parameters for infra/worker: {missing}. "
        "Supply nonempty values in parameters or define nonempty Template defaults."
    )
    assert harness.template == original
    assert [call["method"] for call in harness.calls] == ["GET"]


@pytest.mark.parametrize("value", ["provided", False, 0, 1.5, " "])
def test_required_parameters_accept_nonempty_scalar_overrides(harness, value):
    harness.template["parameters"][0] = {"name": "VM_NAME", "required": True}
    ocp.process(
        "worker", {"VM_NAME": value, "PASSWORD": "explicit"}, namespace="workers", api=harness.api
    )
    expected = str(value).lower() if isinstance(value, bool) else str(value)
    assert harness.calls_for("POST")[0]["body"]["parameters"][0]["value"] == expected


def test_required_defaults_preserved_and_optional_empty_values_allowed(harness):
    harness.template["parameters"][0]["required"] = True
    harness.template["parameters"].extend(
        [{"name": "OPTIONAL_EMPTY", "value": "", "required": False}, {"name": "OPTIONAL_MISSING"}]
    )
    ocp.process("worker", {"PASSWORD": "explicit"}, namespace="workers", api=harness.api)
    assert harness.calls_for("POST")[0]["body"]["parameters"] == [
        *harness.template["parameters"][:2],
        {"name": "PASSWORD", "value": "explicit"},
        *harness.template["parameters"][3:],
    ]


@pytest.mark.parametrize("operation", [ocp.process, ocp.create, ocp.get, ocp.delete])
@pytest.mark.parametrize("override", [None, "", "explicit"])
def test_required_generator_still_needs_nonempty_explicit_value(harness, operation, override):
    harness.template["parameters"][2]["required"] = True
    parameters = {} if override is None else {"PASSWORD": override}
    if override == "explicit":
        operation("worker", parameters, namespace="workers", api=harness.api)
        assert harness.calls_for("POST")[0]["body"]["parameters"][2] == {
            "name": "PASSWORD",
            "required": True,
            "value": "explicit",
        }
    else:
        expected = (
            "Unresolved parameter generators"
            if override is None
            else "Missing required template parameters"
        )
        with pytest.raises(ValueError, match=expected):
            operation("worker", parameters, namespace="workers", api=harness.api)
        assert [call["method"] for call in harness.calls] == ["GET"]
