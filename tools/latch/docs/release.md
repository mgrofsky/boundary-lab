# Release

Latch's package name on PyPI is `boundary-latch`. The first public release, 0.1.0, was uploaded manually.

The repository also has a release workflow in `.github/workflows/release-latch.yml`. It is configured to use Trusted Publishing, which lets GitHub publish without a stored PyPI token. The workflow starts when a tag beginning with `latch-v` is pushed. This page describes that configured path; the first upload did not verify that it works from start to finish.

## What the workflow does

1. Build the source archive, called an sdist, and the installable wheel in `tools/latch`. Run `twine check` and fail if the tag does not match the version in `pyproject.toml`. For example, tag `latch-v0.1.0` requires version `0.1.0`.
2. Publish to TestPyPI using the GitHub `testpypi` environment.
3. After TestPyPI succeeds, publish to PyPI using the GitHub `pypi` environment.

Each publish job requests the `id-token: write` permission to identify itself to the package service. The build job only needs read access to the repository. Approval before publishing depends on the reviewer rules set on the two GitHub environments.

## Check the publishing setup

The repository owner needs to check these settings before relying on the workflow. Having the workflow file in the repository does not confirm the service settings.

1. On TestPyPI and PyPI, open the project's Publishing settings and add or verify the trusted publisher. Use a pending publisher if the project has not been created on that service.
   - Project: `boundary-latch`
   - Owner: `mgrofsky`
   - Repository: `boundary-lab`
   - Workflow: `release-latch.yml`
   - Environment: `testpypi` on TestPyPI and `pypi` on PyPI
2. In the GitHub repository, open Settings, then Environments. Create or check `testpypi` and `pypi`. Add the owner as a required reviewer on each.

To check whether PyPI has a public project page, use `curl -s -o /dev/null -w "%{http_code}" https://pypi.org/pypi/boundary-latch/json`. A 200 means the project exists; a 404 means that URL has no public project. This check does not confirm that the publishing workflow is ready.

## Publish a version

1. Change `version` in `tools/latch/pyproject.toml` and `__version__` in `src/boundary_latch/__init__.py`. Merge the change through a pull request.
2. Tag the merge commit and push the tag. These commands show the tag format; choose the new version rather than reusing the already published 0.1.0 release.

        git tag latch-v0.1.0
        git push origin latch-v0.1.0

3. Approve the `testpypi` job, check the uploaded package on TestPyPI, then approve the `pypi` job. Confirm the uploaded version on PyPI after that job finishes.

## Check the wheel yourself

    cd tools/latch
    python -m build
    twine check dist/*
    python3 -m venv /tmp/latch_clean
    /tmp/latch_clean/bin/pip install dist/*.whl
    /tmp/latch_clean/bin/boundary-latch scenarios --json

The clean environment should install Latch with pydantic and its dependencies, without Redline, numpy, fastapi, or httpx. The `latch-wheel` job in the automated checks covers the same install and scenario check.

A published version cannot be replaced. If one is wrong, publish the next version number.
