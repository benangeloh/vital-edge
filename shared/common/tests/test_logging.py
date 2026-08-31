"""Test setup logging dan correlation context."""

from __future__ import annotations

import json

import pytest
import structlog

from fleetview_common import configure_logging, current_context, get_logger, log_context


def test_configure_logging_json_menghasilkan_satu_baris_json(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging(level="INFO", fmt="json", service="test-svc", version="9.9.9")
    get_logger("t").info("peristiwa_uji", angka=1)

    payload = json.loads(capsys.readouterr().err.strip())
    assert payload["event"] == "peristiwa_uji"
    assert payload["service"] == "test-svc"
    assert payload["version"] == "9.9.9"
    assert payload["angka"] == 1


def test_log_context_menyisipkan_lalu_memulihkan(capsys: pytest.CaptureFixture[str]) -> None:
    """Inilah yang membuat satu batch_id bisa ditelusuri dari edge sampai dashboard."""
    configure_logging(level="INFO", fmt="json", service="test-svc", version="9.9.9")
    structlog.contextvars.clear_contextvars()

    with log_context(batch_id="abc-123", ship_id="kapal-1"):
        get_logger("t").info("di_dalam")
        assert current_context()["batch_id"] == "abc-123"

    get_logger("t").info("di_luar")

    lines = [json.loads(x) for x in capsys.readouterr().err.strip().splitlines()]
    inside, outside = lines[0], lines[1]
    assert inside["batch_id"] == "abc-123"
    assert inside["ship_id"] == "kapal-1"
    assert "batch_id" not in outside
    assert current_context() == {}
