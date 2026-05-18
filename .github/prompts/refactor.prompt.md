---
mode: agent
description: "Refactor a module in lto-backup while keeping tests green. Use when improving structure, extracting responsibilities, or fixing architecture violations. Reads code and tests first, applies minimal change."
---

## Task

Refactor the specified module following the project architecture rules.

### Steps

1. Read the target file and its tests.
2. Identify the specific refactoring goal (see below).
3. Apply the smallest change that achieves the goal.
4. Do not change behaviour — all existing tests must still pass.
5. Run `mypy` and fix any type errors.
6. Run `pytest` and confirm all tests pass.

### Refactoring Goal

<!-- Describe what to refactor and why here -->
