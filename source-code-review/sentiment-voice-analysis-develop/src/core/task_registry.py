class TaskRegistry:
    """
    A registry to hold and manage task classes.
    """

    def __init__(self):
        self._tasks = {}

    def register(self, task_name: str) -> callable:
        """
        Decorator to register a task class with a given name.
        Args:
            task_name (str): The name to register the task under.
        Returns:
            function: The decorator function.
        """

        def wrapper(cls):
            if task_name in self._tasks:
                raise ValueError(f"Task '{task_name}' is already registered.")
            self._tasks[task_name] = cls
            return cls

        return wrapper

    def get_task(self, task_name: str) -> type:
        """
        Retrieve a task class by its registered name.
        Args:
            task_name (str): The name of the task to retrieve.
        Returns:
            class: The task class associated with the given name.
        Raises:
            KeyError: If the task name is not found in the registry.
        """
        if task_name not in self._tasks:
            raise KeyError(f"Task '{task_name}' not found in registry.")
        return self._tasks[task_name]


task_registry = TaskRegistry()
