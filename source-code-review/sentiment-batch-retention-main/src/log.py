from zoneinfo import ZoneInfo
from datetime import datetime
from dotenv import load_dotenv
import json
import logging
import sys
import os

load_dotenv(override=True)
LOG_NAME = os.environ["LOG_NAME"]
thailand_tz = ZoneInfo("Asia/Bangkok")

class JsonFormatter(logging.Formatter):
    """Formats log records as a JSON object."""
    def format(self, record):
        log_object = {
            "severity": record.levelname,
            "project_name": "sentiment-batch",
            "team": "RPA&AI-Automation",
            "datetime": datetime.now(thailand_tz).strftime("%Y-%m-%d %H:%M:%S"),
            "message": record.getMessage()
        }
        if hasattr(record, 'json_payload'):
            log_object["data"] = record.json_payload
        if record.exc_info:
            log_object['exception'] = self.formatException(record.exc_info)
        return json.dumps(log_object, ensure_ascii=False)

logger = logging.getLogger(LOG_NAME)
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(JsonFormatter())
logger.setLevel(logging.INFO)
logger.addHandler(handler)
# Suppress noisy Google API logs
logging.getLogger("google.api_core").setLevel(logging.WARNING)
logging.getLogger("google.auth").setLevel(logging.WARNING)