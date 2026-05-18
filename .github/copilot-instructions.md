# Copilot Instructions — lto-backup

Python 3.12+ application that backs up a file-based records management system to LTO tapes.

## Non-Negotiable Rules

- One production class per file.
- Dependency injection — never instantiate concrete infrastructure inside services or domain classes.
- `typing.Protocol` or ABCs for all infrastructure interfaces.
- Type hints everywhere; mypy strict must pass.
- No `print()` — use `logging`.
- No global mutable state.
- Explicit domain exceptions from `lto_backup.exceptions` only.
- Every implemented class requires corresponding unit tests.

## Layout

```
src/lto_backup/domain/          pure frozen dataclasses
src/lto_backup/exceptions/      domain exception hierarchy
src/lto_backup/interfaces/      Protocol interfaces
src/lto_backup/infrastructure/  concrete adapters
src/lto_backup/services/        business logic (no infra imports)
src/lto_backup/config/          configuration dataclasses
src/lto_backup/wiring/          composition root
src/lto_backup/cli/             entry point
tests/                          mirrors src layout
```

Domain rules and implementation roadmap → `SPEC.md`.
