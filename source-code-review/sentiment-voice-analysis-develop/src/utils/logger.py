# Library imports
import logging
import os
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from pythonjsonlogger import jsonlogger

load_dotenv()


class CustomJsonFormatter(jsonlogger.JsonFormatter):
    """
    Custom JSON formatter with specific fields for structured logging.
    Formats logs in JSON with project-specific metadata.
    """

    # Configuration Constants
    DEFAULT_TIMEZONE = "Asia/Bangkok"
    DEFAULT_PROJECT_NAME = "sentiment-batch"
    DEFAULT_TEAM_NAME = "RPA&AI-Automation"

    def __init__(self, project_name: str | None = None, team_name: str | None = None):
        """
        Initialize the custom JSON formatter.

        Parameters:
            project_name (str, optional): Project name for logging. Defaults to 'sentiment-batch'
            team_name (str, optional): Team name for logging. Defaults to 'RPA&AI-Automation'
        """
        super().__init__(json_ensure_ascii=False)
        self.thailand_tz = ZoneInfo(self.DEFAULT_TIMEZONE)
        self.project_name = project_name or self.DEFAULT_PROJECT_NAME
        self.team_name = team_name or self.DEFAULT_TEAM_NAME

    def add_fields(self, log_record: dict, record: logging.LogRecord, message_dict: dict) -> None:
        """
        Add custom fields to the log record in a specific order.

        Parameters:
            log_record (dict): The log record to modify
            record (logging.LogRecord): The original log record
            message_dict (dict): The message dictionary

        Returns:
            None
        """
        super().add_fields(log_record, record, message_dict)

        # Extract custom data if present
        custom_data = log_record.pop("data", None) if "data" in log_record else None
        if hasattr(record, "json_payload"):
            custom_data = record.json_payload

        # Clear all fields
        log_record.clear()

        # Add fields in desired order
        log_record["severity"] = record.levelname
        log_record["project_name"] = self.project_name
        log_record["team"] = self.team_name
        log_record["datetime"] = datetime.now(self.thailand_tz).strftime("%Y-%m-%d %H:%M:%S")
        log_record["logger_name"] = record.name
        log_record["message"] = record.getMessage()

        # Add exception info if present
        if record.exc_info:
            log_record["exception"] = self.formatException(record.exc_info)

        # Add custom data at the end if present
        if custom_data:
            log_record["data"] = custom_data


