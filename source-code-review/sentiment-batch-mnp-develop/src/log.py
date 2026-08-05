from zoneinfo import ZoneInfo
from datetime import datetime
from dotenv import load_dotenv
from pythonjsonlogger import jsonlogger
import json
import logging
import sys
import os

load_dotenv(override=True)

class CustomJsonFormatter(jsonlogger.JsonFormatter):
    """Custom JSON formatter with specific fields."""
    
    def __init__(self):
        super().__init__(json_ensure_ascii=False)
        self.thailand_tz = ZoneInfo("Asia/Bangkok")
    
    def add_fields(self, log_record, record, message_dict):
        super().add_fields(log_record, record, message_dict)
        
        # Clear the log_record and rebuild in desired order
        custom_data = log_record.pop('data', None) if 'data' in log_record else None
        if hasattr(record, 'json_payload'):
            custom_data = record.json_payload
        
        # Clear all fields
        log_record.clear()
        
        # Add fields in desired order
        log_record['severity'] = record.levelname
        log_record['project_name'] = 'sentiment-batch'
        log_record['team'] = 'RPA&AI-Automation'
        log_record['datetime'] = datetime.now(self.thailand_tz).strftime("%Y-%m-%d %H:%M:%S")
        log_record['message'] = record.getMessage()
        
        # Add exception info if present
        if record.exc_info:
            log_record['exception'] = self.formatException(record.exc_info)

        # Add custom data at the end if present
        if custom_data:
            log_record['data'] = custom_data

class Logger:
    def __init__(self, name=__name__, env: str = "prod"):
        if env == "dev":
            self.logger = logging.getLogger(name)
            # Clear existing handlers to avoid duplicates
            self.logger.handlers.clear()
            
            handler = logging.StreamHandler(sys.stdout)
            self.logger.setLevel(logging.INFO)
            formatter = self.dev_format()
            handler.setFormatter(formatter)
        else:
            self.logger = logging.getLogger(os.environ["LOG_NAME"])
            # Clear existing handlers to avoid duplicates
            self.logger.handlers.clear()
            
            handler = logging.StreamHandler(sys.stdout)
            self.logger.setLevel(logging.INFO)
            formatter = self.json_format()
            handler.setFormatter(formatter)
        
        self.logger.addHandler(handler)
        logging.getLogger("google.api_core").setLevel(logging.WARNING)
        logging.getLogger("google.auth").setLevel(logging.WARNING)

    def json_format(self):
        return CustomJsonFormatter()
    
    def dev_format(self):
        formatter = logging.Formatter(
            fmt='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        return formatter
    
    def info(self, message, **kwargs):
        self.logger.info(message, **kwargs)

    def debug(self, message, **kwargs):
        self.logger.debug(message, **kwargs)
    
    def warning(self, message, **kwargs):
        self.logger.warning(message, **kwargs)
    
    def error(self, message, exc_info=False, **kwargs):
        self.logger.error(message, exc_info=exc_info, **kwargs)