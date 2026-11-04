# Start the project

This guide records the original starter setup and upgrade workflow. The upgrade
is now implemented; for current package usage, see [README.md](README.md). For an
existing checkout, create the development environment in step 2 and run the
checks in step 3; the extraction and initial upgrade steps are historical.

Use Bash on Linux, macOS, or WSL. Requirements: Python 3.9+, Git, and an already
installed/signed-in Codex CLI. No cluster login is needed for setup or tests.

## 1. Extract

From the directory containing the downloaded ZIP:

```bash
python3 -m zipfile -e openshift-template-client-starter.zip .
cd openshift-template-client
```

The ZIP has one top-level `openshift-template-client/` directory. Extract into a
new parent directory so it does not overwrite an existing project.

## 2. Initialize Git and the development environment

```bash
git init -b main
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

Use your organization's configured package index or `PIP_INDEX_URL` if required.
No special uv installation is required.

## 3. Verify the starter

```bash
python -m pytest
python -m ruff check .
python -m ruff format --check .
python -m build
```

These are offline application tests: they do not contact OpenShift. Dependency
installation and isolated builds need access to the configured package index
unless dependencies have been prepared locally.

## 4. Save the starting point

```bash
git add .
git commit -m "Initialize OpenShift template client starter"
```

Git needs your usual user.name and user.email configured. Use your own values
if Git prompts for them.

## 5. Install the skills

Run `codex` from this directory, then enter the two installer prompts in
[SKILLS.md](SKILLS.md). Use `/skills` to verify them. Exit that session when done.

## 6. Start the upgrade

With the virtual environment active, run:

```bash
codex "$(cat CODEX_PROMPT.md)"
```

Alternatively, open this folder in the Codex IDE extension and paste the contents
of CODEX_PROMPT.md into a new task.

AGENTS.md is discovered automatically. CODEX_PROMPT.md is the concrete initial
task, and docs/requirements.md defines the expected behavior.

## 7. Review and commit the result

```bash
git diff
python -m pytest
python -m ruff check .
python -m ruff format --check .
python -m build
git add .
git commit -m "Refine stateless OpenShift template client"
```

Create an empty repository named `openshift-template-client` on your Git host.
Use the remote commands shown by that host to connect and push this local repo.
The starter does not assume a GitLab/GitHub URL or publish any package.

## Fully offline package installation

On a connected machine with the same target OS/architecture and Python version,
prepare wheels from this extracted project:

```bash
python3 -m pip wheel --wheel-dir wheelhouse '.[dev]' 'setuptools>=77,<81' wheel
```

Transfer the project and wheelhouse to the offline host. After creating and
activating its virtual environment:

```bash
python -m pip install --no-index --find-links wheelhouse 'setuptools>=77,<81' wheel
python -m pip install --no-index --find-links wheelhouse --no-build-isolation -e '.[dev]'
python -m pytest
python -m ruff check .
python -m ruff format --check .
python -m build --no-isolation
```

Do the skill-directory transfer separately as described in SKILLS.md. Offline
package tooling does not itself provide an offline model; Codex still needs its
configured model access.
