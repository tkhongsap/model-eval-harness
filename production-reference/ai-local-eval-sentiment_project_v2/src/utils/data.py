"""Pure data-conversion helpers (no I/O)."""


def str_to_bool(value: str) -> bool:
  """Parse a boolean from common truthy/falsy string tokens.

  Case-insensitive. Surrounding whitespace is ignored. Accepted tokens are
  kept narrow on purpose so misconfigurations fail loudly rather than being
  silently coerced.

  Args:
      value: The string to interpret. Truthy tokens: ``yes``, ``true``,
          ``t``, ``y``, ``1``. Falsy tokens: ``no``, ``false``, ``f``,
          ``n``, ``0``.

  Returns:
      ``True`` for truthy tokens, ``False`` for falsy tokens.

  Raises:
      ValueError: If ``value`` is not in the accepted token sets.
  """
  truthy: frozenset[str] = frozenset({"yes", "true", "t", "y", "1"})
  falsy: frozenset[str] = frozenset({"no", "false", "f", "n", "0"})

  token = value.strip().lower()
  if token in truthy:
    return True
  if token in falsy:
    return False
  raise ValueError(f"Invalid boolean value: {value!r}")


def str_to_int(value: str) -> int:
  """Parse a base-10 integer from a string.

  Surrounding whitespace is ignored. Anything ``int`` itself rejects raises,
  so misconfigurations fail loudly at this layer; swallowing the failure into
  a default is the env wrapper's job (``common.get_env_int``).

  Args:
      value: The string to interpret.

  Returns:
      The parsed integer.

  Raises:
      ValueError: If ``value`` is not a base-10 integer.
  """
  return int(value.strip())
