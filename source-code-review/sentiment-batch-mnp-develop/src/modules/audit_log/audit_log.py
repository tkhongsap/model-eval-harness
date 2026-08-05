import os
import pandas as pd
from src.log import Logger
from src.modules.audit_log.transaction import (
    TransactionPayload,
    TransactionLogSchema,
)
from src.modules.audit_log.performance import (
    PerformancePayload,
    PerformanceLogSchema,
)

class AuditLogModule:
    def __init__(self):
        pass

    def log_transaction(self, log_data: list[TransactionPayload]) -> pd.DataFrame:
        """
        Convert list of transaction log dictionaries to a pandas DataFrame.
        Parameters:
            log_data (list[TransactionPayload]): List of transaction log dictionaries.
        Returns:
            pd.DataFrame: DataFrame containing the transaction logs.
        """
        extracted_logs = []
        for record in log_data:
            record_obj = TransactionLogSchema.from_dict(record)
            extracted_logs.append(record_obj)
        df = pd.DataFrame([vars(log) for log in extracted_logs])
        return df
        
    def log_performance(self, log_data: list[PerformancePayload]) -> pd.DataFrame:
        """
        Convert list of performance log dictionaries to a pandas DataFrame.
        Parameters:
            log_data (list[PerformancePayload]): List of performance log dictionaries.
        Returns:
            pd.DataFrame: DataFrame containing the performance logs.
        """
        extracted_logs = []
        for record in log_data:
            record_obj = PerformanceLogSchema.from_dict(record)
            extracted_logs.append(record_obj)
        df = pd.DataFrame([vars(log) for log in extracted_logs])
        return df
        
        