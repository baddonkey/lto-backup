---
mode: agent
description: "Review code in lto-backup for architecture compliance. Use when auditing a module, PR, or change against project rules. Checks DI, interface boundaries, type hints, logging, exceptions, and test coverage."
---

## Task

Review the specified code for adherence to the project architecture rules.

### Checklist

- [ ] One production class per file.
- [ ] No concrete infrastructure instantiated inside services or domain classes.
- [ ] Domain objects are free of infrastructure concerns.
- [ ] Type hints are present everywhere.
- [ ] No `print()` calls — logging only.
- [ ] No global mutable state.
- [ ] Explicit domain exceptions used (not bare `Exception`).
- [ ] Corresponding unit tests exist and pass.
- [ ] mypy clean.

### Target

<!-- Specify the file, PR, or change to review here -->
