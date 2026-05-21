import json
import logging
from pathlib import Path

import pytest

from src.logging_setup import configure_logging


@pytest.fixture(autouse=True)
def reset_logging():
    """Restore root logger handlers after each test."""
    root = logging.getLogger()
    original_handlers = root.handlers[:]
    original_level = root.level
    yield
    # Close any file handlers added during the test
    for h in root.handlers:
        if h not in original_handlers:
            h.close()
    root.handlers = original_handlers
    root.setLevel(original_level)


def test_configure_logging_writes_jsonl(tmp_path: Path):
    log_file = tmp_path / "migration.log"
    configure_logging(log_file, level="INFO")
    logger = logging.getLogger("test_jsonl_logger")
    logger.info("hello", extra={"video_id": "v1", "state": "downloaded"})

    for h in logging.getLogger().handlers:
        h.flush()

    content = log_file.read_text(encoding="utf-8").strip()
    line = json.loads(content.splitlines()[-1])
    assert line["msg"] == "hello"
    assert line["level"] == "INFO"
    assert line["video_id"] == "v1"
    assert "ts" in line
