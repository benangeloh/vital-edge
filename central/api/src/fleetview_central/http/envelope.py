"""Envelope response yang seragam.

Setiap endpoint di setiap bidang mengembalikan bentuk yang sama. Klien —
dashboard maupun Edge Agent — jadi punya satu jalur penanganan error, bukan
satu per endpoint.

    {"ok": true,  "data": {...}, "error": null,       "meta": {}}
    {"ok": false, "data": null,  "error": {...},      "meta": {}}
"""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

__all__ = ["Envelope", "ErrorBody", "PageMeta", "error", "success"]

T = TypeVar("T")


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    retryable: bool = False
    """Klien memakai ini untuk memutuskan mengulang atau menyerah. Sync Engine
    di edge mematuhinya secara harfiah."""


class PageMeta(BaseModel):
    total: int
    page: int
    limit: int


class Envelope(BaseModel, Generic[T]):
    ok: bool
    data: T | None = None
    error: ErrorBody | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


def success(data: Any = None, **meta: Any) -> dict[str, Any]:
    return {"ok": True, "data": data, "error": None, "meta": meta}


def error(
    *,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
    retryable: bool = False,
) -> dict[str, Any]:
    return {
        "ok": False,
        "data": None,
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
            "retryable": retryable,
        },
        "meta": {},
    }
