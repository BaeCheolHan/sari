# sari v2

High-performance local indexing and search engine rebuilt on LSP-first architecture.

## Quick Start

```bash
python3 -m pip install -e .
python3 -m sari.cli.main doctor
python3 -m sari.cli.main daemon start
```

## Quality Gate

```bash
python3 -m pytest -q
python3 tools/quality/full_tree_policy_check.py --root src --fail-on-todo
```

## Release (PyPI)

```bash
# local preflight
tools/ci/release_pypi.sh
```

- GitHub Actions: `.github/workflows/release-pypi.yml`
- Trigger:
  - `v*` tag push -> publish to PyPI
  - manual dispatch -> publish to PyPI/TestPyPI
