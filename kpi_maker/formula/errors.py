"""Formula errors that an editor can point at."""
from __future__ import annotations

from typing import Optional


class FormulaError(ValueError):
    """A formula that cannot be parsed, validated or evaluated.

    Carries a character offset where one is known, so the studio can put the
    caret on the offending token instead of showing a message about a formula
    the user has to re-read to understand.
    """

    def __init__(self, message: str, offset: Optional[int] = None,
                 hint: Optional[str] = None) -> None:
        super().__init__(message)
        self.message = message
        self.offset = offset
        self.hint = hint

    def as_dict(self) -> dict:
        return {"message": self.message, "offset": self.offset, "hint": self.hint}

    def __str__(self) -> str:
        text = self.message
        if self.hint:
            text = f"{text} — {self.hint}"
        return text
