"""Typed application errors that map onto HTTP responses."""

from __future__ import annotations


class AppError(Exception):
    status_code = 400

    def __init__(self, detail: str, status_code: int | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        if status_code is not None:
            self.status_code = status_code


class NotFound(AppError):
    status_code = 404


class InvalidInput(AppError):
    status_code = 400


class ConfigMissing(AppError):
    status_code = 400
