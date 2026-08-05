"""
Tests for TaskRegistry class.
"""

import pytest

from src.core.task_interface import TaskInterface
from src.core.task_registry import TaskRegistry, task_registry


class DummyTask(TaskInterface):
    """Dummy task for testing."""

    def execute_task(self):
        return "executed"


class TestTaskRegistry:
    """Test suite for TaskRegistry class."""

    def test_init(self):
        """Test TaskRegistry initialization."""
        registry = TaskRegistry()
        assert registry._tasks == {}

    def test_register_task(self):
        """Test registering a task."""
        registry = TaskRegistry()

        @registry.register("test_task")
        class TestTask(TaskInterface):
            def execute_task(self):
                return "test"

        assert "test_task" in registry._tasks
        assert registry._tasks["test_task"] == TestTask

    def test_register_duplicate_task_raises_error(self):
        """Test that registering duplicate task name raises ValueError."""
        registry = TaskRegistry()

        @registry.register("duplicate_task")
        class Task1(TaskInterface):
            def execute_task(self):
                pass

        with pytest.raises(ValueError, match="Task 'duplicate_task' is already registered"):

            @registry.register("duplicate_task")
            class Task2(TaskInterface):
                def execute_task(self):
                    pass

    def test_get_task(self):
        """Test retrieving a registered task."""
        registry = TaskRegistry()

        @registry.register("retrieve_task")
        class RetrieveTask(TaskInterface):
            def execute_task(self):
                return "retrieved"

        retrieved = registry.get_task("retrieve_task")
        assert retrieved == RetrieveTask

    def test_get_nonexistent_task_raises_error(self):
        """Test that getting non-existent task raises KeyError."""
        registry = TaskRegistry()

        with pytest.raises(KeyError, match="Task 'nonexistent' not found in registry"):
            registry.get_task("nonexistent")

    def test_global_task_registry_exists(self):
        """Test that global task_registry instance exists."""
        assert task_registry is not None
        assert isinstance(task_registry, TaskRegistry)

    def test_register_decorator_returns_class(self):
        """Test that register decorator returns the class unchanged."""
        registry = TaskRegistry()

        @registry.register("decorator_test")
        class DecoratorTask(TaskInterface):
            test_attr = "value"

            def execute_task(self):
                return "decorator"

        assert DecoratorTask.test_attr == "value"
        instance = DecoratorTask()
        assert instance.execute_task() == "decorator"
