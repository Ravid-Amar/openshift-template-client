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
is still `Template`. Discover by resource name rather than only by kind because
both `templates` and `processedtemplates` use that kind.

## Resource operations

Use `kubernetes.dynamic.DynamicClient` to discover resources by `apiVersion` and
`kind`. This supports built-in resources and installed APIs such as KubeVirt
VirtualMachine without adding their generated client models.

Server-side apply uses PATCH with `application/apply-patch+yaml`, a field manager,
and an explicit name. JSON bodies are valid for this content type. Preserve
conflicts by default. It creates or updates; it does not prune old resources.

Delete each rendered identity in reverse order. Namespaced discovery and the
requested namespace must agree with the manifest. A 404 from an individual
delete means already absent; other failures must remain visible.

For deterministic replay, reject unresolved parameter generators and
`metadata.generateName` in the first version. Do not reconstruct resources by
performing naive string replacement on YAML/JSON.

## Namespace permissions

- Template source namespace: `get` on `templates` in `template.openshift.io`.
- Target namespace: `create` on `processedtemplates` in `template.openshift.io`.
- Target resource kinds: `create` and `patch` for server-side apply; `delete`
  for deletion. Add `get`/`list` only if implementation actually needs them.
- Ordinary API discovery access is also needed. This is not permission to
  mutate cluster-scoped resources.

## Sources

- [OCP 4.17 Template API](https://docs.redhat.com/en/documentation/openshift_container_platform/4.17/html/template_apis/template-template-openshift-io-v1)
- [Kubernetes Python client](https://github.com/kubernetes-client/python)
- [Kubernetes server-side apply](https://kubernetes.io/docs/reference/using-api/server-side-apply/)
- [Secret stringData and server-side apply](https://kubernetes.io/docs/concepts/configuration/secret/#working-with-secrets)

For air-gapped work, retain the relevant official documentation locally and
record the actual OCP and client versions used for integration testing.
