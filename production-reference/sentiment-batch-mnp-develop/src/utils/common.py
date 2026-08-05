import io, yaml, copy, re, os
from typing import Any
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path

def read_file(file_path: str|Path, encoding: str = 'utf-8') -> str:
    """
    Read a file from the given path and return its contents as a string.
    Args:
        file_path (str|Path): The path to the file.
        encoding (str): The file encoding, default is 'utf-8'.
    Returns:
        str: The contents of the file as a string.
    """
    if isinstance(file_path, str):
        file_path = Path(file_path)
    with open(file_path, 'r', encoding=encoding) as file:
        content = file.read()
    return content

def load_yaml(file_path: str|Path, encoding: str = 'utf-8') -> dict:
    """
    Load a YAML file from path and return its contents as a dictionary.
    Args:
        file_path (str|Path): The path to the YAML file.
        encoding (str): The file encoding, default is 'utf-8'.
    Returns:
        dict: The contents of the YAML file as a dictionary.
    """
    if isinstance(file_path, str):
        file_path = Path(file_path)
    with open(file_path, 'r', encoding=encoding) as file:
        yaml_dict = yaml.safe_load(file)
    return yaml_dict

def load_yaml_string(yaml_string: str) -> dict:
    """
    Load a YAML string and return its contents as a dictionary.
    Args:
        yaml_string (str): The YAML formatted string.
    Returns:
        dict: The contents of the YAML string as a dictionary.
    """
    yaml_dict = yaml.safe_load(yaml_string)
    return yaml_dict

def pydantic_resolve_refs(schema: dict) -> dict:
    """
    Resolve $ref references in a Pydantic JSON schema
    Parameters:
        schema (dict): The JSON schema with potential $ref references.
    Returns:
        dict: The JSON schema with all $ref references resolved.
    """
    schema = copy.deepcopy(schema)
    defs = schema.get("$defs", {})

    def _resolve(node):
        if isinstance(node, dict):
            # If this node is a $ref, inline the referenced definition
            if "$ref" in node:
                ref = node["$ref"]
                if isinstance(ref, str) and ref.startswith("#/$defs/"):
                    key = ref.split("/")[-1]
                    target = defs.get(key)
                    if target is None:
                        return node
                    return _resolve(copy.deepcopy(target))
                return node

            # Otherwise recursively resolve children
            return {k: _resolve(v) for k, v in node.items() if k != "$defs"}
        if isinstance(node, list):
            return [_resolve(i) for i in node]
        return node

    resolved = _resolve(schema)
    if isinstance(resolved, dict) and "$defs" in resolved:
        resolved.pop("$defs", None)
    return resolved

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
    keys = path.split('.')
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

def export_pandas_df_to_markdown(df: pd.DataFrame, file_path: str|Path, encoding: str = 'utf-8') -> None:
    """
    Export a pandas DataFrame to a markdown file.
    Parameters:
        df (pd.DataFrame): The DataFrame to export.
        file_path (str|Path): The path to the output markdown file.
        encoding (str): The file encoding, default is 'utf-8'.
    Returns:
        None
    """
    if isinstance(file_path, str):
        file_path = Path(file_path)
    if not file_path.parent.exists():
        file_path.parent.mkdir(parents=True, exist_ok=True)
    with open(file_path, 'w', encoding=encoding) as f:
        f.write(df.to_markdown(index=False))

def convert_df_to_excel(df: pd.DataFrame) -> bytes:
    """
    Convert a pandas DataFrame to Excel format and return as bytes.
    Parameters:
        df (pd.DataFrame): The DataFrame to convert.
    Returns:
        bytes: The Excel file content in bytes.
    """
    with io.BytesIO() as output:
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False)
        data = output.getvalue()
    return data

def percentage_format(value: str|float|int, precision: int = 2) -> str:
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

