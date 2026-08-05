import json
import logging
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

LOG_NAME = "RTR-FRAUD-VALIDATION"
thailand_tz = ZoneInfo("Asia/Bangkok")


# Initial Logger
class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        log_object: dict = {
            "severity": record.levelname,
            "project_name": "rtr-fraud-validation",
            "team": "RPA&AI-Automation",
            "datetime": datetime.now(thailand_tz).strftime("%Y-%m-%d %H:%M:%S"),
            "message": record.getMessage(),
        }
        if hasattr(record, "json_payload"):
            log_object["data"] = record.json_payload
        return json.dumps(log_object, ensure_ascii=False)


def get_logger(name: str = LOG_NAME) -> logging.Logger:
    """Return a named logger with the JSON formatter attached.

    Safe to call multiple times — handlers are only added once.
    Prefer this factory in new code; the module-level ``logger`` singleton
    is kept for backward compatibility.
    """
    log = logging.getLogger(name)
    if not log.handlers:
        _handler = logging.StreamHandler(sys.stdout)
        _handler.setFormatter(JsonFormatter())
        log.setLevel(logging.INFO)
        log.addHandler(_handler)
    return log


# Backward-compatible singleton used by existing code
logger = get_logger(LOG_NAME)
