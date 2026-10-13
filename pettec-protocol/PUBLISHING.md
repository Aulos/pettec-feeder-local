# Publishing pettec-xlink

The library is a separate PyPI distribution built from this repository's
`pettec-protocol` directory. It uses the MIT license. Its runtime dependencies
are limited to the Python standard library.

Before the first release, sign in to PyPI and open
[Publishing](https://pypi.org/manage/account/publishing/). Add a pending publisher
with these exact values:

| Field | Value |
| --- | --- |
| PyPI project name | `pettec-xlink` |
| Owner | `Aulos` |
| Repository name | `pettec-feeder-local` |
| Workflow name | `protocol-publish.yml` |
| Environment name | `pypi` |

Create the GitHub Actions environment `pypi` in the repository settings. Protect
its deployments according to your repository's release policy. No PyPI API token
is needed. The workflow name is the filename, without `.github/workflows/`.

For the initial library release:

1. Wait for the monorepo extraction branch to pass the validation workflow.
2. Create and push the tag `protocol-v0.1.0` at that validated commit. The tag
   contains the publishing workflow; it does not need to be on `main` yet.
3. Confirm that the **Publish protocol library** workflow passes and PyPI
   displays `pettec-xlink` version `0.1.0` with a wheel and source distribution.
4. Merge the extraction and create the integration's `v0.6.0` GitHub release only
   after the PyPI package can be installed. The integration manifest already pins `pettec-xlink==0.1.0`.

Do not create a GitHub Release object for the library tag. HACS uses this
repository's GitHub releases for integration updates, so keep those releases
reserved for integration versions.

For subsequent library releases, change the package version in
`pettec-protocol/pyproject.toml`, validate the package, then push a matching
`protocol-v<VERSION>` tag. The publishing workflow rejects mismatched tags.
Update both the integration manifest and root `pyproject.toml` dependency pin
when the integration starts using a new library version.

The publishing workflow builds and tests the library before uploading its
artifacts to PyPI using Trusted Publishing. GitHub Actions validation also
installs the wheel and runs the library tests without installing Home Assistant.
