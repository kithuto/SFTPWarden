# Contributing, Development and Testing

This guide is for people who want to run SFTPWarden from source, contribute a
change, build documentation or validate the project before publishing a release.

For the shorter GitHub contribution guide, see
[CONTRIBUTING.md](https://github.com/kithuto/sftpwarden/blob/dev/CONTRIBUTING.md).

## Branch Model

SFTPWarden uses `dev` as the integration branch and `main` as the protected
production/release branch.

Contributors should not work directly on `dev` or open normal pull requests to
`main`. Instead:

1. Fork the repository.
2. Create your own branch from `dev`.
3. Develop and validate your change in that branch.
4. Open a Pull Request from your branch to `dev`.

```bash
git clone https://github.com/<your-user>/sftpwarden.git
cd sftpwarden
git remote add upstream https://github.com/kithuto/sftpwarden.git
git fetch upstream
git checkout -b dev upstream/dev
git checkout -b fix/my-change
```

After development, push your branch and open:

```text
fix/my-change -> dev
```

The maintainer promotes accepted changes from `dev` to `main` when preparing
production updates or public releases.

## Install from Source

```bash
git clone https://github.com/kithuto/sftpwarden.git
cd sftpwarden
git checkout dev
python -m pip install --upgrade pip
python -m pip install -e ".[dev,docs,mysql,postgres,mongodb]"
sftpwarden --version
```

The `mysql` extra also enables MariaDB because both providers use PyMySQL. The
`mariadb` extra is an alias for the same dependency.

Use Python 3.11, 3.12, 3.13 or 3.14. Install the Python versions locally before
running the full `tox` matrix.

## Development Workflow

Work in a feature or fix branch created from `dev`. Keep changes focused on one
user-visible outcome and read the relevant implementation before editing it.

For a CLI change, inspect help and run the affected tests. For an operational
change, also plan a disposable environment that can prove the behavior.
Use [release validation](https://github.com/kithuto/sftpwarden/blob/dev/tests/release_validation/README.md)
for prerequisites and cleanup rules.

Image builds, rendered manifests and mocked tests are useful intermediate
checks. Runtime, authentication, deployment, watcher and recovery changes also
need real environment checks before they are considered complete. Inspect the
selected Docker or Kubernetes context before creating resources.

## Testing

While iterating, use Python 3.14 and the smallest relevant test selection:

```bash
python -m pytest tests/cli/
```

Narrow that path to the affected test files when possible. Batch related edits
before checking them. Repeat passing checks only when a subsequent change or
failure invalidates the result.

| Change | Typical focused check |
| --- | --- |
| CLI/service behavior | Relevant pytest files |
| Python formatting or imports | `tox -e lint` |
| Type annotations or typed interfaces | `tox -e mypy` |
| Sphinx docs or their inputs | `tox -e docs` |
| Standalone Markdown | Diff, links and structure |
| Packaging or dependency metadata | Relevant package or audit environment |

After a complete code change is ready, run the full ordinary suite once:

```bash
tox
```

This covers lint, typing, Python 3.11–3.14 tests, coverage, documentation, package
checks and dependency audit. Install the supported interpreters first. The
coverage minimum is 95% and the project target is above 97%. Add meaningful
behavior and failure-path tests rather than exclusions.

The ordinary suite excludes opt-in external release validation. Report those
checks separately and identify any platform you could not verify.
`tox -e clean` removes artifacts and designated smoke-test images; it is not
a validation step. Inspect its scope before running it.

## Documentation

The Sphinx source lives in `docs/`. `docs/index.md` owns the main navigation
and static assets live under `docs/_static/`.

Build the Sphinx site locally:

```bash
python -m pip install -e ".[docs]"
sphinx-build -W --keep-going -b html docs docs/_build/html
```

The CI-friendly validation command is:

```bash
tox -e docs
```

Keep the README focused on adoption. Put deeper explanations in the specific docs
pages:

- [Getting Started](getting-started.md)
- [Configuration](configuration.md)
- [Providers](providers.md)
- [Named Keys](named-keys.md)
- [Operations](operations.md)
- [Security](security.md)
- [CLI Reference](cli-reference.md)

The public site is published from `main` by `.github/workflows/docs.yml`.
Its path filters include the public root documents, `docs/**` and
`pyproject.toml`, which supplies the documented version. Check that workflow for
the current publishing commands.

Use `tox -e docs` for documentation changes. The strict build above also treats
warnings as errors and leaves HTML available for review. Check links and read
rendered pages at desktop and narrow widths; a successful Sphinx build does not
prove that every external URL or documented procedure works.

Write in English. Start tutorials with the simplest working path, explain the
expected result and link to deeper reference material. Avoid serial commas before
`and` or `or`, required virtual environments and version-pinned basic installation.
Keep examples consistent with the password-first Alice tutorial and default YAML
provider.

## Adding or Changing Features

Keep public behavior stable unless the change is intentional and documented:

- CLI command names and option names should remain compatible.
- `sftpwarden.yaml`, provider files, SQL schemas and context registry formats
  should remain compatible.
- Runtime state should remain readable across upgrades.
- JSON output should stay parseable.

For user/provider changes, add tests that cover both service behavior and CLI
behavior when the CLI output matters.

For remote/deploy/watcher changes, include dry-run coverage so generated SSH,
rsync/scp, Docker and scheduler commands stay reviewable.

Tox environments clean their generated artifacts. Separate `tox -e clean`
cleanup also removes designated Docker smoke-test images; inspect its scope
before using it and preserve unrelated operator resources.

## Pull Request Checklist

Before opening a PR:

- target `dev`, not `main`;
- for docs-only changes, run `tox -e docs`;
- for code changes, run `tox`;
- verify affected runtime or deployment behavior in a disposable environment;
- update README/docs when adoption or operations behavior changes;
- update examples when configuration changes;
- redact secrets from logs and screenshots;
- describe the user-facing behavior, validation performed and any known limits.

## Release Readiness

Release validation is broader than the ordinary test suite. Follow the
[release-validation guide](https://github.com/kithuto/sftpwarden/blob/dev/tests/release_validation/README.md)
for required infrastructure, supported subsets and cleanup.

The release workflow validates candidate packages, images and the chart before
publication. Promotion must use those same artifacts and recorded hashes.
Do not rebuild a validated candidate during publication or overwrite a conflicting
versioned artifact.

Documentation-only diffs run Sphinx without the Python or infrastructure test
matrix. Code, dependency, test and workflow changes require full validation.
For a `dev` → `main` PR or the subsequent main push, CI can reuse a successful
dev push candidate whose complete Git tree matches. It checks the validation
receipt and artifact hashes, then refreshes the security scans without rebuilding
the images. Missing or expired evidence requires full validation; a matching
failed candidate blocks promotion. Manual validation runs force full
validation. Require `Release aggregate` in branch protection, since individual
jobs intentionally skip when another validation route applies.

See the root [contribution guide](https://github.com/kithuto/sftpwarden/blob/dev/CONTRIBUTING.md)
for the exact routing, wait limits and trusted-candidate requirements.

The deployment release group rehearses image/index and chart publication in a
disposable registry, including safe retries and conflicting-artifact rejection.
If production publication fails partway through, rerun the failed jobs from that
same workflow run so they reuse its verified artifacts. Starting a new run may
rebuild candidates if trusted dev artifacts are unavailable, and may correctly
reject bytes that differ from files already published under that version.

If the version reached `main` without publication, use the Release candidate
workflow's manual `Publish or recover the current version (main only)` option
on `main`. It reuses an identical validated dev candidate or performs full
validation when no such evidence remains. Security and artifact conflict checks
still apply. Ordinary manual runs do not publish. A requested release must pass
the separate `Publication aggregate`; skipped publication is not a successful
release. See the root contribution guide for recovery details.

Review dependency and container scans, SBOM results and required platform gates.
Missing infrastructure or skipped checks are unverified scope, not passing
evidence. Keep release-specific results in the active release plan.

When publishing, move the shipped version out of the README roadmap and keep
only the next two future versions there. Record released behavior in the
changelog. Keep the project licensed under MIT.
