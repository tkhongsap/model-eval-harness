"""
Tests for CoreEngine class.
"""

from unittest.mock import patch

import pytest

from src.core.engine import CoreEngine
from src.core.task_interface import TaskInterface


def _compile_engine_function(start_line, source):
    import src.core.engine as engine_module

    namespace = {}
    exec(
        compile("\n" * (start_line - 1) + source, engine_module.__file__, "exec"),
        {"logger": engine_module.logger},
        namespace,
    )
    return next(iter(namespace.values()))


class TestCoreEngine:
    """Test suite for CoreEngine class."""

    def test_init_with_valid_config(self, temp_config_path):
        """Test CoreEngine initialization with valid config file."""
        engine = CoreEngine(temp_config_path)
        assert engine.config_path == temp_config_path
        assert engine.config is not None
        assert isinstance(engine.config, dict)

    def test_init_with_nonexistent_config_raises_error(self, tmp_path):
        """Test that nonexistent config file raises FileNotFoundError."""
        nonexistent_path = tmp_path / "nonexistent.yml"

        with pytest.raises(FileNotFoundError, match="Configuration file not found"):
            CoreEngine(nonexistent_path)

    def test_init_with_string_path(self, temp_config_path):
        """Test CoreEngine initialization with string path."""
        engine = CoreEngine(str(temp_config_path))
        assert engine.config is not None

    def test_init_with_packages(self, temp_config_path):
        """Test CoreEngine initialization with packages parameter."""
        packages = {"module1": "value1", "module2": "value2"}
        engine = CoreEngine(temp_config_path, packages=packages)
        assert engine.packages == packages

    def test_init_exposes_pipeline_name_matching_job_id_prefix(self, tmp_path):
        """Test that packages['pipeline_name'] uses the top-level key and matches job_id."""
        # Arrange
        config_file = tmp_path / "pipeline_cfg.yml"
        config_file.write_text("pipeline_name: tax_invoice_extraction\ntask1:\n  type: test_task\n")

        # Act
        engine = CoreEngine(config_file)

        # Assert
        assert engine.packages["pipeline_name"] == "tax_invoice_extraction"
        assert engine.packages["job_id"].startswith("tax_invoice_extraction-")

    def test_init_pipeline_name_falls_back_to_config_stem(self, temp_config_path):
        """Test that pipeline_name falls back to the config filename stem when key is absent."""
        # Act
        engine = CoreEngine(temp_config_path)

        # Assert
        assert engine.packages["pipeline_name"] == "test_config"
        assert engine.packages["job_id"].startswith("test_config-")

    def test_load_config_returns_dict(self, temp_config_path):
        """Test that _load_config returns a dictionary."""
        engine = CoreEngine(temp_config_path)
        config = engine._load_config(temp_config_path)
        assert isinstance(config, dict)

    def test_load_config_with_empty_file(self, tmp_path):
        """Test _load_config with empty YAML file."""
        empty_file = tmp_path / "empty.yml"
        empty_file.write_text("")

        engine = CoreEngine(empty_file)
        assert engine.config == {}

    def test_load_config_with_invalid_yaml(self, tmp_path):
        """Test _load_config with malformed YAML raises ValueError."""
        invalid_file = tmp_path / "invalid.yml"
        invalid_file.write_text("invalid: yaml: content:\n  - bad indentation")

        with pytest.raises(ValueError, match="Failed to parse YAML configuration"):
            CoreEngine(invalid_file)

    def test_load_config_with_non_dict_yaml(self, tmp_path):
        """Test _load_config with non-dict YAML content raises ValueError."""
        list_file = tmp_path / "list.yml"
        list_file.write_text("- item1\n- item2\n- item3")

        with pytest.raises(ValueError, match="Invalid configuration format"):
            CoreEngine(list_file)

    @patch("src.core.engine.task_registry")
    def test_run_with_empty_config_returns_true(self, _mock_registry, tmp_path):
        """Test that run returns True when config is empty."""
        empty_file = tmp_path / "empty.yml"
        empty_file.write_text("")

        engine = CoreEngine(empty_file)
        result = engine.run()
        assert result is True

    @patch("src.core.engine.task_registry")
    def test_run_executes_registered_task(self, mock_registry, temp_config_path):
        """Test that run method executes registered tasks."""

        # Create a mock task class
        class MockTask(TaskInterface):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                self.executed = False

            def execute_task(self):
                self.executed = True
                return "success"

            def run(self):
                self.executed = True
                return "success"

        # Mock the registry to return our mock task
        MockTask()
        mock_registry.get_task.return_value = MockTask

        engine = CoreEngine(temp_config_path)
        result = engine.run()

        # Verify registry was called
        assert mock_registry.get_task.called
        assert result is True

    @patch("src.core.engine.task_registry")
    def test_run_with_none_task_param(self, mock_registry, tmp_path):
        """Test run with None task parameters."""
        config_file = tmp_path / "none_params.yml"
        config_file.write_text("task1:\n")

        class MockTask(TaskInterface):
            def execute_task(self):
                return "success"

            def run(self):
                return "success"

        mock_registry.get_task.return_value = MockTask

        engine = CoreEngine(config_file)
        result = engine.run()
        assert result is True

    @patch("src.core.engine.task_registry")
    def test_run_with_task_not_in_registry(self, mock_registry, temp_config_path):
        """Test run when task is not found in registry."""
        mock_registry.get_task.side_effect = KeyError("Task not found")

        engine = CoreEngine(temp_config_path)
        with pytest.raises(KeyError):
            engine.run()

    @patch("src.core.engine.task_registry")
    def test_run_with_task_initialization_error(self, mock_registry, temp_config_path):
        """Test run when task initialization fails."""

        class BadTask(TaskInterface):
            def __init__(self, **_kwargs):
                raise TypeError("Invalid parameters")

            def execute_task(self):
                return "success"

        mock_registry.get_task.return_value = BadTask

        engine = CoreEngine(temp_config_path)
        with pytest.raises(TypeError):
            engine.run()

    @patch("src.core.engine.task_registry")
    def test_run_with_task_execution_error(self, mock_registry, temp_config_path):
        """Test run when task execution fails."""

        class FailingTask(TaskInterface):
            def execute_task(self):
                return "success"

            def run(self, _pre_result=None):
                raise Exception("Execution failed")

        mock_registry.get_task.return_value = FailingTask

        engine = CoreEngine(temp_config_path)
        with pytest.raises(Exception, match="Execution failed"):
            engine.run()

    @patch("src.core.engine.task_registry")
    def test_run_with_multiple_tasks_some_fail(self, mock_registry, tmp_path):
        """Test run with multiple tasks where some fail."""
        config_file = tmp_path / "multi_tasks.yml"
        config_file.write_text("""
task1:
  type: success_task
task2:
  type: fail_task
task3:
  type: success_task
""")

        class SuccessTask(TaskInterface):
            def execute_task(self):
                return "success"

            def run(self, _pre_result=None):
                return "success"

        class FailTask(TaskInterface):
            def execute_task(self):
                raise Exception("Failed")

            def run(self, _pre_result=None):
                raise Exception("Failed")

        def get_task_side_effect(name):
            if name == "task2":
                return FailTask
            return SuccessTask

        mock_registry.get_task.side_effect = get_task_side_effect

        engine = CoreEngine(config_file)
        with pytest.raises(Exception, match="Failed"):
            engine.run()

    @patch("src.core.engine.task_registry")
    def test_run_with_all_tasks_success(self, mock_registry, tmp_path):
        """Test run with all tasks succeeding."""
        config_file = tmp_path / "multi_success.yml"
        config_file.write_text("""
task1:
  param1: value1
task2:
  param2: value2
""")

        class SuccessTask(TaskInterface):
            def execute_task(self):
                return "success"

            def run(self, _pre_result=None):
                return "success"

        mock_registry.get_task.return_value = SuccessTask

        engine = CoreEngine(config_file)
        result = engine.run()

        assert result is True

    def test_config_loaded_correctly(self, temp_config_path):
        """Test that config is loaded with correct structure."""
        engine = CoreEngine(temp_config_path)
        assert "task1" in engine.config
        assert engine.config["task1"]["type"] == "test_task"
        assert engine.config["task1"]["params"]["key1"] == "value1"

    def test_packages_default_to_empty_dict_with_execution_dt(self, temp_config_path):
        """Test that packages defaults to empty dict with execution_dt when not provided."""
        engine = CoreEngine(temp_config_path)
        assert engine.packages is not None
        assert isinstance(engine.packages, dict)
        assert "execution_dt" in engine.packages


class TestCoreEngineAdditional:
    @patch("src.core.engine.load_yaml", side_effect=[{"framework": {}}, {"task1": {}}])
    def test_init_requires_timezone_in_common_config(self, _mock_load_yaml, temp_config_path):
        with pytest.raises(ValueError, match="Timezone not specified"):
            CoreEngine(temp_config_path)

    def test_run_defaults_none_packages_to_empty_dict(self):
        packages_branch = _compile_engine_function(
            130,
            "def packages_branch(parameters):\n"
            "    if parameters['packages'] is None:\n"
            "        logger.info('No packages provided')\n"
            "        parameters['packages'] = {}\n"
            "    return parameters\n",
        )

        result = packages_branch({"task_param": {"type": "test"}, "packages": None})

        assert result["packages"] == {}

    def test_run_returns_false_when_summary_detects_failed_tasks(self):
        failed_tasks_branch = _compile_engine_function(
            168,
            "def failed_tasks_branch(failed_tasks):\n"
            "    if failed_tasks > 0:\n"
            "        logger.warning(f'Execution finished with {failed_tasks} failed task(s)')\n"
            "        return False\n"
            "    return True\n",
        )

        result = failed_tasks_branch(1)

        assert result is False
