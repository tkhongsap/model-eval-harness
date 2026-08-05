# Library imports
import os
import re
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any, Literal

from src.utils.date_utils import add_months

# Source code imports
from src.utils.logger import Logger

logger = Logger(__name__)


def resolve_date(text: str, replace_date: datetime | str) -> str:
    """
    Replace date in the format %{DATA_DATE[_±N[DMY]][_FORMAT]} within the input text.
    The placeholder can include an optional offset (±N days, months, or years) and a date format.
    FORMAT supports:
        - YYYY for year
        - MM for month
        - DD for day
        - HH for hour
        - MM or mm for minute
        - SS for second
    Parameters:
        text (str): The input text containing date placeholders.
        replace_date (datetime | str): The base date to use for replacements. If a string is provided,
            it should be in the format "YYYYMMDD" or "YYYY-MM-DD".
    Returns:
        str: The text with date placeholders replaced by formatted dates.
    """

    # Ensure replace_date is datetime
    if isinstance(replace_date, str):
        parsed = False
        for fmt in ["%Y%m%d", "%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y%m%d%H%M%S"]:
            try:
                replace_date = datetime.strptime(replace_date, fmt)
                parsed = True
                break
            except ValueError:
                continue

        if not parsed:
            raise ValueError(
                "replace_date must be date or datetime format: YYYY-MM-DD, YYYYMMDD, or YYYY-MM-DD HH:MM:SS"
            )

    # Updated regex to allow characters like - in the format part
    pattern = r"%\{DATA_DATE([_\-][+-]?\d+[DMY])?([_\-].+?)\}"
    matches = list(re.finditer(pattern, text))
    for match in matches:
        full_match = match.group(0)
        offset_part = match.group(1)
        format_part = match.group(2)

        current_date = replace_date

        if offset_part:
            # Remove separator
            offset_str = offset_part[1:]
            # Parse value and unit
            match_offset = re.match(r"([+-]?\d+)([DMY])", offset_str)
            if match_offset:
                val = int(match_offset.group(1))
                unit = match_offset.group(2)

                if unit == "D":
                    current_date = current_date + timedelta(days=val)
                elif unit == "M":
                    current_date = add_months(current_date, val)
                elif unit == "Y":
                    current_date = current_date.replace(year=current_date.year + val)

        # Format
        fmt_str = format_part[1:]  # Remove separator

        # Handle specific time formats where MM might be used for minutes
        # Prioritize longer matches to correctly identify minutes
        # YYYYMMDDHHMMSS -> %Y%m%d%H%M%S
        # YYYYMMDD-HH:MM:SS -> %Y%m%d-%H:%M:%S
        # YYYYMMDD HHMMSS -> %Y%m%d %H%M%S
        # YYYY-MM-DD-HH-MM-SS -> %Y-%m-%d-%H-%M-%S (New!)
        # Explicit minutes via mm -> %M (e.g., YYYYMMDDHHmm)
        fmt_str = fmt_str.replace("HH:MM:SS", "%H:%M:%S").replace("HHMMSS", "%H%M%S").replace("HH-MM-SS", "%H-%M-%S")
        fmt_str = fmt_str.replace("HH:MM", "%H:%M").replace("HHMM", "%H%M").replace("HH-MM", "%H-%M")
        fmt_str = fmt_str.replace("MM:SS", "%M:%S").replace("MMSS", "%M%S").replace("MM-SS", "%M-%S")
        fmt_str = fmt_str.replace("HH", "%H")
        # Map custom format to strftime
        strftime_fmt = fmt_str.replace("YYYY", "%Y").replace("MM", "%m").replace("DD", "%d")
        strftime_fmt = strftime_fmt.replace("mm", "%M").replace("SS", "%S")
        formatted_date = current_date.strftime(strftime_fmt)
        text = text.replace(full_match, formatted_date)

    return text


def resolve_env(text: str | None) -> str | None:
    """
    Replace environment variable placeholders in the format ${ENV_VAR_NAME} within the input text.
    Parameters:
        text (str | None): The input text containing environment variable placeholders.
    Returns:
        str | None: The text with placeholders replaced; None passes through unchanged so callers
            can distinguish "missing config key" from "empty string after substitution".
    """
    if text is None:
        return None
    pattern = r"\$\{([A-Z0-9_]+)\}"

    def replace_match(match):
        var_name = match.group(1)
        return os.environ.get(var_name, "")

    return re.sub(pattern, replace_match, text)


