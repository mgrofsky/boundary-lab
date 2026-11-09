# Release

Dye's package name on PyPI is `boundary-dye`. The first public release, 0.1.0, was uploaded by hand with twine and an API token, to TestPyPI first and then PyPI.

The repository also has a release workflow in `.github/workflows/release-dye.yml`. It uses Trusted Publishing, which lets GitHub publish without a stored PyPI token. The workflow starts when a tag beginning with `dye-v` is pushed. It ran from start to finish on the `dye-v0.1.0` tag after the hand upload, and both publish jobs succeeded. The publish steps skip files the index already has, so that run uploaded nothing new.

## What the workflow does

1. Build the source archive, called an sdist, and the installable wheel in `tools/dye`. Run `twine check` and fail if the tag does not match the version in `pyproject.toml`. For example, tag `dye-v0.1.0` requires version `0.1.0`.
2. Publish to TestPyPI using the GitHub `testpypi` environment. Files that TestPyPI already has are skipped.
3. After TestPyPI succeeds, publish to PyPI using the GitHub `pypi` environment.

Each publish job requests the `id-token: write` permission to identify itself to the package service. The build job only needs read access to the repository. Approval before publishing depends on the reviewer rules set on the two GitHub environments.

## Check the publishing setup

These settings are in place for the current repository and workflow names. Check them again if any of those names change. Having the workflow file in the repository does not confirm the service settings.

1. On TestPyPI and PyPI, open the project's Publishing settings and add or verify the trusted publisher. Use a pending publisher if the project has not been created on that service.
   - Project: `boundary-dye`
   - Owner: `mgrofsky`
   - Repository: `boundary-lab`
   - Workflow: `release-dye.yml`
   - Environment: `testpypi` on TestPyPI and `pypi` on PyPI
2. In the GitHub repository, open Settings, then Environments. Create or check `testpypi` and `pypi`. Add the owner as a required reviewer on each. Latch shares these two environments.

To check whether PyPI has a public project page, use `curl -s -o /dev/null -w "%{http_code}" https://pypi.org/pypi/boundary-dye/json`. A 200 means the project exists; a 404 means that URL has no public project.

## Publish a version

1. Change `version` in `tools/dye/pyproject.toml` and `__version__` in `src/boundary_dye/__init__.py`. Merge the change through a pull request.
2. Tag the merge commit and push the tag.

        git tag dye-v0.1.0
        git push origin dye-v0.1.0

3. Approve the `testpypi` job, check the uploaded package on TestPyPI, then approve the `pypi` job. Confirm the uploaded version on PyPI after that job finishes.

## Check the wheel yourself

    cd tools/dye
    python -m build
    twine check dist/*
    python3 -m venv /tmp/dye_clean
    /tmp/dye_clean/bin/pip install dist/*.whl
    /tmp/dye_clean/bin/boundary-dye scenarios --json

The clean environment should install Dye with no other packages. The `scenarios` command should print five results, all true.

A published version cannot be replaced. If one is wrong, publish the next version number.
