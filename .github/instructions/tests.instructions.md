---
description: "Use when writing, reviewing, or fixing tests in lto-backup. Covers pytest conventions, fakes, and coverage expectations."
applyTo: "tests/**/*.py"
---

## Structure

- Group tests in classes named `Test<Subject>`.
- One `setup_method` per class if shared state is needed — avoid module-level fixtures for simple cases.
- Test method names: `test_<what>_<expected_outcome>`.

## Isolation

- Never touch the real filesystem, network, or tape hardware — use `tmp_path` (pytest built-in) or in-memory fakes.
- Inject fakes via constructor, not monkeypatching.
- Do not import from `lto_backup.infrastructure` in domain or service tests.

## Coverage

For every implemented class, cover:
1. Happy path — expected inputs produce expected outputs.
2. Edge cases — zero, empty, boundary values.
3. Expected exceptions — use `pytest.raises(<SpecificException>)`, never `pytest.raises(Exception)`.

## Assertions

- One logical assertion per test where practical.
- Prefer `assert x == y` over `assertEqual` — plain assert reads better with pytest output.

## Running

```bash
pytest               # all tests
pytest tests/unit/   # unit only
pytest -x            # stop on first failure
```
