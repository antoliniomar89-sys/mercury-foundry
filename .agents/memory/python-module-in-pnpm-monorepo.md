---
name: Python module install in pnpm monorepo
description: Where installLanguagePackages/installProgrammingLanguage put Python project files in this pnpm-workspace template, and pytest cwd behavior.
---

`installProgrammingLanguage({language:"python-3.12"})` + `installLanguagePackages({language:"python", packages:[...]})`
run `uv init` / `uv add` at the **repo root**, creating `pyproject.toml`, `uv.lock`, and a
`.pythonlibs` venv there — even if the Python code you actually want lives in a subdirectory
(e.g. a standalone tool alongside a pnpm monorepo of JS artifacts).

**Why:** the tool has no concept of "subproject" — it always operates at the workspace root.

**How to apply:** for a standalone Python tool inside a pnpm monorepo, let the root
pyproject.toml/uv.lock/.pythonlibs stay as the single shared Python environment (don't fight it
by creating a second pyproject.toml inside the subfolder — that causes uv workspace conflicts).
Put actual source/tests in the subfolder and just `cd` into it (or set cwd) when running
`python3` / `pytest` — the root `.pythonlibs` venv is active regardless of cwd, and `pytest -m`
picks up cwd for rootdir/import purposes. Delete the auto-generated root `main.py` stub if unused.
