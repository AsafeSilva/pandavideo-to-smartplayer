"""Logging estruturado em JSON-lines."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path


_STANDARD = {"name", "msg", "args", "levelname", "levelno", "pathname", "filename",
             "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
             "created", "msecs", "relativeCreated", "thread", "threadName",
             "processName", "process", "message", "asctime"}


class JsonLinesFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for k, v in record.__dict__.items():
            if k in _STANDARD or k.startswith("_"):
                continue
            try:
                json.dumps(v)
                payload[k] = v
            except (TypeError, ValueError):
                payload[k] = repr(v)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(log_path: Path, level: str = "INFO") -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(getattr(logging, level))

    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(JsonLinesFormatter())
    sh = logging.StreamHandler()
    sh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))

    root.handlers.clear()
    root.addHandler(fh)
    root.addHandler(sh)