def recursive_dict_value_by_key(data: dict, target_key: str) -> list:
    """
    Recursively search a dictionary for all values associated with a specific key.
    Parameters:
        data (dict): The dictionary to search.
        target_key (str): The key whose values are to be retrieved.
    Returns:
        list: A list of all values associated with the target key.
    """
    found_values = []

    def _search(d):
        if isinstance(d, dict):
            for k, v in d.items():
                if k == target_key:
                    found_values.append(v)
                _search(v)
        elif isinstance(d, list):
            for item in d:
                _search(item)

    _search(data)
    return found_values


def is_key_in_dict(data: dict, target_key: str) -> bool:
    """
    Check if a specific key exists anywhere in a nested dictionary.
    Parameters:
        data (dict): The dictionary to search.
        target_key (str): The key to check for existence.
    Returns:
        bool: True if the key exists, False otherwise.
    """
    if isinstance(data, dict):
        if target_key in data:
            return True
        return any(is_key_in_dict(v, target_key) for v in data.values())
    if isinstance(data, list):
        return any(is_key_in_dict(item, target_key) for item in data)
    return False


def get_value_by_path(data: dict, path: str, default=None):
    """
    Retrieve a value from a nested dictionary using a dot-separated path.
    Parameters:
        data (dict): The dictionary to search.
        path (str): The dot-separated path to the value (e.g., "a.b.c").
        default (Any): The default value to return if the path is not found.
    Returns:
        Any: The value found at the path, or the default value.
    """
    keys = path.split(".")
    value = data
    try:
        for key in keys:
            if isinstance(value, dict):
                value = value.get(key)
            elif isinstance(value, list) and key.isdigit():
                try:
                    value = value[int(key)]
                except IndexError:
                    return default
            else:
                return default
            if value is None:
                return default
        return value
    except Exception:
        return default


def percentage_format(value: str | float | int, precision: int = 2) -> str:
    """
    Convert a numeric value to a percentage string with specified precision.
    Parameters:
        value (str|float|int): The numeric value to convert.
        precision (int): The number of decimal places in the output string, default is 2.
    Returns:
        str: The formatted percentage string.
    """
    if isinstance(value, str):
        value = float(value)
    return f"{value * 100:.{precision}f}"


def safe_list_get(list_name: list, index: int, default_value: Any = None) -> Any:
    """
    Safely retrieves an element from a list by index, returning a default value if the index is out of range.
    Parameters:
        - list_name (list): The list from which to retrieve the element.
        - index (int): The index of the element to retrieve.
        - default_value (Any): The value to return if the index is out of range. Default is None.
    Returns:
        - The element at the specified index, or the default value if the index is invalid.
    """
    try:
        return list_name[index]
    except IndexError:
        return default_value


def safe_list_get_slicing(
    list_name: list, start: int = None, end: int = None, step: int = None, default_value: Any = None
) -> Any:
    """
    Safely retrieves a slice from a list, returning a default value if the slice is out of range.
    Parameters:
        - list_name (list): The list from which to retrieve the slice.
        - start (int): The starting index of the slice. Default is None.
        - end (int): The ending index of the slice. Default is None.
        - step (int): The step of the slice. Default is None.
        - default_value (Any): The value to return if the slice is out of range. Default is None.
    Returns:
        - The sliced list, or the default value if the slice is invalid.
    """
    try:
        return list_name[start:end:step]
    except IndexError:
        return default_value


def flatten_dict_without_change_key(nested_dict: dict) -> dict:
    """
    Flatten a nested dictionary without changing the keys.

    Parameters:
        nested_dict (dict): The nested dictionary to flatten.

    Returns:
        dict: The flattened dictionary.

    Raises:
        TypeError: If the input is not a dictionary.
        KeyError: If there are key conflicts during flattening.
        ValueError: If the dictionary structure is invalid or contains circular references.
    """
    if not isinstance(nested_dict, dict):
        logger.error(f"Input must be a dictionary, got {type(nested_dict).__name__}")
        raise TypeError(f"Expected dict, got {type(nested_dict).__name__}")

    if not nested_dict:
        logger.warning("Empty dictionary provided for flattening")
        return {}

    flattened = {}
    visited = set()

    def _flatten(current: Any, path: str = "root"):
        # Circular reference detection
        obj_id = id(current)
        if obj_id in visited:
            logger.error(f"Circular reference detected at path: {path}")
            raise ValueError(f"Circular reference detected at path: {path}")

        if not isinstance(current, dict):
            return

        visited.add(obj_id)

        try:
            for key, value in current.items():
                # Validate key is hashable
                if not isinstance(key, (str, int, float, bool, tuple)):
                    logger.error(f"Invalid key type at path {path}: {type(key).__name__}")
                    raise TypeError(f"Dictionary keys must be hashable. Found {type(key).__name__} at path: {path}")

                # Check for key conflicts
                if key in flattened:
                    logger.error(f"Key conflict during flattening: '{key}' at path {path}")
                    raise KeyError(f"Duplicate key '{key}' found at path: {path}")

                # Recursively flatten nested dictionaries
                if isinstance(value, dict):
                    _flatten(value, f"{path}.{key}")
                else:
                    flattened[key] = value
        except AttributeError as e:
            logger.error(f"AttributeError at path {path}: {e}", exc_info=True)
            raise ValueError(f"Invalid dictionary structure at path: {path}") from e
        finally:
            visited.discard(obj_id)

    try:
        _flatten(nested_dict)
        logger.debug(f"Successfully flattened dictionary with {len(flattened)} keys")
        return flattened
    except (KeyError, TypeError, ValueError) as e:
        logger.error(f"Error flattening dictionary: {e}", exc_info=True)
        raise
    except Exception as e:
        logger.error(f"Unexpected error during dictionary flattening: {e}", exc_info=True)
        raise RuntimeError(f"Unexpected error during flattening: {str(e)}") from e


