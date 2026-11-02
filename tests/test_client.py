"""Exercise the real Kubernetes dynamic client's HTTP construction offline."""

import json
import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

from kubernetes import client
from kubernetes.client.exceptions import ApiException
from kubernetes.dynamic import DynamicClient
from kubernetes.dynamic.resource import Resource

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
            "secrets": "Secret",
        }
        if name is None:
            name = next(n for n, k in by_name.items() if k == kind)
        return Resource(
            prefix="apis" if group else "api",
            group=group,
            api_version=version,
            name=name,
            kind=by_name[name],
            namespaced=by_name[name] != "Namespace",
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
        self.calls = []
        self.errors = {}
        transport = client.ApiClient()
        self.addCleanup(transport.close)
        transport.call_api = Mock(side_effect=self.http)
        self.api = DynamicClient(transport, discoverer=FakeDiscovery)

    def http(self, path, method, path_params, query, headers, **kwargs):
        call = {
            "path": path,
            "method": method,
            "query": dict(query),
            "headers": deepcopy(headers),
            "body": deepcopy(kwargs.get("body")),
        }
        self.calls.append(call)
        error = self.errors.get((method, path.rsplit("/", 1)[-1]))
        if error:
            raise ApiException(status=error, reason="simulated API error")
        if method == "GET":
            result = deepcopy(self.template)
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
            params or {"VM_NAME": "worker-01"},
            namespace="workers",
            template_namespace="infra",
            api=self.api,
        )

    def test_fetch_process_and_actual_apply_http_contract(self):
        refs = self.create()
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
        with self.assertRaisesRegex(ValueError, "Deletion would regenerate parameters: PASSWORD"):
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
