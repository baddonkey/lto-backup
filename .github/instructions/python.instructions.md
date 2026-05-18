---
description: "Use when writing or reviewing Python source files in lto-backup. Covers type hints, logging, exceptions, and dataclass conventions."
applyTo: "src/**/*.py"
---

## Type Hints

- All function signatures must have full type annotations.
- Use `X | Y` union syntax (Python 3.10+), not `Optional[X]`.
- Prefer `list[T]` / `dict[K, V]` over `List` / `Dict` from `typing`.

## Logging

- Every production class declares `logger = logging.getLogger(__name__)` at module level.
- Use `logger.debug` for per-item detail, `logger.info` for milestones, `logger.warning` for recoverable anomalies, `logger.error` before raising.
- Never use `print()`.

## Exceptions

- Raise only exceptions from `lto_backup.exceptions`.
- Always chain: `raise DomainError("...") from exc`.

## Dataclasses

- Domain objects use `@dataclass(frozen=True)`.
- Use `field(default_factory=list)` for mutable defaults.
- No infrastructure imports in `domain/` or `exceptions/`.

## General

- Prefer simple, explicit code — no clever one-liners.
- One class per file, filename matches class name in snake_case.
- No bare `except:` — always name the exception type.