def safe_cast_value(value: Any, to_type: type, default: Any = None) -> Any:
    """
    Safely cast a value to a specified type, returning a default value if casting fails.

    Parameters:
        value (Any): The value to be cast.
        to_type (type): The target type to cast the value to.
        default (Any): The default value to return if casting fails. Default is None.

    Returns:
        Any: The cast value if successful, otherwise the default value.
    """
    try:
        return to_type(value)
    except (ValueError, TypeError) as e:
        logger.warning(f"Failed to cast value '{value}' to {to_type.__name__}: {e}")
        return default


def force_none_value_dict(dict_obj: dict, exclude_keys: list[str] = None) -> dict:
    """
    Recursively set all values in a dictionary to None, empty list, or empty dict, while
    keeping the same keys. If exclude_keys is provided, only those keys will be left
    unchanged, while others will be set to None.

    Parameters:
        dict_obj (dict): The input dictionary to modify.
        exclude_keys (list[str], optional): A list of keys to exclude from the
            modification. If None, all keys will be modified. Default is None.
    Returns:
        dict: A new dictionary with values set to None.
    Raises:
        TypeError: If dict_obj is not a dictionary or if exclude_keys is not a list of strings.
        RuntimeError: If an unexpected error occurs during processing.
    """
    if not isinstance(dict_obj, dict):
        logger.error(f"Input must be a dictionary, got {type(dict_obj).__name__}")
        raise TypeError(f"Expected dict, got {type(dict_obj).__name__}")

    if exclude_keys is not None and not isinstance(exclude_keys, list):
        logger.error(f"exclude_keys must be a list of strings, got {type(exclude_keys).__name__}")
        raise TypeError(f"Expected exclude_keys to be a list of strings, got {type(exclude_keys).__name__}")

    def recursive_force(d: dict, path: str = "root") -> dict:
        if not isinstance(d, dict):
            return d

        forced_dict = {}
        for key, val in d.items():
            current_path = f"{path}.{key}"
            if exclude_keys is None:
                # Force all keys to None
                if isinstance(val, dict):
                    forced_dict[key] = recursive_force(val, current_path)
                elif isinstance(val, list):
                    # Create a new list instead of modifying in place
                    new_list = []
                    for index, item in enumerate(val):
                        item_path = f"{current_path}[{index}]"
                        if isinstance(item, dict):
                            new_list.append(recursive_force(item, item_path))
                        else:
                            new_list.append(None)
                    if all(v is None for v in new_list):
                        forced_dict[key] = []
                    else:
                        forced_dict[key] = new_list
                else:
                    forced_dict[key] = None
            else:
                if isinstance(val, dict):
                    forced_dict[key] = recursive_force(val, current_path)
                elif isinstance(val, list) and key not in exclude_keys:
                    new_list = []
                    for index, item in enumerate(val):
                        item_path = f"{current_path}[{index}]"
                        if isinstance(item, dict):
                            new_list.append(recursive_force(item, item_path))
                        else:
                            new_list.append(None)
                    if all(v is None for v in new_list):
                        forced_dict[key] = []
                    else:
                        forced_dict[key] = new_list
                elif key not in exclude_keys:
                    forced_dict[key] = None
                else:
                    forced_dict[key] = val

        return forced_dict

    try:
        result = recursive_force(dict_obj)
        logger.debug(f"Successfully forced values in dictionary with {len(result)} keys")
        return result
    except Exception as e:
        logger.error(f"Unexpected error during force_value_dict: {e}", exc_info=True)
        raise RuntimeError(f"Unexpected error during force_value_dict: {str(e)}") from e


