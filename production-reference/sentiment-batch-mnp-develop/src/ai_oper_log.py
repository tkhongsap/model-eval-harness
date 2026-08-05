import os
import pandas as pd
from typing import Literal
from zoneinfo import ZoneInfo
from src.utils.common import (
    is_key_in_dict, 
    recursive_dict_value_by_key
)

from src.log import Logger
logger = Logger(__name__, env=os.environ.get("LOG_ENVIRONMENT", "prod"))

def logging_ai_operation(log_obj: dict, log_type: Literal['batch'], message: str = "") -> None:
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
    
    Parameters:
        log_instance (Logger): The logger instance to use for logging.
        log_obj (dict): The dictionary containing log details.
        log_type (Literal['batch']): The type of log, currently only supports 'batch'.
        message (str): An optional message to include in the log.
    Raises:
        ValueError: If log_type is not 'batch' or if log_obj is not a dictionary.
        KeyError: If required keys are missing in log_obj for the specified log_type.
    """
    if log_type not in ['batch']:
        logger.error(f"Invalid log_type: {log_type}. Expected 'batch'.")
        raise ValueError(f"Invalid log_type: {log_type}. Expected 'batch'.")

    if not isinstance(log_obj, dict):
        logger.error(f"Invalid log_obj: {log_obj}. Expected a dictionary.")
        raise TypeError(f"Invalid log_obj: {log_obj}. Expected a dictionary.")

    log_dict = {}
    if log_type == 'batch':
        schema = [
            'process_date', 'environment', 'project_id', 'project_type', 'total_transaction', 'total_success_transaction', 'total_failed_transaction', 'average_response_time_sec', 'total_runtime_sec'
        ]
        for key in schema:
            if not is_key_in_dict(log_obj, key):
                logger.error(f"Missing key in log_obj: {key}")
                raise KeyError(f"Missing key in log_obj: {key}")
        
            values = recursive_dict_value_by_key(log_obj, key)
            log_dict[key] = values[0] if values else None
    else:
        logger.error(f"Unsupported log_type: {log_type}")
        raise ValueError(f"Unsupported log_type: {log_type}")
    
    logger.info(message, extra={'json_payload': log_dict})

def ai_oper_log(transaction_df: pd.DataFrame, tz: ZoneInfo) -> None:
    """
    Process the transaction DataFrame to extract AI operation metrics and log them using the logging_ai_operation function.
    
    Parameters:
        transaction_df (pd.DataFrame): The DataFrame containing transaction logs with columns such as 'start_time', 'end_time', 'gcp_project_id', 'status_pass_failed_retry', and 'latency_ms'.
        tz (ZoneInfo): The timezone to which the timestamps should be converted for accurate logging.

    Raises:
        ValueError: If transaction_df is not a DataFrame or if required columns are missing.
        Exception: If any error occurs during the processing and logging of AI operation metrics.
    """
    logger.info("Stamping AI-Operation logs")
    
    if transaction_df is None or transaction_df.empty:
        logger.warning("No transaction log DataFrame found for stamping logs - skipping logging")
        return
    try:
        transaction_df = transaction_df.copy()
        transaction_df['start_time'] = pd.to_datetime(transaction_df['start_time'], errors='coerce', utc=True).dt.tz_convert(tz)
        transaction_df['end_time'] = pd.to_datetime(transaction_df['end_time'], errors='coerce', utc=True).dt.tz_convert(tz)
        mask = transaction_df['start_time'] > transaction_df['end_time']
        transaction_df.loc[mask, ['start_time', 'end_time']] = (
            transaction_df.loc[mask, ['end_time', 'start_time']].values
        )
        transaction_df['process_date'] = transaction_df['start_time'].dt.date
        transaction_df['latency_ms'] = pd.to_numeric(transaction_df['latency_ms'], errors='coerce')

        log_df = transaction_df.groupby(
            ['process_date', 'gcp_project_id'],
            as_index=False,
            dropna=False
        ).agg(
            total_transaction=('status_pass_failed_retry', 'count'),
            total_success_transaction=('status_pass_failed_retry', lambda x: (x == 'Pass').sum()),
            total_failed_transaction=('status_pass_failed_retry', lambda x: (x == 'Failed').sum()),
            average_response_time_sec=('latency_ms', lambda x: round(x.mean() / 1000, 2) if pd.notna(x.mean()) else 0.0),
            min_start_time=('start_time', 'min'),
            max_end_time=('end_time', 'max'),
        )
        log_df['total_runtime_sec'] = (log_df['max_end_time'] - log_df['min_start_time']).dt.total_seconds().round(2)
        log_df = log_df.drop(columns=['min_start_time', 'max_end_time'])
        env = os.environ.get("ENVIRONMENT", "").lower()
        if env == "prod":
            log_df['environment'] = "production"
        elif env == "nprd":
            log_df['environment'] = "non-production"
        else:
            log_df['environment'] = env or "unknown"
        log_df = log_df.rename(columns={'gcp_project_id': 'project_id'})
        log_df['project_type'] = "batch"

        log_df = log_df[
            ['process_date', 'environment', 'project_id', 'project_type', 'total_transaction', 'total_success_transaction', 'total_failed_transaction', 'average_response_time_sec', 'total_runtime_sec']
        ]
        log_list = log_df.to_dict(orient='records')
        for log in log_list:
            logging_ai_operation(
                log_obj=log,
                log_type='batch',
                message="AI-Operation-Log"
            )
        logger.info("AI-Operation log stamping completed successfully")
    except Exception as log_err:
        logger.error(f"Failed to stamp AI-Operation logs: {log_err}", exc_info=True)