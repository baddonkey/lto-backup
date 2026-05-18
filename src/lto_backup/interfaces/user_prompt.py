from typing import Protocol


class UserPrompt(Protocol):
    def ask(self, message: str) -> str: ...

    def inform(self, message: str) -> None: ...
