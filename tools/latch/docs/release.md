# Release

Latch is published to PyPI as `boundary-latch` with Trusted Publishing, so no token is stored in the repo. The workflow is `.github/workflows/release-latch.yml`. It runs when a tag that starts with `latch-v` is pushed.

## What the workflow does

1. Build: builds the sdist and wheel in `tools/latch`, runs `twine check`, and fails when the tag does not match the version in `pyproject.toml`. Tag `latch-v0.1.0` needs version `0.1.0`.
2. Publish to TestPyPI, behind the `testpypi` environment.
3. Publish to PyPI, behind the `pypi` environment, after TestPyPI succeeds.

Each publish job asks for the `id-token: write` permission. The build job only reads the repo.

## One time setup

These steps need the repo owner. Nothing is published until they are done.

1. On test.pypi.org, then on pypi.org, open Publishing and add a pending publisher.
   - Project: `boundary-latch`
   - Owner: `mgrofsky`
   - Repository: `boundary-lab`
   - Workflow: `release-latch.yml`
   - Environment: `testpypi` on TestPyPI and `pypi` on PyPI
2. In the GitHub repo, open Settings, then Environments. Create `testpypi` and `pypi`. Add the owner as a required reviewer on each.

The project name is free until the first upload. Check it first with `curl -s -o /dev/null -w "%{http_code}" https://pypi.org/pypi/boundary-latch/json`. A 404 means free.

## Publish a version

1. Change `version` in `tools/latch/pyproject.toml` and `__version__` in `src/boundary_latch/__init__.py`. Merge that through a pull request.
2. Tag the merge commit and push the tag.

        git tag latch-v0.1.0
        git push origin latch-v0.1.0

3. Approve the `testpypi` run, check the page on test.pypi.org, then approve the `pypi` run.

## Check the wheel yourself

    cd tools/latch
    python -m build
    twine check dist/*
    python3 -m venv /tmp/latch_clean
    /tmp/latch_clean/bin/pip install dist/*.whl
    /tmp/latch_clean/bin/boundary-latch scenarios --json

The clean environment should hold pydantic and nothing from Redline, numpy, fastapi or httpx. CI runs the same check in the `latch-wheel` job.

A published version cannot be replaced. If one is wrong, publish the next version number.
