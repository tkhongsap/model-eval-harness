import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

from src.core.engine import CoreEngine
from src.utils.date_utils import is_format_datetime

load_dotenv()

REPO_ROOT = Path(__file__).resolve().parent
ALLOWED_CONFIG_ROOT = (REPO_ROOT / "config").resolve()
ALLOWED_CONFIG_SUFFIXES = {".yml", ".yaml"}


def parse_arguments() -> argparse.Namespace:
    """Parse command line arguments.

    Returns:
        Parsed command-line arguments.
    """
    parser = argparse.ArgumentParser(description="Sentiment Batch QA Engine")
    parser.add_argument("-c", "--config_path", type=str, required=True, help="Path to the YAML configuration file")
    parser.add_argument(
        "-r", "--rerun_data_dt", type=str, default=None, help="Date for rerun data in YYYY-MM-DD format (optional)"
    )
    parser.add_argument(
        "-s", "--start_data_dt", type=str, default=None, help="Date for start data in YYYY-MM-DD format (optional)"
    )
    parser.add_argument(
        "-e", "--end_data_dt", type=str, default=None, help="Date for end data in YYYY-MM-DD format (optional)"
    )
    return parser.parse_args()


def validate_config_path(raw_path: str) -> Path:
    """Validate CLI config path before any file-system access.

    Args:
        raw_path: Raw ``--config_path`` input from CLI.

    Returns:
        A resolved absolute path to a valid YAML config file.

    Raises:
        ValueError: If the path is outside ``config/`` or has an invalid extension.
        FileNotFoundError: If the file does not exist.
    """
    resolved_path = Path(raw_path).expanduser().resolve(strict=False)

    try:
        resolved_path.relative_to(ALLOWED_CONFIG_ROOT)
    except ValueError as exc:
        raise ValueError(f"Invalid --config_path: {raw_path}. Path must stay within {ALLOWED_CONFIG_ROOT}.") from exc

    if resolved_path.suffix.lower() not in ALLOWED_CONFIG_SUFFIXES:
        raise ValueError(f"Invalid --config_path: {raw_path}. Only YAML files (.yml/.yaml) are allowed.")

    if not resolved_path.is_file():
        raise FileNotFoundError(f"Configuration file not found: {resolved_path}")

    return resolved_path


if __name__ == "__main__":
    try:
        args = parse_arguments()
        config_path = validate_config_path(args.config_path)

        packages = {}
        if args.rerun_data_dt:
            if not is_format_datetime(args.rerun_data_dt, "%Y-%m-%d"):
                raise ValueError("Invalid date format for --rerun_data_dt. Expected format: YYYY-MM-DD")
            packages["rerun_data_dt"] = args.rerun_data_dt

        if args.start_data_dt:
            if not is_format_datetime(args.start_data_dt, "%Y-%m-%d"):
                raise ValueError("Invalid date format for --start_data_dt. Expected format: YYYY-MM-DD")
            packages["start_data_dt"] = args.start_data_dt

        if args.end_data_dt:
            if not is_format_datetime(args.end_data_dt, "%Y-%m-%d"):
                raise ValueError("Invalid date format for --end_data_dt. Expected format: YYYY-MM-DD")
            packages["end_data_dt"] = args.end_data_dt

        engine = CoreEngine(config_path=config_path, packages=packages)
        success = engine.run()

        if not success:
            # Exit with non-zero code to indicate failure to Cloud Run
            sys.exit(1)
    except Exception as e:
        # Log and exit with error code for any unhandled exceptions
        print(f"Fatal error: {e}", file=sys.stderr)
        sys.exit(1)
