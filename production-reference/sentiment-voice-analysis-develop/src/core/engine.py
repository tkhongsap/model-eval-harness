# Library imports
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

import tasks  # noqa: F401

# Source code imports
from src.core.task_registry import task_registry
from src.utils.common import (
    get_value_by_path,
)
from src.utils.file_utils import (
    load_yaml,
)
from src.utils.logger import Logger

logger = Logger(__name__)


class CoreEngine:
    """
    Core engine to load configuration and execute tasks.
    Handles task orchestration, error handling, and execution logging.
    """

    COMMON_CONFIG_PATH = Path("config/common.yml")
    RESERVED_CONFIG_KEYS = frozenset({"pipeline_name"})

    def __init__(self, config_path: str | Path, packages: dict[str, Any] = None):
        """
        Initialize the core engine with a configuration file.

        Args:
            config_path (str | Path): Path to the YAML configuration file.
            package (Dict[str, Any], optional): Additional parameters for tasks. Defaults to None.

        Raises:
            FileNotFoundError: If the configuration file doesn't exist.
            ValueError: If the configuration is invalid.
        """
        logger.info(f"Initializing CoreEngine with config: {config_path}")

        common_config = load_yaml(self.COMMON_CONFIG_PATH)
        timezone = get_value_by_path(common_config, "framework.timezone")
        if not timezone:
            raise ValueError(f"Timezone not specified in common configuration file {self.COMMON_CONFIG_PATH}")
        execution_dt = datetime.now(tz=ZoneInfo(timezone))
        self.packages = packages if packages is not None else {}
        self.packages["execution_dt"] = execution_dt
        logger.debug(f"Packages provided to CoreEngine: {self.packages}")

        self.config_path = config_path
        self.config = self._load_config(config_path)

        pipeline_name = self.config.get("pipeline_name") or Path(config_path).stem
        self.packages["pipeline_name"] = pipeline_name

        job_id = self._generate_job_id(pipeline_name, execution_dt)
        self.packages["job_id"] = job_id
        logger.info(f"CoreEngine initialized successfully. Found {len(self.config)} task(s) in configuration")

    def _load_config(self, path: str | Path) -> dict[str, Any]:
        """
        Load configuration from a YAML file with validation.

        Args:
            path (str | Path): The path to the YAML configuration file.

        Returns:
            dict: The loaded configuration.

        Raises:
            FileNotFoundError: If the configuration file doesn't exist.
            ValueError: If the YAML is malformed or empty.
        """
        if isinstance(path, str):
            path = Path(path)

        if not path.exists():
            logger.error(f"Configuration file not found: {path}")
            raise FileNotFoundError(f"Configuration file not found: {path}")

        logger.debug(f"Loading configuration from: {path}")

        try:
            config = load_yaml(path)

            if not config:
                logger.warning(f"Configuration file is empty: {path}")
                return {}

            if not isinstance(config, dict):
                logger.error(f"Invalid configuration format. Expected dict, got {type(config)}")
                raise ValueError(f"Invalid configuration format. Expected dict, got {type(config)}")

            logger.debug(f"Configuration loaded successfully with {len(config)} task(s)")
            return config

        except yaml.YAMLError as e:
            logger.error(f"Failed to parse YAML configuration: {e}")
            raise ValueError(f"Failed to parse YAML configuration: {e}") from e
        except Exception as e:
            logger.error(f"Unexpected error loading configuration: {e}")
            raise

    def _generate_job_id(self, pipeline_name: str, execution_dt: datetime) -> str:
        try:
            data_part = execution_dt.strftime("%Y%m%d")
            time_part = execution_dt.strftime("%H%M%S")
            rand_part = uuid.uuid4().hex[:8]
            return f"{pipeline_name}-{data_part}-{time_part}-{rand_part}"
        except Exception as e:
            logger.error(f"Failed to generate job ID: {e}")
            raise

    def run(self) -> bool:
        """
        Execute all tasks as per the loaded configuration.

        Returns:
            bool: True if all tasks completed successfully, False otherwise.
        """
        if not self.config:
            logger.warning("No tasks found in configuration. Nothing to execute.")
            return True

        task_list = [k for k in self.config if k not in self.RESERVED_CONFIG_KEYS]
        total_tasks = len(task_list)
        logger.info(f"Starting execution of {total_tasks} task(s)")
        logger.info(f"Execution started at: {self.packages.get('execution_dt')}")

        pre_result = None
        successful_tasks = 0

        for index, task_name in enumerate(task_list, 1):
            logger.info(f"[{index}/{total_tasks}] Executing task: '{task_name}'")

            try:
                parameters = {"task_param": self.config[task_name], "packages": self.packages}
                # task_params = self.config[task_name]

                # Validate task parameters
                if parameters["task_param"] is None:
                    logger.warning(f"Task '{task_name}' has no parameters, using empty dict")
                    parameters["task_param"] = {}

                if parameters["packages"] is None:
                    logger.info("No packages provided")
                    parameters["packages"] = {}

                # Get task class from registry
                task_class = task_registry.get_task(task_name)

                logger.debug(
                    f"Initializing task '{task_name}' with task parameters: {parameters['task_param']} and packages: {parameters['packages']}"  # noqa: E501
                )

                # Initialize and execute task
                task_instance = task_class(**parameters)
                if pre_result is not None:
                    logger.info(f"Running task '{task_name}' with pre_result from previous task")
                    pre_result = task_instance.run(pre_result)
                else:
                    logger.info(f"Running task '{task_name}' without pre_result")
                    pre_result = task_instance.run()

                logger.info(f"Task '{task_name}' completed successfully")
                successful_tasks += 1

            except KeyError as e:
                logger.error(f"Task '{task_name}' not found in registry: {e}")
                raise
                # Don't raise, continue to next task
            except TypeError as e:
                logger.error(f"Task '{task_name}' failed - Invalid parameters: {e}")
                raise
                # Don't raise, continue to next task
            except Exception as e:
                logger.error(f"Task '{task_name}' failed with error: {e}", exc_info=True)
                raise
                # Don't raise, continue to next task

        # Summary logging
        failed_tasks = total_tasks - successful_tasks
        logger.info(
            f"Execution completed: {successful_tasks} succeeded, {failed_tasks} failed out of {total_tasks} total"
        )

        if failed_tasks > 0:
            logger.warning(f"Execution finished with {failed_tasks} failed task(s)")
            return False

        logger.info("All tasks completed successfully")
        return True
