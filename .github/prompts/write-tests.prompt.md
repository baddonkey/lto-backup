---
mode: agent
description: "Write unit tests for an existing class in lto-backup. Use when adding test coverage, testing a new class, or auditing missing cases. Reads source first, writes pytest tests, verifies all pass."
---

## Task

Write comprehensive unit tests for the class described below.

### Steps

1. Read the target source file to understand its behaviour.
2. Write pytest tests in the corresponding `tests/` file.
3. Cover happy paths, edge cases, and expected exceptions.
4. Use fakes injected via constructor — never touch real filesystem or tape hardware.
5. Run `pytest` and confirm all tests pass.

### Target

<!-- Specify the class or module to test here -->
