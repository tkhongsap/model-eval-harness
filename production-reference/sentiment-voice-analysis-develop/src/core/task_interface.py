"""
Task Interface Module

Defines the abstract base class for all tasks in the system.
Provides lifecycle hooks and standard methods for task execution.
"""

# Library imports
from abc import ABC, abstractmethod
from typing import Any

# Source code imports
from src.utils.logger import Logger

logger = Logger(__name__)


class TaskInterface(ABC):
    """
    Abstract base class for all task implementations.

    Provides a standard interface and lifecycle hooks for task execution,
    including pre-execution validation, execution, post-execution cleanup,
    and error handling.
    """

    def __init__(self, **kwargs):
        """
        Initialize the task with configuration parameters.

        Args:
            **kwargs: Task-specific configuration parameters
        """
        self.config = kwargs.get("task_param", {})
        self.packages = kwargs.get("packages", {})
        self.task_name = self.__class__.__name__
        self.pre_result = None  # Store result from previous task
        logger.debug(f"Initializing task: {self.task_name}")

    def validate(self) -> bool:
        """
        Validate task configuration and prerequisites before execution.
        Override this method to implement custom validation logic.

        Returns:
            bool: True if validation passes, False otherwise

        Raises:
            ValueError: If critical validation fails
        """
        logger.debug(f"Validating task: {self.task_name}")
        return True

    def pre_execute(self) -> None:
        """
        Hook executed before the main task execution.
        Override this method to implement pre-execution setup logic
        (e.g., resource allocation, connection setup).

        Returns:
            None
        """
        logger.debug(f"Pre-execution hook for task: {self.task_name}")
        pass

    @abstractmethod
    def execute_task(self) -> Any:
        """
        Execute the main task logic. Must be implemented by all subclasses.

        Returns:
            Any: The result of the task execution

        Raises:
            NotImplementedError: If not implemented by subclass
        """
        pass

    def post_execute(self, result: Any) -> Any:
        """
        Hook executed after successful task execution.
        Override this method to implement post-execution logic
        (e.g., result processing, notifications).

        Args:
            result (Any): The result returned by execute_task()

        Returns:
            Any: Processed result (default: returns result unchanged)
        """
        logger.debug(f"Post-execution hook for task: {self.task_name}")
        return result

    def on_error(self, error: Exception) -> None:
        """
        Hook executed when task execution fails.
        Override this method to implement custom error handling
        (e.g., cleanup, notifications, retry logic).

        Args:
            error (Exception): The exception that occurred during execution

        Returns:
            None
        """
        logger.error(f"Error in task '{self.task_name}': {error}", exc_info=True)

    def cleanup(self) -> None:
        """
        Hook executed after task completion (success or failure).
        Override this method to implement cleanup logic
        (e.g., resource deallocation, connection closure).

        Returns:
            None
        """
        logger.debug(f"Cleanup hook for task: {self.task_name}")
        pass

    def run(self, pre_result: Any | None = None) -> Any | None:
        """
        Execute the complete task lifecycle with error handling.
        This method orchestrates: validate -> pre_execute -> execute_task ->
        post_execute -> cleanup (or on_error if failure).

        Returns:
            Optional[Any]: The task execution result, or None if task failed

        Raises:
            Exception: Re-raises exceptions after error handling if not suppressed
        """
        logger.info(f"Starting task execution: {self.task_name}")
        result = None

        try:
            # Validation phase
            logger.debug(f"Validating task: {self.task_name}")
            if not self.validate():
                error_msg = f"Task validation failed: {self.task_name}"
                logger.error(error_msg)
                raise ValueError(error_msg)

            # Pre-execution phase
            logger.debug(f"Running pre-execution for task: {self.task_name}")
            if pre_result is not None:
                logger.debug(f"Storing pre_result from previous task: {self.task_name}")
                self.pre_result = pre_result
            self.pre_execute()

            # Main execution phase
            logger.info(f"Executing task: {self.task_name}")
            result = self.execute_task()

            # Post-execution phase
            logger.debug(f"Running post-execution for task: {self.task_name}")
            result = self.post_execute(result)

            logger.info(f"Task completed successfully: {self.task_name}")
            return result

        except Exception as e:
            logger.error(f"Task '{self.task_name}' failed: {e}", exc_info=True)
            self.on_error(e)
            raise

        finally:
            # Cleanup phase (always runs)
            logger.debug(f"Running cleanup for task: {self.task_name}")
            self.cleanup()

    def get_config(self, key: str, default: Any = None) -> Any:
        """
        Retrieve a configuration value by key.

        Args:
            key (str): The configuration key to retrieve
            default (Any): Default value if key not found. Defaults to None

        Returns:
            Any: The configuration value or default
        """
        return self.config.get(key, default)

    def get_package(self, key: str, default: Any = None) -> Any:
        """
        Retrieve a package value by key.

        Args:
            key (str): The package key to retrieve
            default (Any): Default value if key not found. Defaults to None

        Returns:
            Any: The package value or default
        """
        return self.packages.get(key, default)

    def __str__(self) -> str:
        """String representation of the task."""
        return f"{self.task_name}(config={self.config})"

    def __repr__(self) -> str:
        """Detailed string representation of the task."""
        return f"<{self.task_name} at {hex(id(self))}>"
