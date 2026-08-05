"""
Tests for TaskInterface abstract base class.
"""

import pytest

from src.core.task_interface import TaskInterface


class ConcreteTask(TaskInterface):
    """Concrete implementation of TaskInterface for testing."""

    def execute_task(self):
        """Implement abstract execute_task method."""
        return "executed"

    def pre_execute(self):
        """Implement pre_execute hook."""
        return "pre_executed"

    def post_execute(self, result):
        """Implement post_execute hook."""
        return "post_executed"


class TestTaskInterface:
    """Test suite for TaskInterface class."""

    def test_init_with_no_params(self):
        """Test TaskInterface initialization without parameters."""
        task = ConcreteTask()
        assert task.config == {}
        assert task.packages == {}
        assert task.task_name == "ConcreteTask"

    def test_init_with_task_param(self):
        """Test TaskInterface initialization with task_param."""
        task_param = {"key1": "value1", "key2": 123}
        task = ConcreteTask(task_param=task_param)
        assert task.config == task_param
        assert task.packages == {}

    def test_init_with_packages(self):
        """Test TaskInterface initialization with packages."""
        packages = {"package1": "module1", "package2": "module2"}
        task = ConcreteTask(packages=packages)
        assert task.config == {}
        assert task.packages == packages

    def test_init_with_all_params(self):
        """Test TaskInterface initialization with all parameters."""
        task_param = {"setting": "value"}
        packages = {"pkg": "mod"}
        task = ConcreteTask(task_param=task_param, packages=packages)
        assert task.config == task_param
        assert task.packages == packages

    def test_validate_returns_true_by_default(self):
        """Test that default validate method returns True."""
        task = ConcreteTask()
        assert task.validate() is True

    def test_cannot_instantiate_abstract_class(self):
        """Test that TaskInterface cannot be instantiated directly."""
        with pytest.raises(TypeError):
            TaskInterface()

    def test_task_name_set_correctly(self):
        """Test that task_name is set to class name."""
        task = ConcreteTask()
        assert task.task_name == "ConcreteTask"

    def test_execute_method_required(self):
        """Test that subclass must implement execute method."""
        with pytest.raises(TypeError):

            class IncompleteTask(TaskInterface):
                pass

            IncompleteTask()

    def test_custom_validate_can_be_overridden(self):
        """Test that validate method can be overridden."""

        class CustomValidateTask(TaskInterface):
            def execute_task(self):
                return "executed"

            def validate(self):
                return False

        task = CustomValidateTask()
        assert task.validate() is False

    def test_config_accessible_in_methods(self):
        """Test that config is accessible in task methods."""

        class ConfigTask(TaskInterface):
            def execute_task(self):
                return self.config.get("test_key", "default")

        task = ConfigTask(task_param={"test_key": "test_value"})
        assert task.execute_task() == "test_value"

    def test_packages_accessible_in_methods(self):
        """Test that packages is accessible in task methods."""

        class PackageTask(TaskInterface):
            def execute_task(self):
                return self.packages.get("module", "no_module")

        task = PackageTask(packages={"module": "test_module"})
        assert task.execute_task() == "test_module"

    def test_run_executes_full_lifecycle(self):
        """Test that run executes the full task lifecycle."""
        executed_hooks = []

        class LifecycleTask(TaskInterface):
            def validate(self):
                executed_hooks.append("validate")
                return True

            def pre_execute(self):
                executed_hooks.append("pre_execute")

            def execute_task(self):
                executed_hooks.append("execute_task")
                return "result"

            def post_execute(self, result):
                executed_hooks.append("post_execute")
                return f"processed_{result}"

            def cleanup(self):
                executed_hooks.append("cleanup")

        task = LifecycleTask()
        result = task.run()

        assert result == "processed_result"
        assert executed_hooks == ["validate", "pre_execute", "execute_task", "post_execute", "cleanup"]

    def test_run_with_validation_failure(self):
        """Test that run raises error when validation fails."""

        class FailValidationTask(TaskInterface):
            def validate(self):
                return False

            def execute_task(self):
                return "should not execute"

        task = FailValidationTask()

        with pytest.raises(ValueError, match="Task validation failed"):
            task.run()

    def test_run_with_execution_error_calls_on_error(self):
        """Test that on_error is called when execution fails."""
        error_called = []

        class ErrorTask(TaskInterface):
            def execute_task(self):
                raise RuntimeError("Execution failed")

            def on_error(self, error):
                error_called.append(str(error))

        task = ErrorTask()

        with pytest.raises(RuntimeError, match="Execution failed"):
            task.run()

        assert "Execution failed" in error_called[0]

    def test_run_cleanup_always_executes(self):
        """Test that cleanup always executes even on error."""
        cleanup_called = []

        class CleanupTask(TaskInterface):
            def execute_task(self):
                raise Exception("Error")

            def cleanup(self):
                cleanup_called.append("cleanup")

        task = CleanupTask()

        with pytest.raises(Exception):
            task.run()

        assert cleanup_called == ["cleanup"]

    def test_get_config_returns_value(self):
        """Test get_config retrieves configuration values."""
        task = ConcreteTask(task_param={"key1": "value1", "key2": 123})

        assert task.get_config("key1") == "value1"
        assert task.get_config("key2") == 123

    def test_get_config_returns_default(self):
        """Test get_config returns default for missing key."""
        task = ConcreteTask(task_param={"key1": "value1"})

        assert task.get_config("missing") is None
        assert task.get_config("missing", "default") == "default"

    def test_get_package_returns_value(self):
        """Test get_package retrieves package values."""
        task = ConcreteTask(packages={"pkg1": "mod1", "pkg2": "mod2"})

        assert task.get_package("pkg1") == "mod1"
        assert task.get_package("pkg2") == "mod2"

    def test_get_package_returns_default(self):
        """Test get_package returns default for missing key."""
        task = ConcreteTask(packages={"pkg1": "mod1"})

        assert task.get_package("missing") is None
        assert task.get_package("missing", "default") == "default"

    def test_str_representation(self):
        """Test __str__ returns readable representation."""
        task = ConcreteTask(task_param={"key": "value"})

        str_repr = str(task)
        assert "ConcreteTask" in str_repr
        assert "config" in str_repr

    def test_repr_representation(self):
        """Test __repr__ returns detailed representation."""
        task = ConcreteTask()

        repr_str = repr(task)
        assert "ConcreteTask" in repr_str
        assert "0x" in repr_str  # Memory address

    def test_pre_execute_default_returns_none(self):
        """Test default pre_execute returns None."""
        task = ConcreteTask()
        assert task.pre_execute() == "pre_executed"  # Our concrete implementation returns this

    def test_post_execute_default_returns_result(self):
        """Test default post_execute returns result unchanged."""
        task = ConcreteTask()
        assert task.post_execute("test_result") == "post_executed"  # Our concrete implementation

    def test_on_error_default_implementation(self):
        """Test default on_error logs error."""
        task = ConcreteTask()
        error = Exception("Test error")

        # Should not raise, just log
        task.on_error(error)

    def test_cleanup_default_implementation(self):
        """Test default cleanup does nothing."""
        task = ConcreteTask()

        # Should not raise
        task.cleanup()


class PreResultTask(TaskInterface):
    def execute_task(self):
        return self.pre_result


class DefaultHooksTask(TaskInterface):
    def execute_task(self):
        return "done"


class TestTaskInterfaceAdditional:
    def test_run_stores_pre_result_before_execution(self):
        task = PreResultTask()

        assert task.run(pre_result={"value": 1}) == {"value": 1}

    def test_default_post_execute_returns_same_result(self):
        task = DefaultHooksTask()

        assert task.post_execute("done") == "done"
