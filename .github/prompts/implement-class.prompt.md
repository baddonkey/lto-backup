---
mode: agent
description: "Implement a single class in lto-backup. Use when asked to add or complete a class. Reads existing code first, implements with full types, adds tests, verifies mypy and pytest."
---

## Task

Implement the class described below following the project architecture rules.

### Steps

1. Read the target file (if it already exists) and any related files.
2. Implement the class with full type hints.
3. Keep the class to one responsibility; one class per file.
4. Do not instantiate concrete infrastructure inside the class.
5. Add or update the corresponding unit test file in `tests/`.
6. Run `mypy` and fix any type errors.
7. Run `pytest` and confirm all tests pass.

### Target

<!-- Describe the class to implement here -->