class Logger:
    """
    Custom logger supporting JSON (production) and development formats.
    Provides structured logging with environment-based configuration.
    """

    # Log Level Mapping
    LOG_LEVELS = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
        "CRITICAL": logging.CRITICAL,
    }

    # Default Configuration
    DEFAULT_LOG_LEVEL = "INFO"
    DEFAULT_LOG_NAME = "app"
    DEFAULT_ENV = "prod"
    DEFAULT_LOG_DIR = "logs"  # Default log directory
    DEFAULT_LOG_FILE = "app.log"  # Default log file name

    def __init__(
        self,
        name: str = __name__,
        env: str | None = None,
        log_to_file: bool = True,  # Enable file logging
        log_dir: str | None = None,  # Custom log directory
        log_file: str | None = None,  # Custom log file name
        max_bytes: int = 10 * 1024 * 1024,  # 10MB per file
        backup_count: int = 5,  # Keep 5 backup files
    ):
        """
        Initialize the logger with environment-based configuration.

        Parameters:
            name (str): The name of the logger. Defaults to module name
            env (str, optional): Environment ('local' or 'prod'). Auto-detected from ENVIRONMENT env var
            log_to_file (bool): Whether to write logs to file in local mode. Defaults to True
            log_dir (str, optional): Directory for log files. Defaults to 'logs/'
            log_file (str, optional): Log file name. Defaults to 'app.log'
            max_bytes (int): Maximum size per log file. Defaults to 10MB
            backup_count (int): Number of backup files to keep. Defaults to 5

        Raises:
            ValueError: If logger initialization fails
        """
        # Determine environment
        env = os.getenv("ENVIRONMENT", self.DEFAULT_ENV).lower() if env is None else env.lower()

        self.env = env
        self.logger_name = name

        # File logging configuration
        self.log_to_file = log_to_file and (self.env == "local")  # Only in local
        self.log_dir = log_dir or os.getenv("LOG_DIR", self.DEFAULT_LOG_DIR)
        self.log_file = log_file or os.getenv("LOG_FILE", self.DEFAULT_LOG_FILE)

        # Determine log level from environment variable
        log_level_str = os.getenv("LOG_LEVEL", self.DEFAULT_LOG_LEVEL).upper()
        self.log_level = self.LOG_LEVELS.get(log_level_str, logging.INFO)

        try:
            if self.env == "local":
                # Development mode: human-readable format
                logger_name = name
                self.logger = logging.getLogger(logger_name)

                # Prevent duplicate handlers
                if not self.logger.handlers:
                    # Console handler
                    console_handler = logging.StreamHandler(sys.stdout)
                    formatter = self._dev_format()
                    console_handler.setFormatter(formatter)
                    self.logger.addHandler(console_handler)

                    # File handler (if enabled)
                    if self.log_to_file:
                        file_handler = self._create_file_handler(formatter, max_bytes, backup_count)
                        if file_handler:
                            self.logger.addHandler(file_handler)

                    self.logger.setLevel(self.log_level)

            else:
                # Production mode: JSON format
                log_name = os.getenv("LOG_NAME", self.DEFAULT_LOG_NAME)
                self.logger = logging.getLogger(log_name)

                # Prevent duplicate handlers
                if not self.logger.handlers:
                    handler = logging.StreamHandler(sys.stdout)
                    formatter = self._json_format()
                    handler.setFormatter(formatter)
                    self.logger.addHandler(handler)
                    self.logger.setLevel(self.log_level)

            # Suppress noisy third-party loggers
            self._suppress_third_party_loggers()

            # Log successful initialization in debug mode
            if self.log_level == logging.DEBUG:
                log_location = (
                    f"console + file ({self.log_dir}/{self.log_file})" if self.log_to_file else "console only"
                )
                self.logger.debug(
                    f"Logger initialized - Name: {self.logger_name}, "
                    f"Environment: {self.env}, Level: {log_level_str}, "
                    f"Output: {log_location}"
                )

        except Exception as e:
            # Fallback to basic logging if initialization fails
            print(f"Failed to initialize logger: {e}", file=sys.stderr)
            self.logger = logging.getLogger(name)
            handler = logging.StreamHandler(sys.stdout)
            handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
            self.logger.addHandler(handler)
            self.logger.setLevel(logging.INFO)

    def _create_file_handler(
        self, formatter: logging.Formatter, max_bytes: int, backup_count: int
    ) -> RotatingFileHandler | None:
        """
        Create a rotating file handler for log files.

        Parameters:
            formatter (logging.Formatter): The formatter to use
            max_bytes (int): Maximum size per log file
            backup_count (int): Number of backup files to keep

        Returns:
            RotatingFileHandler or None: File handler if successful, None otherwise
        """
        try:
            # Create log directory if it doesn't exist
            log_path = Path(self.log_dir)
            log_path.mkdir(parents=True, exist_ok=True)

            # Full path to log file
            log_file_path = log_path / self.log_file

            # Create rotating file handler
            file_handler = RotatingFileHandler(
                filename=str(log_file_path), maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
            )
            file_handler.setFormatter(formatter)

            return file_handler

        except Exception as e:
            print(f"Failed to create file handler: {e}", file=sys.stderr)
            print("Logs will only be written to console", file=sys.stderr)
            return None

    def _json_format(self) -> CustomJsonFormatter:
        """
        Create JSON formatter for production logging.

        Returns:
            CustomJsonFormatter: Configured JSON formatter
        """
        project_name = os.getenv("PROJECT_NAME", CustomJsonFormatter.DEFAULT_PROJECT_NAME)
        team_name = os.getenv("TEAM_NAME", CustomJsonFormatter.DEFAULT_TEAM_NAME)
        return CustomJsonFormatter(project_name=project_name, team_name=team_name)

    def _dev_format(self) -> logging.Formatter:
        """
        Create human-readable formatter for development logging.

        Returns:
            logging.Formatter: Configured development formatter
        """
        return logging.Formatter(fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

    def _suppress_third_party_loggers(self) -> None:
        """
        Suppress verbose logging from third-party libraries.
        """
        noisy_loggers = ["google.api_core", "google.auth", "google.cloud", "urllib3", "requests"]
        for logger_name in noisy_loggers:
            logging.getLogger(logger_name).setLevel(logging.WARNING)

    def info(self, message: str, **kwargs) -> None:
        """
        Log an info level message.

        Parameters:
            message (str): The log message
            **kwargs: Additional keyword arguments for the logger
        """
        self.logger.info(message, **kwargs)

    def debug(self, message: str, **kwargs) -> None:
        """
        Log a debug level message.

        Parameters:
            message (str): The log message
            **kwargs: Additional keyword arguments for the logger
        """
        self.logger.debug(message, **kwargs)

    def warning(self, message: str, **kwargs) -> None:
        """
        Log a warning level message.

        Parameters:
            message (str): The log message
            **kwargs: Additional keyword arguments for the logger
        """
        self.logger.warning(message, **kwargs)

    def error(self, message: str, exc_info: bool = False, **kwargs) -> None:
        """
        Log an error level message.

        Parameters:
            message (str): The log message
            exc_info (bool): Whether to include exception traceback. Defaults to False
            **kwargs: Additional keyword arguments for the logger
        """
        self.logger.error(message, exc_info=exc_info, **kwargs)

    def critical(self, message: str, exc_info: bool = False, **kwargs) -> None:
        """
        Log a critical level message.

        Parameters:
            message (str): The log message
            exc_info (bool): Whether to include exception traceback. Defaults to False
            **kwargs: Additional keyword arguments for the logger
        """
        self.logger.critical(message, exc_info=exc_info, **kwargs)

    def exception(self, message: str, **kwargs) -> None:
        """
        Log an exception with traceback (convenience method).
        Equivalent to error() with exc_info=True.

        Parameters:
            message (str): The log message
            **kwargs: Additional keyword arguments for the logger
        """
        self.logger.exception(message, **kwargs)

    def set_level(self, level: str) -> None:
        """
        Dynamically change the log level.

        Parameters:
            level (str): The log level ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL')

        Raises:
            ValueError: If level is invalid
        """
        level_upper = level.upper()
        if level_upper not in self.LOG_LEVELS:
            raise ValueError(f"Invalid log level: {level}. Must be one of {list(self.LOG_LEVELS.keys())}")

        self.logger.setLevel(self.LOG_LEVELS[level_upper])
        self.log_level = self.LOG_LEVELS[level_upper]
        self.info(f"Log level changed to: {level_upper}")
