# Publishing releases

[`.github/workflows/publish.yml`](../.github/workflows/publish.yml) publishes a
wheel and source distribution to PyPI when a `v*` tag is pushed to GitHub. It
uses [PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/), so no
PyPI API token or GitHub repository secret is required.

## One-time configuration

1. Push the workflow and project code to
   `Ravid-Amar/openshift-template-client`. In the repository's **Settings →
   Actions → General**, enable Actions and allow the `actions/*` and
   `pypa/gh-action-pypi-publish` actions used by this workflow.
2. Under **Settings → Environments**, create an environment named **`pypi`**.
   If you restrict deployment branches/tags, allow tags matching **`v*`**.
   Required reviewers are optional: enabling them pauses publication until a
   reviewer approves the environment deployment.
3. Sign in to PyPI. For a new project, open
   [account publishing settings](https://pypi.org/manage/account/publishing/)
   and add a **pending publisher** using the GitHub provider. Fill in the values
   below. If you already own the PyPI project, add the publisher under that
   project's **Manage → Publishing** instead.

| PyPI field | Value |
| --- | --- |
| PyPI project name (new project only) | `openshift-template-client` |
| Owner | `Ravid-Amar` |
| Repository name | `openshift-template-client` |
| Workflow filename | `publish.yml` |
| Environment name | `pypi` |

The filename is `publish.yml`, without `.github/workflows/`. All values must
match the repository and workflow. A pending publisher creates the project on
its first successful upload; it does not reserve the project name. If the name
belongs to someone else, obtain access or choose another package name and update
the package metadata, workflow URL, and documentation consistently. See PyPI's
[new-project publisher guide](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/)
and [existing-project guide](https://docs.pypi.org/trusted-publishers/adding-a-publisher/).

The workflow already grants `id-token: write` only to the publish job. The test
and build jobs have read-only repository access. The publish job downloads the
built distributions and uploads them without checking out or executing package
code. This follows the
[PyPA publishing workflow guide](https://packaging.python.org/en/latest/guides/publishing-package-distribution-releases-using-github-actions-ci-cd-workflows/).

## Release a version

The package version is maintained explicitly in `pyproject.toml`. The workflow
does not derive it from tags or change it automatically. The current version is
`0.1.2`, which includes Template discovery, parameter inspection, required-parameter
validation, and template-name listing. Run the checks:

```bash
python -m pytest
python -m ruff check .
python -m ruff format --check .
python -m build
```

Commit the workflow, documentation, and version changes, then push the commit
and its matching release tag:

```bash
git push origin main
git tag -a v0.1.2 -m "Release 0.1.2"
git push origin v0.1.2
```

Commit and push the workflow before creating the release tag, and tag the commit
containing the intended code and version. Local tag creation alone does not
trigger GitHub Actions; the tag must reach GitHub. Creating a new `v*` tag in
GitHub's interface also triggers the workflow. A GitHub Release is not required.

For each later release, update the version and tag together, for example
`0.1.3` and `v0.1.3`. A mismatch such as `v0.2.0` with package version `0.1.2`
fails before publication. Development/prerelease versions can also be published
with matching tags, such as `0.2.0rc1` and `v0.2.0rc1`; installers may need
`--pre` to select them.

The workflow runs offline tests and Ruff checks on Python 3.9 and 3.12, builds
the distributions on Python 3.12, validates metadata/README with `twine check
--strict`, and verifies installation/import of the wheel in a clean environment
before publication. Follow the **Publish to PyPI** run in the repository's
**Actions** tab. Once it succeeds:

```bash
python -m pip install 'openshift-template-client==0.1.2'
```

PyPI does not permit replacing a previously uploaded distribution filename. If
a release is already published, make a new version and tag for changed code.
An unsuccessful run that uploaded nothing can be rerun after correcting the
external configuration. Check the workflow logs and PyPI files first if an
upload partially completed. See
[PyPI upload troubleshooting](https://docs.pypi.org/trusted-publishers/troubleshooting/).

## Installation and authentication

See [README installation](../README.md#install) for PyPI, GitHub, and local-clone
installation commands. Git authentication is needed to push commits/tags; Git's
`user.name` and `user.email` only identify commit authors. GitHub authentication
for your terminal is separate from the workflow's PyPI Trusted Publishing setup.
