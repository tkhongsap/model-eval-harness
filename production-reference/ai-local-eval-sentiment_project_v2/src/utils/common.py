"""Environment-variable helpers with typed defaults."""

import os

from src.utils.data import str_to_bool, str_to_int


def get_env(name: str, default: str | None = None) -> str | None:
  """Read an environment variable with whitespace stripped.

  Args:
      name: The environment variable name.
      default: Value to return if the variable is unset. Defaults to ``None``.

  Returns:
      The stripped value of the environment variable, or ``default`` if the
      variable is not present in ``os.environ``.
  """
  value = os.environ.get(name)
  if value is None:
    return default
  return value.strip()


def get_env_bool(name: str, default: bool) -> bool:
  """Read an environment variable and parse it as a boolean.

  Unparseable values are tolerated (the function returns ``default``) so a
  typo in an env var does not crash service startup. The accepted token
  sets are defined by :func:`src.utils.data.str_to_bool`.

  Args:
      name: The environment variable name.
      default: Fallback when the variable is unset or its value cannot be
          parsed as a boolean.

  Returns:
      The parsed boolean value, or ``default`` if the variable is unset or
      the value is not in the accepted token sets.
  """
  raw = os.environ.get(name)
  if raw is None:
    return default
  try:
    return str_to_bool(raw)
  except ValueError:
    return default


def get_env_int(name: str, default: int) -> int:
  """Read an environment variable and parse it as an integer.

  Unparseable values are tolerated (the function returns ``default``) so a
  typo in an env var does not crash service startup. Parsing is defined by
  :func:`src.utils.data.str_to_int`.

  Args:
      name: The environment variable name.
      default: Fallback when the variable is unset or its value cannot be
          parsed as an integer.

  Returns:
      The parsed integer value, or ``default`` if the variable is unset or
      unparseable.
  """
  raw = os.environ.get(name)
  if raw is None:
    return default
  try:
    return str_to_int(raw)
  except ValueError:
    return default
