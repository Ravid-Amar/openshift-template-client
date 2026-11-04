# OpenShift API notes

This is a compact implementation reference based on OpenShift Container
Platform 4.20. Its Template API is `template.openshift.io/v1`; `4.20` is the
platform release, not a value for `apiVersion` or an API URL segment. The GET
and processing POST endpoints below match the 4.20 reference. Check the official
documentation for the deployed cluster version before changing the contract;
this documentation review does not replace live-cluster validation.

## Template discovery and parameter inspection

`list_templates(namespace="infra", api=api)` lists all stored Templates in one
source namespace:

`GET /apis/template.openshift.io/v1/namespaces/{namespace}/templates`

It returns the `items` from the TemplateList as a list of full dictionaries,
following continuation tokens if present. An empty namespace returns `[]`.
It never uses the cluster-wide list endpoint or processes any Template. Errors
on any page propagate without returning a partial list or automatically retrying.

`list_template_parameters("worker", namespace="infra", api=api)` reads the named
Template using the source GET endpoint below and returns its `parameters` list
in stored order, or `[]` if absent. There is no separate parameter endpoint.
Definitions retain their supplied `name`, `displayName`, `description`, `required`,
`value` (the default), `generate`, and `from` fields; omitted fields remain omitted.
Inspection does not reject missing required values or unresolved generators.

Both functions require a source `namespace`, with no target namespace argument,
and accept the usual injected `api` and per-request `request_timeout`. They validate
arguments before authentication, close clients they open, and leave injected
clients open. API errors (including 403/404) and transport timeouts propagate.
Full Templates and parameter defaults may contain secrets; avoid logging them.

## Template processing

1. Read the source Template:

   `GET /apis/template.openshift.io/v1/namespaces/{template_namespace}/templates/{name}`

2. Work on a copy. Override matching entries in `parameters` while retaining
   unspecified defaults. Explicit values should disable that parameter's
   `generate` and `from` fields. Reject unknown names.

   Before the processing POST, reject unresolved generators for deterministic
   replay. Then check all parameters marked `required: true`: each must have a
   nonempty value after overrides. A nonempty Template default satisfies the
   requirement; an absent value or an explicit empty override does not. Boolean
   `False` and numeric `0` overrides become the nonempty strings `false` and `0`.
   Optional parameters may remain empty. Raise `ValueError` listing all missing
   required names, the source Template identity, and how to supply them, without
   including parameter values or descriptions. This applies to `process()`,
   `create()`, and template-based `get()`/`delete()` before any target operations.
   A required generated parameter still needs a nonempty fixed default or explicit
   override. OpenShift remains responsible for substitution and schema validation.

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

- Template source namespace: `get` on `templates` in `template.openshift.io`
  for processing and parameter inspection; `list` for `list_templates()`.
- Target namespace: `create` on `processedtemplates` in `template.openshift.io`.
- Target resource kinds: `create` and `patch` for server-side apply; `delete`
  for deletion; `get` for inspecting deployed resources. No `list` or `watch` is needed.
- Ordinary API discovery access is also needed. This is not permission to
  mutate cluster-scoped resources.

## Sources

- [OCP 4.20 Template API](https://docs.redhat.com/en/documentation/openshift_container_platform/4.20/html/template_apis/template-template-openshift-io-v1)
- [Kubernetes Python client](https://github.com/kubernetes-client/python)
- [Kubernetes server-side apply](https://kubernetes.io/docs/reference/using-api/server-side-apply/)
- [Secret stringData and server-side apply](https://kubernetes.io/docs/concepts/configuration/secret/#working-with-secrets)

For air-gapped work, retain the relevant official documentation locally and
record the actual OCP and client versions used for integration testing.