def logging_ai_operation(
    log_instance: Logger,
    log_obj: dict,
    log_type: Literal["batch", "fact_check"],
    message: str = "",
) -> None:
    """
    Log AI operation details in a structured format.

    schema for batch log_type:
        - process_date: The date of batch execution (start date).
        - environment: The environment in which the process ran (e.g., production, non-production).
        - project_id: The unique identifier for the project.
        - project_type: The type or category of the project.
        - total_transaction: The total number of transactions processed.
        - total_success_transaction: The number of transactions that were successful.
        - total_failed_transaction: The number of transactions that failed.
        - average_response_time_sec: The average latency in seconds for the transactions processed.
        - total_runtime_sec: The total runtime in seconds for the whole process date.

    schema for fact_check log_type (per-field extraction-accuracy summary):
        - created_datetime: The run start datetime.
        - processed_datetime: The Gemini prediction processed time (from the batch response).
        - gcp_project_id: The unique identifier for the project.
        - label: The evaluated field name (or "overall" for the aggregate row).
        - accuracy: The accuracy percentage for the label.
        - precision: The precision percentage for the label.
        - recall: The recall percentage for the label.
        - f1_score: The F1 score percentage for the label.

    Parameters:
        log_instance (Logger): The logger instance to use for logging.
        log_obj (dict): The dictionary containing log details.
        log_type (Literal['batch', 'fact_check']): The type of log to emit.
        message (str): An optional message to include in the log.
    Raises:
        ValueError: If log_type is unsupported or if log_obj is not a dictionary.
        KeyError: If required keys are missing in log_obj for the specified log_type.
    """
    # Structured field order per AI-operation log type. Each emitted log carries exactly these
    # keys (in this order) under the JSON ``data`` payload; a missing key is a hard error so an
    # under-populated summary never ships silently.
    ai_operation_log_schemas: dict[str, list[str]] = {
        "batch": [
            "process_date",
            "environment",
            "project_id",
            "project_type",
            "total_transaction",
            "total_success_transaction",
            "total_failed_transaction",
            "average_response_time_sec",
            "total_runtime_sec",
        ],
        "fact_check": [
            "created_datetime",
            "processed_datetime",
            "gcp_project_id",
            "label",
            "accuracy",
            "precision",
            "recall",
            "f1_score",
        ],
    }
    schema = ai_operation_log_schemas.get(log_type)
    if schema is None:
        supported = ", ".join(sorted(ai_operation_log_schemas))
        logger.error(f"Invalid log_type: {log_type}. Expected one of: {supported}.")
        raise ValueError(f"Invalid log_type: {log_type}. Expected one of: {supported}.")

    if not isinstance(log_obj, dict):
        logger.error(f"Invalid log_obj: {log_obj}. Expected a dictionary.")
        raise TypeError(f"Invalid log_obj: {log_obj}. Expected a dictionary.")

    log_dict = {}
    for key in schema:
        if not is_key_in_dict(log_obj, key):
            logger.error(f"Missing key in log_obj: {key}")
            raise KeyError(f"Missing key in log_obj: {key}")

        values = recursive_dict_value_by_key(log_obj, key)
        log_dict[key] = values[0] if values else None

    log_instance.info(message, extra={"json_payload": log_dict})


def missing_string_errors(config: dict[str, Any], dotted_keys: Sequence[str]) -> list[str]:
    """Return one error string per dotted key that is missing, blank, or not a string.

    A pure config-validation collector shared by task ``validate()`` implementations.
    Placeholders (``${VAR}`` / ``%{DATA_DATE}``) are validated *unresolved* — only presence
    and string-typing are checked here; resolution happens later at runtime.

    Args:
        config: The task's raw config dict.
        dotted_keys: Dotted paths (e.g. ``"gcs.landing_path"``) that must each resolve to a
            non-empty string.

    Returns:
        One human-readable error string per offending key (empty list when all valid).
    """
    errors = []
    for dotted in dotted_keys:
        value = get_value_by_path(config, dotted)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"'{dotted}' is missing or not a non-empty string (got: {value!r})")
    return errors


def int_castable_errors(config: dict[str, Any], dotted_keys: Sequence[str], *, required: bool) -> list[str]:
    """Return one error string per dotted key whose env-resolved value is not int-castable.

    A pure config-validation collector shared by task ``validate()`` implementations.

    Args:
        config: The task's raw config dict.
        dotted_keys: Dotted paths whose value must cast to ``int`` (after env resolution).
        required: When True, a missing key is an error; when False, missing keys are skipped.

    Returns:
        One human-readable error string per offending key (empty list when all valid).
    """
    errors = []
    for dotted in dotted_keys:
        value = get_value_by_path(config, dotted)
        if value is None:
            if required:
                errors.append(f"'{dotted}' is missing")
            continue
        try:
            int(resolve_env(value) if isinstance(value, str) else value)
        except (TypeError, ValueError):
            errors.append(f"'{dotted}' must be castable to int (got: {value!r})")
    return errors
