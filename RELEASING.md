# Releasing replaycheck

## Before the release

1. Update the version in `pyproject.toml` and `replaycheck/__init__.py`.
2. Add a release section to `CHANGELOG.md`.
3. Run `make test`, `make benchmark`, and the three demo targets.
4. Build a wheel and verify it in a clean virtual environment:

   ```console
   python -m pip wheel . --no-deps --wheel-dir /tmp/replaycheck-wheel
   python -m venv /tmp/replaycheck-release-venv
   /tmp/replaycheck-release-venv/bin/python -m pip install \
     /tmp/replaycheck-wheel/replaycheck-*.whl
   /tmp/replaycheck-release-venv/bin/replaycheck --version
   ```

5. Push the release commit and wait for the matrix CI run to pass.

## Publishing

Configure a trusted PyPI publisher for the repository before enabling automated
publishing. Then create an annotated version tag after CI is green:

```console
git tag -a v0.2.0 -m "replaycheck 0.2.0"
git push origin v0.2.0
```

The tag should publish the already-tested wheel, not a separately modified
working tree. Until trusted publishing is configured, upload the wheel manually
from a clean checkout using the approved PyPI credentials.

## After the release

- Verify `pip install replaycheck==<version>` in a clean environment.
- Verify both the Python API and `replaycheck --version`.
- Create the matching GitHub release and paste the changelog entry.
- Record the CI run, wheel filename, and PyPI URL in the release notes.
