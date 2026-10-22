"""Preserve the original failure while reporting independent cleanup errors."""

from __future__ import annotations


def raise_cleanup_errors(
    message: str, errors: list[BaseException], primary: BaseException | None = None,
) -> None:
    unique: list[BaseException] = [primary] if primary is not None else []

    def retain(error: BaseException) -> None:
        if any(error is previous for previous in unique):
            return
        if isinstance(error, BaseExceptionGroup):
            for nested in error.exceptions:
                retain(nested)
        else:
            unique.append(error)

    for error in errors:
        retain(error)
    if len(unique) == 1:
        if unique[0] is not primary:
            raise unique[0]
    elif unique:
        raise BaseExceptionGroup(message, unique)
    # With only the primary failure, let the owning context propagate it.
