# OpenShift API notes

This is a compact implementation reference. Check the official documentation
for the deployed cluster version before changing the request contract.

## Template processing

1. Read the source Template:

   `GET /apis/template.openshift.io/v1/namespaces/{template_namespace}/templates/{name}`

2. Work on a copy. Override matching entries in `parameters` while retaining
   unspecified defaults. Explicit values should disable that parameter's
   `generate` and `from` fields. Reject unknown names.

3. Submit the Template to the target namespace:

   `POST /apis/template.openshift.io/v1/namespaces/{namespace}/processedtemplates`

4. The response is a processed Template. Its rendered manifests are in
   `objects`, not `items`. Processing alone does not create those objects.

The API resource plural is `processedtemplates`; the submitted/returned kind
is still `Template`. Discover with `kind="Template"` and the resource name because
both `templates` and `processedtemplates` use that kind.

## Resource operations

Use `kubernetes.dynamic.DynamicClient` to discover resources by `apiVersion` and
`kind`. This supports built-in resources and installed APIs such as KubeVirt
VirtualMachine without adding their generated client models.

Server-side apply uses PATCH with `application/apply-patch+yaml`, a field manager,
and an explicit name. JSON bodies are valid for this content type. Preserve
conflicts by default. It creates or updates; it does not prune old resources.

Read each rendered identity with a namespaced GET to its main resource endpoint.
`get()` returns the full server response, including `status` when that kind has one.
It needs `get` on the resource itself, not `get` on a status subresource. Read
errors, including 404, propagate; no watch, list, or readiness logic is added.

Delete each rendered identity in reverse order. Namespaced discovery and the
requested namespace must agree with the manifest. A 404 from an individual
delete means already absent; other failures must remain visible.

Template-based deletion reprocesses identities and issues DELETE by name and
namespace. It does not GET live resource UIDs and needs no saved create result.
The optional `resources=refs` mode skips Template processing and permits
`template_name=None`; omit other template inputs too. A supplied
`metadata.uid` becomes `DeleteOptions.preconditions.uid`. The API rejects a UID
mismatch with 409, protecting a replacement object with the same name. Without
a supplied UID, deletion remains name-based. Ignore rendered Template UIDs as
preconditions: they may be stale exported metadata.

`create()` returns UIDs from server-side apply responses; the package does not
persist them. A later `get()` returns the current UID and cannot reconstruct an
original object's UID after replacement. Accepted background deletion does not
mean that finalizers have completed or that the object has disappeared.

For deterministic replay, reject unresolved parameter generators and
`metadata.generateName` in the first version. Do not reconstruct resources by
performing naive string replacement on YAML/JSON.

## Namespace permissions

- Template source namespace: `get` on `templates` in `template.openshift.io`.
- Target namespace: `create` on `processedtemplates` in `template.openshift.io`.
- Target resource kinds: `create` and `patch` for server-side apply; `delete`
  for deletion; `get` for inspecting deployed resources. No `list` or `watch` is needed.
- Ordinary API discovery access is also needed. This is not permission to
  mutate cluster-scoped resources.

## Sources

- [OCP 4.17 Template API](https://docs.redhat.com/en/documentation/openshift_container_platform/4.17/html/template_apis/template-template-openshift-io-v1)
- [Kubernetes Python client](https://github.com/kubernetes-client/python)
- [Kubernetes server-side apply](https://kubernetes.io/docs/reference/using-api/server-side-apply/)
- [Secret stringData and server-side apply](https://kubernetes.io/docs/concepts/configuration/secret/#working-with-secrets)

For air-gapped work, retain the relevant official documentation locally and
record the actual OCP and client versions used for integration testing.
