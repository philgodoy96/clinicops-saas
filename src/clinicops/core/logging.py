import json
import logging
from datetime import UTC, datetime
from logging.config import dictConfig
from typing import Any

from clinicops.core.request_context import get_correlation_id, get_request_id

STRUCTURED_LOG_FIELDS = (
    "event",
    "request_id",
    "correlation_id",
    "http_method",
    "http_path",
    "status_code",
    "duration_ms",
)


class RequestContextFilter(logging.Filter):
    """Inject active request identifiers into log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.__dict__.setdefault("request_id", get_request_id())
        record.__dict__.setdefault("correlation_id", get_correlation_id())
        return True


class JsonFormatter(logging.Formatter):
    """Render application log records as JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        for field_name in STRUCTURED_LOG_FIELDS:
            field_value = record.__dict__.get(field_name)
            if field_value is not None:
                payload[field_name] = field_value

        if record.exc_info is not None:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str)


def configure_logging(log_level: str) -> None:
    """Configure process-wide structured application logging."""

    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "filters": {
                "request_context": {
                    "()": "clinicops.core.logging.RequestContextFilter",
                }
            },
            "formatters": {
                "json": {
                    "()": "clinicops.core.logging.JsonFormatter",
                }
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "filters": ["request_context"],
                    "formatter": "json",
                    "stream": "ext://sys.stdout",
                }
            },
            "root": {
                "handlers": ["console"],
                "level": log_level,
            },
            "loggers": {
                "uvicorn": {
                    "handlers": ["console"],
                    "level": log_level,
                    "propagate": False,
                },
                "uvicorn.access": {
                    "handlers": [],
                    "level": "CRITICAL",
                    "propagate": False,
                },
                "uvicorn.error": {
                    "handlers": ["console"],
                    "level": log_level,
                    "propagate": False,
                },
            },
        }
    )