def add_months(sourcedate: datetime, months: int) -> datetime:
    """
    Add or subtract months from a given date.
    Parameters:
        sourcedate (datetime): The original date.
        months (int): The number of months to add (can be negative).
    Returns:
        datetime: The new date after adding/subtracting months.
    """
    month = sourcedate.month - 1 + months
    year = sourcedate.year + month // 12
    month = month % 12 + 1
    day = min(sourcedate.day, [31,
        29 if year % 4 == 0 and not year % 100 == 0 or year % 400 == 0 else 28,
        31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month-1])
    return sourcedate.replace(year=year, month=month, day=day)

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
        for fmt in ["%Y%m%d", "%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"]:
            try:
                replace_date = datetime.strptime(replace_date, fmt)
                parsed = True
                break
            except ValueError:
                continue
        
        if not parsed:
            raise ValueError("replace_date must be date or datetime format: YYYY-MM-DD, YYYYMMDD, or YYYY-MM-DD HH:MM:SS")
            
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
                
                if unit == 'D':
                    current_date = current_date + timedelta(days=val)
                elif unit == 'M':
                    current_date = add_months(current_date, val)
                elif unit == 'Y':
                    current_date = current_date.replace(year=current_date.year + val)
        
        # Format
        fmt_str = format_part[1:] # Remove separator
        
        # Handle specific time formats where MM might be used for minutes
        # Prioritize longer matches to correctly identify minutes
        # YYYYMMDDHHMMSS -> %Y%m%d%H%M%S
        # YYYYMMDD-HH:MM:SS -> %Y%m%d-%H:%M:%S
        # YYYYMMDD HHMMSS -> %Y%m%d %H%M%S
        # YYYY-MM-DD-HH-MM-SS -> %Y-%m-%d-%H-%M-%S (New!)
        # Explicit minutes via mm -> %M (e.g., YYYYMMDDHHmm)
        fmt_str = fmt_str.replace('HH:MM:SS', '%H:%M:%S').replace('HHMMSS', '%H%M%S').replace('HH-MM-SS', '%H-%M-%S')
        fmt_str = fmt_str.replace('HH:MM', '%H:%M').replace('HHMM', '%H%M').replace('HH-MM', '%H-%M')
        fmt_str = fmt_str.replace('MM:SS', '%M:%S').replace('MMSS', '%M%S').replace('MM-SS', '%M-%S')
        fmt_str = fmt_str.replace('HH', '%H')
        # Map custom format to strftime
        strftime_fmt = fmt_str.replace('YYYY', '%Y').replace('MM', '%m').replace('DD', '%d')
        strftime_fmt = strftime_fmt.replace('mm', '%M').replace('SS', '%S')
        formatted_date = current_date.strftime(strftime_fmt)
        text = text.replace(full_match, formatted_date)

    return text

def resolve_env(text: str) -> str:
    """
    Replace environment variable placeholders in the format ${ENV_VAR_NAME} within the input text.
    Parameters:
        text (str): The input text containing environment variable placeholders.
    Returns:
        str: The text with environment variable placeholders replaced by their values.
    """
    pattern = r"\$\{([A-Z0-9_]+)\}"
    
    def replace_match(match):
        var_name = match.group(1)
        return os.environ.get(var_name, "")

    return re.sub(pattern, replace_match, text)

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

def parse_datetime(val: Any, timezone: Any) -> datetime | None:
    """
    Parse a datetime from a string or return None if parsing fails.
    Parameters:
        val (Any): The value to parse as datetime.
        timezone (Any): The timezone to apply to the parsed datetime.
    Returns:
        datetime | None: The parsed datetime object or None if parsing fails.
    """
    if not val:
        return None
    if isinstance(val, str):
        try:
            return datetime.fromisoformat(val).astimezone(timezone)
        except ValueError:
            return None
    if isinstance(val, datetime):
        return val.astimezone(timezone)
    return None

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
    elif isinstance(data, list):
        return any(is_key_in_dict(item, target_key) for item in data)
    return False