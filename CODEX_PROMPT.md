Upgrade this starter into a minimal, well-documented, stateless Python package
for OpenShift templates and the resources produced from them.

Read AGENTS.md, docs/requirements.md, docs/api-notes.md, README.md, the current
implementation, and tests before changing code.

Use $python-design-patterns to keep the design simple. Use $modern-python
selectively for development tooling; the project's Python 3.9 requirement,
pip/venv support, and minimal dependencies override generic tooling preferences.
If a skill is unavailable, follow the project instructions and continue.

The package is called openshift-template-client and imports as
openshift_templates. Its consumer is my separate mini scheduler.

Implement the requirements, rather than stopping after a proposal:

1. Preserve a small synchronous API: connect(), process(), create(), delete().
2. Fetch existing Templates from OpenShift, override supplied parameters, and
   process them using the server's processedtemplates endpoint.
3. Apply/delete arbitrary supported namespaced resource kinds through the
   Kubernetes Python client, including KubeVirt VirtualMachine.
4. Make deletion using template name, namespaces, and parameters the primary
   workflow. It must work with a fresh client after restart and without saved
   inventory or a previous create result.
5. Require deterministic replay. Reject unresolved random parameter generators
   for both create and template-based delete in this first version. Reject
   generateName and document template revision/resource identity assumptions.
6. Improve early validation, configurable request timeouts, type hints,
   docstrings, partial-failure reporting, and client lifecycle handling without
   adding unnecessary abstractions or runtime dependencies.
7. Keep operations scoped to the requested namespace; never require cluster
   admin privileges, new CRDs, or cluster-scoped resource creation.
8. Review Secret stringData with server-side apply. Add a small normalization
   only if needed, using the standard library, with a meaningful test.
9. Add focused offline tests for the public contract and important failure paths.
10. Update README with working local kubeconfig and in-cluster create/delete
    examples, required permissions, stateless assumptions, and error behavior.

Use the existing package structure and keep kubernetes as the only direct
runtime dependency. Keep scheduling logic outside the library. Do not build a
CLI, web service, controller, inventory store, or framework.

Briefly state your intended changes, then implement and verify them. Run:

    python -m pytest
    python -m ruff check .
    python -m ruff format --check .
    python -m build

Also verify that the built wheel imports in a clean environment. Do not use real
cluster credentials or mutate a real cluster for the offline checks. Do not
publish or push the package as part of this task.

Finish with the resulting public API, usage examples, test/build results, and
any checks that still need a real OpenShift cluster. Make reasonable small
implementation decisions yourself; ask only if a missing fact changes the
required behavior.
