#!/usr/bin/env python3
# add move unable to process file in archieve to input when read prodiction.jsonl files
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from google.cloud import storage
from google import genai
from google.genai.types import CreateBatchJobConfig, JobState, HttpOptions
from google.genai import types
from google.protobuf.json_format import MessageToDict
from dotenv import load_dotenv
from zoneinfo import ZoneInfo
import asyncio
import requests
import re
import aiohttp
import google
import re
import mimetypes
import os
import sys
import json
import io
import google.auth
import pandas as pd
import time
import yaml
# ------------------------------------------------------------------------
# Import from other file
# ------------------------------------------------------------------------
from src.sharepoint import (
    get_access_token,
    list_folders_in_folder, 
    list_files_in_folder, 
    list_file_name_in_sharepoint_folder, 
    get_item_download_url_by_path,
    upload_file_to_sharepoint
)
from src.data_model import (
    upload_master_files, 
    upload_daily_files,
    create_daily_bar_chart_daily,
    create_daily_summary_files
)

from src.log import logger
import src.prompt as prompt
# ------------------------------------------------------------------------
# Auth & Environment Helper
# ------------------------------------------------------------------------
try:
    credentials, project_id = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
except google.auth.exceptions.DefaultCredentialsError:
    logger.error("❌ Could not find default Google Cloud credentials.")
    logger.error("Please run 'gcloud auth application-default login' or set GOOGLE_APPLICATION_CREDENTIALS.")
    sys.exit(1)


load_dotenv(override=True)
# threadpoolexecutor = ThreadPoolExecutor(max_workers=64)
thailand_tz = ZoneInfo("Asia/Bangkok")

################################### Initialize google config ###################################
GOOGLE_CLOUD_PROJECT = os.environ["GOOGLE_CLOUD_PROJECT"]
GOOGLE_CLOUD_LOCATION = os.environ["GOOGLE_CLOUD_LOCATION"]

PROCESSING_BUCKET = os.environ["PROCESSING_BUCKET"]
PROJECT_NAME = os.environ["PROJECT_NAME"]

# GCS Folder Layout
INPUT_PREFIX = os.environ["INPUT_PREFIX"]
PROCESSING_PREFIX = os.environ["PROCESSING_PREFIX"]
ARCHIVE_PREFIX = os.environ["ARCHIVE_PREFIX"]
BATCH_PREFIX = os.environ["BATCH_PREFIX"]
OUTPUT_EXCEL_PREFIX = os.environ["OUTPUT_EXCEL_PREFIX"]

# Batching Logic
BATCH_SIZE = int(os.environ["BATCH_SIZE"])
MODEL_NAME = os.environ["MODEL_NAME"] # Use 1.5-flash as 2.5 is not a public model name
MAX_CONCURRENT_UPLOAD = int(os.environ["MAX_CONCURRENT_UPLOAD"])

################################### Initialize google config ###################################

VERINT_SITE_CLIENT_ID = os.environ["VERINT_SITE_CLIENT_ID"]
VERINT_SITE_CLIENT_SECRET = os.environ["VERINT_SITE_CLIENT_SECRET"]
VERINT_SITE_TENANT_ID = os.environ["VERINT_SITE_TENANT_ID"]
VERINT_SITE_SITE_DOMAIN = os.environ["VERINT_SITE_SITE_DOMAIN"]
VERINT_SITE_SITE_PATH = os.environ["VERINT_SITE_SITE_PATH"]
VERINT_SITE_BASE_ROOT = os.environ["VERINT_SITE_BASE_ROOT"]

CONTROL_SITE_CLIENT_ID = os.environ["CONTROL_SITE_CLIENT_ID"]
CONTROL_SITE_CLIENT_SECRET = os.environ["CONTROL_SITE_CLIENT_SECRET"]
CONTROL_SITE_TENANT_ID = os.environ["CONTROL_SITE_TENANT_ID"]
CONTROL_SITE_SITE_DOMAIN = os.environ["CONTROL_SITE_SITE_DOMAIN"]
CONTROL_SITE_SITE_PATH = os.environ["CONTROL_SITE_SITE_PATH"]
CONTROL_SITE_BASE_ROOT = os.environ["CONTROL_SITE_BASE_ROOT"]
CONTROL_SITE_PROMPTS_ROOT = os.environ["CONTROL_SITE_PROMPTS_ROOT"]
CONTROL_SITE_CONTROL_PATH = os.environ["CONTROL_SITE_CONTROL_PATH"]
CONTROL_SITE_TRANSACTION_LOG_PATH = os.environ["CONTROL_SITE_TRANSACTION_LOG_PATH"]
CONTROL_SITE_PERFORMANCE_LOG_PATH = os.environ["CONTROL_SITE_PERFORMANCE_LOG_PATH"]

CONTROL_SITE_COST_PATH =  os.environ["CONTROL_SITE_COST_PATH"]

SANDBOX_SITE_CLIENT_ID = os.environ["SANDBOX_SITE_CLIENT_ID"]
SANDBOX_SITE_CLIENT_SECRET = os.environ["SANDBOX_SITE_CLIENT_SECRET"]
SANDBOX_SITE_TENANT_ID = os.environ["SANDBOX_SITE_TENANT_ID"]
SANDBOX_SITE_SITE_DOMAIN = os.environ["SANDBOX_SITE_SITE_DOMAIN"]
SANDBOX_SITE_SITE_PATH = os.environ["SANDBOX_SITE_SITE_PATH"]
SANDBOX_SITE_BASE_ROOT = os.environ["SANDBOX_SITE_BASE_ROOT"] 

PRODUCT_INPUT = os.environ["PRODUCT_INPUT"]
PRODUCT_OUTPUT = os.environ["PRODUCT_OUTPUT"]
LOOKBACK_DAYS = int(os.environ["LOOKBACK_DAYS"])

storage_client = storage.Client()
genai_client = genai.Client(
    vertexai=True, 
    project=GOOGLE_CLOUD_PROJECT, 
    location=GOOGLE_CLOUD_LOCATION,
    http_options=HttpOptions(api_version="v1")
)

# passing config function
config_dicts = {
    "PROCESSING_BUCKET": PROCESSING_BUCKET,
    "INPUT_PREFIX": INPUT_PREFIX,
    "PROCESSING_PREFIX": PROCESSING_PREFIX,
    "ARCHIVE_PREFIX": ARCHIVE_PREFIX,
    "BATCH_PREFIX": BATCH_PREFIX,
    "OUTPUT_EXCEL_PREFIX": OUTPUT_EXCEL_PREFIX,
    "BATCH_SIZE": BATCH_SIZE,
    "PROJECT_ID": project_id,
    "MODEL_NAME": MODEL_NAME,
    "VERINT_SITE_CLIENT_ID": VERINT_SITE_CLIENT_ID,
    "VERINT_SITE_CLIENT_SECRET": VERINT_SITE_CLIENT_SECRET,
    "VERINT_SITE_TENANT_ID": VERINT_SITE_TENANT_ID,
    "VERINT_SITE_SITE_DOMAIN": VERINT_SITE_SITE_DOMAIN,
    "VERINT_SITE_SITE_PATH": VERINT_SITE_SITE_PATH,
    "VERINT_SITE_BASE_ROOT": VERINT_SITE_BASE_ROOT,
    "CONTROL_SITE_CLIENT_ID": CONTROL_SITE_CLIENT_ID,
    "CONTROL_SITE_CLIENT_SECRET": CONTROL_SITE_CLIENT_SECRET,
    "CONTROL_SITE_TENANT_ID": CONTROL_SITE_TENANT_ID,
    "CONTROL_SITE_SITE_DOMAIN": CONTROL_SITE_SITE_DOMAIN,
    "CONTROL_SITE_SITE_PATH": CONTROL_SITE_SITE_PATH,
    "CONTROL_SITE_BASE_ROOT": CONTROL_SITE_BASE_ROOT,
    "CONTROL_SITE_PROMPTS_ROOT": CONTROL_SITE_PROMPTS_ROOT,
    "CONTROL_SITE_CONTROL_PATH": CONTROL_SITE_CONTROL_PATH,
    "PRODUCT_INPUT": PRODUCT_INPUT,
    "PRODUCT_OUTPUT": PRODUCT_OUTPUT,
    "GOOGLE_CLOUD_LOCATION": GOOGLE_CLOUD_LOCATION,
    "SANDBOX_SITE_CLIENT_ID": SANDBOX_SITE_CLIENT_ID,
    "SANDBOX_SITE_CLIENT_SECRET": SANDBOX_SITE_CLIENT_SECRET,
    "SANDBOX_SITE_TENANT_ID": SANDBOX_SITE_TENANT_ID,
    "SANDBOX_SITE_SITE_DOMAIN": SANDBOX_SITE_SITE_DOMAIN,
    "SANDBOX_SITE_SITE_PATH": SANDBOX_SITE_SITE_PATH,
    "SANDBOX_SITE_BASE_ROOT": SANDBOX_SITE_BASE_ROOT
}

# ------------------------------------------------------------------------
# Step 1: Process Completed Batches
# ------------------------------------------------------------------------
bucket = storage_client.bucket(PROCESSING_BUCKET)

def find_file_uri(request_data: dict) -> str | None:
    """Safely extracts the fileUri from a request payload."""
    try:
        # logger.info(f"----------------------------- contents : ")
        # logger.info(request_data.get('contents', {}))
        # logger.info(f"----------------------------- contents parts : ")
        # logger.info(request_data.get('contents', {})[0].get('parts', []))
        for part in request_data.get('contents', {})[0].get('parts', []):
            # logger.info(f"----------------------------- filedata : {part.get('fileData')}")
            if 'fileData' in part and part['fileData'] is not None and 'fileUri' in part['fileData']:
                return part['fileData']['fileUri']
    except Exception:
        logger.error(f"Failed to find file uri...")
        pass # Will return None
    return None

def feed_log_to_sharepoint(lines: list[dict], folder_prefix: str, access_token: str, **config_dicts):
    columns = [
        "data_date", 
        "start_time", 
        "end_time", 
        "total_time_mins", 
        "type", 
        "gcp_project_id", 
        "gcp_project_name", 
        "user_id",
        "source", 
        "storage_path", 
        "folder", 
        "filename", 
        "file_metadata_min",  
        "status_pass_failed_retry", 
        "error_log_if", 
        "latency_ms", 
        "token_usage_input", 
        "token_usage_output", 
        "total_cost_usd"
    ]

    try:
        cost_file_path = f"{CONTROL_SITE_BASE_ROOT.split('/')[0]}/{CONTROL_SITE_COST_PATH}/gemini-cost.yaml"
        cost_url = get_item_download_url_by_path("control", cost_file_path)
        cost_file = requests.get(
            cost_url,
            headers={"Authorization": f"Bearer {access_token}"}
        )
        cost_file.raise_for_status()
        cost_config = yaml.safe_load(cost_file.content)
        batch_cost = cost_config['batch'][MODEL_NAME]['pricing']

        text_input_item = next((item for item in batch_cost['input'] if item['type'] == 'text'), None)
        audio_input_item = next((item for item in batch_cost['input'] if item['type'] == 'audio'), None)
        audio_output_item = next((item for item in batch_cost['output'] if item['type'] == 'all'), None)
            
        text_input_price_usd = text_input_item['cost'] / text_input_item['tokens']
        audio_input_price_usd = audio_input_item['cost'] / audio_input_item['tokens']
        audio_output_price_usd = audio_output_item['cost'] / audio_output_item['tokens']
    except:
        text_input_price_usd = 0.15 / 1_000_000
        audio_input_price_usd = 0.5 / 1_000_000
        audio_output_price_usd = 1.25 / 1_000_000

    logs = []
    for line in lines:
        if not line.strip():
            continue

        data = json.loads(line)

        file_uri = find_file_uri(data.get('request', {}))
        
        file_uri_split =  file_uri.split("/") # file_uri : gs://sentiment-retention-bucket-01/processing/202511/20251111/9155832402870003681_0828192340_090028_90003867_firstname_lastname_title_T.wav
        tmp            = file_uri_split[-1].replace(".wav", "").split("_") # 9155832402870003681_0828192340_090028_90003867_firstname_lastname_T_20260108_236_IN.wav
        
        try:
            audio_duration = int(safe_list_get(tmp, 8, 0))
            audio_duration = f"{audio_duration // 60}:{(audio_duration % 60):02d}" 
        except Exception as e:
            logger.error(f"Error formatting audio duration for file {file_uri} with audio duration {audio_duration}: {e}")
            audio_duration = "0:00"

        text_input_token = 0
        audio_input_token = 0
        text_output_token = 0

        for token_detail in data.get('response', {}).get("usageMetadata", {}).get("promptTokensDetails", []):
            if token_detail.get("modality") == "AUDIO":
                audio_input_token = token_detail.get("tokenCount", 0)
            elif token_detail.get("modality") == "TEXT":
                text_input_token = token_detail.get("tokenCount", 0)

        for token_detail in data.get('response', {}).get("usageMetadata", {}).get("candidatesTokensDetails", []):
            if token_detail.get("modality") == "TEXT":
                text_output_token = token_detail.get("tokenCount", 0)

        audio_price = (
            text_input_token * text_input_price_usd
            + audio_input_token * audio_input_price_usd
            + text_output_token * audio_output_price_usd
        )

        start_time = str(data.get("response", {}).get("createTime", ""))
        end_time = str(data.get("processed_time", ""))
        
        if start_time and end_time:
            try:
                if datetime.fromisoformat(start_time) > datetime.fromisoformat(end_time):
                    start_time, end_time = end_time, start_time
            except Exception:
                pass
        
        try:
            process_time = abs((datetime.fromisoformat(end_time) - datetime.fromisoformat(start_time)).total_seconds())
        except Exception as e:
            process_time = 0

        text_input_token = 0
        audio_input_token = 0
        promptTokensDetails = data.get('response', {}).get("usageMetadata", {}).get("promptTokensDetails", [])
        for token_detail in promptTokensDetails:
            if token_detail.get("modality") == "AUDIO":
                audio_input_token = token_detail.get("tokenCount", 0)
            elif token_detail.get("modality") == "TEXT":
                text_input_token = token_detail.get("tokenCount", 0)

        candidatesTokensDetails = data.get('response', {}).get("usageMetadata", {}).get("candidatesTokensDetails", [])
        for token_detail in candidatesTokensDetails:
            if token_detail.get("modality") == "TEXT":
                text_output_token = token_detail.get("tokenCount", 0)

        input_token = text_input_token + audio_input_token
        output_token = text_output_token

        status = "Pass"
        if output_token == 0:
            status = "Failed"

        try:
            output = (
                data.get("response", {})
                .get("candidates", [{}])[0]
                .get("content", {})
                .get("parts", [{}])[0]
                .get("text", "")
            )

            logs.append([
                file_uri.split("/")[-2],
                start_time,
                end_time,
                f"{int(process_time // 60)}.{int(process_time % 60):02d}",
                "AI Classification",
                project_id,
                PROJECT_NAME,
                "daisyrpa",
                "SharePoint",
                f"https://{VERINT_SITE_SITE_DOMAIN}/{VERINT_SITE_SITE_PATH}/{VERINT_SITE_BASE_ROOT}/{INPUT_PREFIX}/{PRODUCT_INPUT}",
                f"{file_uri.split('/')[-2][0:4]}/{file_uri.split('/')[-2]}",
                file_uri.split('/')[-1],
                audio_duration,
                status,
                "",
                f"{process_time*1000}", 
                input_token,
                output_token,
                audio_price
            ])
        except Exception as e:
            logs.append([
                file_uri.split("/")[-2],
                start_time,
                end_time,
                f"{int(process_time // 60)}.{int(process_time % 60):02d}",
                "AI Classification",
                project_id,
                PROJECT_NAME,
                "daisyrpa",
                "SharePoint",
                f"https://{VERINT_SITE_SITE_DOMAIN}/{VERINT_SITE_SITE_PATH}/{VERINT_SITE_BASE_ROOT}/{INPUT_PREFIX}/{PRODUCT_INPUT}",
                f"{file_uri.split('/')[-2][0:4]}/{file_uri.split('/')[-2]}",
                file_uri.split('/')[-1],
                audio_duration,
                "Failed",
                str(e),
                f"{process_time*1000}", 
                input_token,
                output_token,
                audio_price
            ])

    # =========================
    # TRANSACTION LOG UPLOAD
    # =========================
    transaction_df = pd.DataFrame(logs, columns=columns)
    yyyymmdd = folder_prefix.split('T')[0].split('/')[-1].replace("-", "")
    transaction_log_path = f"{CONTROL_SITE_BASE_ROOT}/{CONTROL_SITE_TRANSACTION_LOG_PATH}/{yyyymmdd[:4]}"
    log_name = f"transaction_log_{yyyymmdd[0:6]}.csv"
    transaction_path = f"{transaction_log_path}/{log_name}"
    
    # print log for operation
    logging_ai_operation(transaction_df)

    try:
        log_url = get_item_download_url_by_path("control", transaction_path)
        log_file = requests.get(
            log_url,
            headers={"Authorization": f"Bearer {access_token}"}
        )
        log_file.raise_for_status()

        # Load existing CSV into DataFrame
        existing_df = pd.read_csv(io.BytesIO(log_file.content))

        transaction_final_df = pd.concat([existing_df, transaction_df], ignore_index=True)
    except Exception as e:
        logger.error(f"Error concat new record: {transaction_path}")
        transaction_final_df = transaction_df
    transaction_final_df['data_date'] = transaction_final_df['data_date'].astype(str)
    transaction_final_df['start_time'] = pd.to_datetime(transaction_final_df['start_time'], format='ISO8601', utc=True)
    transaction_final_df['start_time'] = transaction_final_df['start_time'].dt.tz_convert('Asia/Bangkok')
    transaction_final_df['end_time'] = pd.to_datetime(transaction_final_df['end_time'], format='ISO8601', utc=True)
    transaction_final_df['end_time'] = transaction_final_df['end_time'].dt.tz_convert('Asia/Bangkok')
    transaction_final_df = transaction_final_df.sort_values(by='data_date')
    csv_buffer = io.BytesIO()
    transaction_final_df.to_csv(csv_buffer, index=False, encoding="utf-8-sig")
    csv_buffer.seek(0)

    upload_file_to_sharepoint("control", csv_buffer, transaction_path)

    logger.info(f"Transaction log uploaded to SharePoint: {transaction_path}")

    # =========================
    # PERFORMANCE LOG UPLOAD
    # =========================
    performance_col = [
        "data_date",
        "run_date",
        "gcp_project_id",
        "gcp_project_name",
        "total_transaction",
        "total_completed",
        "total_failed",
        "success_rate",
        "total_runtime"
    ]
    transaction_final_df['run_date'] = transaction_final_df['start_time'].dt.strftime('%Y%m%d')

    performance_rows = []
    for data_date in transaction_final_df["data_date"].unique():
        data_date_df = transaction_final_df[transaction_final_df["data_date"] == data_date]

        for run_date in data_date_df["run_date"].unique():
            if pd.isna(run_date):
                continue
            run_date_df = data_date_df[data_date_df["run_date"] == run_date]
            
            total_transaction = len(run_date_df)
            total_completed = (run_date_df["status_pass_failed_retry"] == "Pass").sum()
            total_failed = (run_date_df["status_pass_failed_retry"] == "Failed").sum()
            min_time = run_date_df['start_time'].min()
            max_time = run_date_df['end_time'].max()
            seconds = (max_time - min_time).total_seconds()
            minutes = f"{int(seconds // 60)}.{int(seconds % 60):02d}" 
            performance_rows.append({
                "data_date": data_date,
                "run_date": run_date,
                "gcp_project_id": run_date_df["gcp_project_id"].iloc[0],
                "gcp_project_name": run_date_df["gcp_project_name"].iloc[0],
                "total_transaction": total_transaction,
                "total_completed": total_completed,
                "total_failed": total_failed,
                "success_rate": round((total_completed / total_transaction) * 100, 2) if total_transaction > 0 else 0,
                "total_runtime": minutes
            })

    performance_final_df = pd.DataFrame(performance_rows)


    yyyymmdd = folder_prefix.split('T')[0].split('/')[-1].replace("-", "")
    performance_log_path = f"{CONTROL_SITE_BASE_ROOT}/{CONTROL_SITE_PERFORMANCE_LOG_PATH}/{yyyymmdd[:4]}"
    log_name = f"performance_log_{yyyymmdd[0:6]}.csv"
    performance_path = f"{performance_log_path}/{log_name}"
    performance_final_df = performance_final_df.sort_values(by=['data_date', 'run_date'])
    csv_buffer = io.BytesIO()
    performance_final_df.to_csv(csv_buffer, index=False, encoding="utf-8-sig")
    csv_buffer.seek(0)

    upload_file_to_sharepoint("control", csv_buffer, performance_path)

    logger.info(f"Performance log uploaded to SharePoint: {performance_path}")
 

def move_to_archived_files(file_uris: set[str]):
    if not file_uris:
        logger.info("No files to move to archive.")
        return

    logger.info(f"Moving {len(file_uris)} processed files to archive...")
    
    for uri in file_uris:
        try:
            # Extract blob name from GCS URI
            blob_name = uri.replace(f"gs://{PROCESSING_BUCKET}/", "", 1)
            source_blob = bucket.blob(blob_name)

            # Replace prefix from 'processing/' → 'archive/'
            destination_blob_name = blob_name.replace(PROCESSING_PREFIX, ARCHIVE_PREFIX, 1)

            # Copy → Delete (move)
            bucket.copy_blob(source_blob, bucket, destination_blob_name)
            source_blob.delete()

            logger.info(f"Moved {uri} → gs://{PROCESSING_BUCKET}/{destination_blob_name}")

        except Exception as e:
            logger.error(f"Failed to move file {uri} to archive: {e}")

def move_to_input_files(file_uris: set[str]):
    if not file_uris:
        logger.info("No files to move to input.")
        return

    logger.info(f"Moving {len(file_uris)} processed files to input...")
    
    for uri in file_uris:
        try:
            # Extract blob name from GCS URI
            blob_name = uri.replace(f"gs://{PROCESSING_BUCKET}/", "", 1)
            source_blob = bucket.blob(blob_name)

            # Replace prefix from 'processing/' → 'input/'
            destination_blob_name = blob_name.replace(PROCESSING_PREFIX, INPUT_PREFIX, 1)

            # Copy → Delete (move)
            bucket.copy_blob(source_blob, bucket, destination_blob_name)
            source_blob.delete()

            logger.info(f"Moved {uri} → gs://{PROCESSING_BUCKET}/{destination_blob_name}")

        except Exception as e:
            logger.error(f"Failed to move file {uri} to input: {e}")

def delete_gcs_folder(folder_prefix: str):
    """Deletes all blobs under a given GCS folder prefix."""
    logger.info(f"🧹 Deleting processed batch folder: {folder_prefix}")
    blobs = list(bucket.list_blobs(prefix=folder_prefix))
    if not blobs:
        logger.warning(f"No blobs found to delete under {folder_prefix}.")
        return

    try:
        with storage_client.batch():
            for blob in blobs:
                logger.info(f"Deleted files: {blob.name}")
                blob.delete()
    except Exception as e:
        logger.error(f"Error during bulk deletion of {folder_prefix}: {e}", exc_info=True)

def safe_list_get(list_name, index, default_value):
    """
    Safely retrieves an element from a list by index, returning a default value if the index is out of range.
    Parameters:
        - list_name: The list from which to retrieve the element.
        - index: The index of the element to retrieve.
        - default_value: The value to return if the index is out of range.
    Returns:
        - The element at the specified index, or the default value if the index is invalid.
    """
    try:
        return list_name[index]
    except IndexError:
        return default_value
    
def ensure_df_schema(df: pd.DataFrame, schemas: list) -> pd.DataFrame:
    """
    Ensure that the DataFrame contains all required schema columns.

    Parameters:
        df (pd.DataFrame): Input DataFrame to validate.
        schemas (list): List of required schema field names.
    Returns:
        pd.DataFrame: DataFrame with all schema columns.
    """
    logger.debug(f"Validating DataFrame schema with {len(schemas)} required columns")
    # Get all schema field names in order
    schema_columns = list(schemas)
    
    # Add missing columns with None values
    missing_columns = []
    for col in schema_columns:
        if col not in df.columns:
            logger.debug(f"Adding missing column: {col}")
            df[col] = pd.NA
            missing_columns.append(col)
    
    if missing_columns:
        logger.warning(f"Added {len(missing_columns)} missing columns: {missing_columns}")
    else:
        logger.debug("All required columns are present")
    
    # Reorder columns to match schema
    df = df[schema_columns]
    logger.info(f"DataFrame schema validated and reordered to match {len(schema_columns)} columns")
    
    return df

def logging_ai_operation(transaction_df: pd.DataFrame) -> None:
    """
    Post-execution hook for the task. This method is called after the main execution logic is completed. It is responsible for stamping AI-Operation logs based on the transaction log data.
    """
    logger.info("Stamping AI-Operation logs")        
    if transaction_df is None or transaction_df.empty:
        logger.warning("No transaction log DataFrame found for stamping logs - skipping logging")
        return None
    try:
        required_columns = ['start_time', 'end_time', 'gcp_project_id', 'status_pass_failed_retry', 'latency_ms']
        transaction_df = ensure_df_schema(transaction_df, required_columns).copy()
        transaction_df['start_time'] = pd.to_datetime(transaction_df['start_time'], errors='coerce', utc=True).dt.tz_convert('Asia/Bangkok')
        transaction_df['end_time'] = pd.to_datetime(transaction_df['end_time'], errors='coerce', utc=True).dt.tz_convert('Asia/Bangkok')
        transaction_df['latency_ms'] = pd.to_numeric(transaction_df['latency_ms'], errors='coerce')
        transaction_df['process_date'] = transaction_df['start_time'].dt.date
        process_dates = transaction_df['start_time'].dt.date.unique().tolist() if 'start_time' in transaction_df.columns else []
        for process_date in process_dates:
            filtered_df = transaction_df[(transaction_df['process_date'] == process_date)]
                
            if filtered_df.empty:
                logger.warning(f"No transaction records found for process_date {process_date} - skipping log stamping")
                continue

            log_df = filtered_df.groupby(
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
            log_df['total_runtime_sec'] = (pd.to_datetime(log_df['max_end_time'], errors='coerce') - pd.to_datetime(log_df['min_start_time'], errors='coerce')).dt.total_seconds().round(2)
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
            
            log_df['process_date'] = log_df['process_date'].astype(str)
            log_list = log_df.to_dict(orient='records')
            for log in log_list:
                logger.info("AI-Operation-Log", extra={'json_payload': log})

        logger.info("AI-Operation log stamping completed successfully")
    except Exception as log_err:
        logger.error(f"Failed to stamp AI-Operation logs: {log_err}", exc_info=True)

def check_and_process_batch(folder_prefix: str) -> bool:
    """Checks a single batch folder and processes it if complete."""
    logger.info(f"Checking batch folder: {folder_prefix}")

    # 1. Check for input JSON
    input_json_blobs = list(bucket.list_blobs(prefix=f"{folder_prefix}input_json/", max_results=1))
    
    # 2. Check for output model folder
    output_blob_iterator = bucket.list_blobs(prefix=f"{folder_prefix}output/", delimiter='/')
    _ = list(output_blob_iterator)
    output_model_folders = list(output_blob_iterator.prefixes)

    if not input_json_blobs or not output_model_folders:
        logger.info(f"Batch {folder_prefix} is not ready (missing input or output folder). Skipping.")
        return False
        
    model_output_prefix = output_model_folders[0] # e.g., .../output/prediction-model-.../

    # 3. Check for predictions.jsonl file
    # The file is located at: output/prediction-model-*/predictions.jsonl
    # We need to search recursively in the model output folder
    all_blobs = list(bucket.list_blobs(prefix=model_output_prefix))
    prediction_files = [b for b in all_blobs if b.name.endswith('predictions.jsonl')]

    if not prediction_files:
        logger.info(f"Batch {folder_prefix} is not ready (output folder exists, but no predictions.jsonl file found). Skipping.")
        return False

    # --- This batch is complete and ready for processing ---
    logger.info(f"📂 Processing completed batch: {folder_prefix}")
    
    all_data = []
    passed_files_uri_set = set()
    failed_files_uri_set = set()
    
    text_input_price_usd = 0.15/1000000
    audio_input_price_usd = 0.5/1000000
    audio_output_price_usd = 1.25/1000000
    usd_to_thb = 33

    for blob in prediction_files:
        logger.info(f"Reading prediction file: {blob.name}")
        try:
            lines = blob.download_as_text(encoding='utf-8').splitlines()  # Add encoding here
        except Exception as e:
            logger.error(f"Failed to download or read {blob.name}: {e}")
            continue

        for line in lines:
            if not line.strip():
                continue
            
            
            data = json.loads(line)
            # Find the original file URI from the request
            file_uri = find_file_uri(data.get('request', {}))
            if not file_uri:
                logger.warning(f"Could not find fileUri in prediction line: {line[:100]}...")
                continue

            # Extract Fill in data
            # file_uri : gs://sentiment-retention-bucket-01/processing/202511/20251111/9155832402870003681_0828192340_090028_90003867_firstname_lastname_title_T.wav - OLD
            # file_uri : gs://sentiment-retention-bucket-01/processing/202511/20251111/9155832402870003681_0828192340_090028_90003867_firstname_lastname_T_20260108_236_IN.wav - NEW
            file_uri_split =  file_uri.split("/") # file_uri : gs://sentiment-retention-bucket-01/processing/202511/20251111/9155832402870003681_0828192340_090028_90003867_firstname_lastname_title_T.wav
            tmp           = file_uri_split[-1].replace(".wav", "").split("_") # 9155832402870003681_0828192340_090028_90003867_firstname_lastname_T_20260108_236_IN.wav

            call_id       = safe_list_get(tmp, 0, "")
            phone_number  = safe_list_get(tmp, 1, "")
            call_date     = file_uri_split[-2] 
            call_time     = safe_list_get(tmp, 2, "")

            file_name     = file_uri_split[-1]
            first_name    = safe_list_get(tmp, 4, "")
            last_name     = safe_list_get(tmp, 5, "")
            user          = f"{first_name} {last_name}"

            input_folder  = "/".join(file_uri_split[:-1])
            output_folder = input_folder.replace("processing", "archive")
            
            process_time      = data.get("processed_time", {})
            process_time_gmt7 = datetime.fromisoformat(process_time) + timedelta(hours=7)
            run_date          = process_time_gmt7.isoformat()

            # Post processing AI result 
            try:
                audio_duration = int(safe_list_get(tmp, 8, 0))
                if isinstance(audio_duration, int) or isinstance(audio_duration, float):
                    audio_duration = f"{audio_duration // 60}.{(audio_duration % 60):02d}" 
                text_input_token = 0
                audio_input_token = 0
                text_output_token = 0
                promptTokensDetails = data.get("response", {}).get("usageMetadata", {}).get("promptTokensDetails", [])
                for token_detail in promptTokensDetails:
                    if token_detail.get("modality") == "AUDIO":
                        audio_input_token = token_detail.get("tokenCount", 0)
                    elif token_detail.get("modality") == "TEXT":
                        text_input_token = token_detail.get("tokenCount", 0)

                candidatesTokensDetails = data.get("response", {}).get("usageMetadata", {}).get("candidatesTokensDetails", [])
                for token_detail in candidatesTokensDetails:
                    if token_detail.get("modality") == "TEXT":
                        text_output_token = token_detail.get("tokenCount", 0)

                audio_price = (text_input_token*text_input_price_usd + audio_input_token*audio_input_price_usd + text_output_token*audio_output_price_usd) * usd_to_thb       
                # raw_text = data['response']['candidates'][0]['content']['parts'][0]['text'] # old version no function call
                ai_result = data['response']['candidates'][0]['content']['parts'][0]['functionCall']['args'] # new version with function call
                # json_str = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_text.strip())
                # ai_result = json.loads(json_str)
                for product_name in ai_result["product"].keys():
                    if ai_result["product"][product_name] is None:
                        continue
                    product_result = ai_result["product"][product_name]
                    main = ""
                    keyword = ""
                    if product_result.get("main", None) is not None:
                        main = product_result.get("main", {}).get("reason", "")
                        keyword = product_result.get("main", {}).get("keyword", "")
                        
                    secondary = ""
                    keyword_secondary = ""
                    if product_result.get("secondary", None) is not None:
                        secondary = product_result.get("secondary", {}).get("reason", "")
                        keyword_secondary = product_result.get("secondary", {}).get("keyword", "")

                    third = ""
                    keyword_third = ""
                    if product_result.get("third", None) is not None:
                        third = product_result.get("third", {}).get("reason", "")
                        keyword_third = product_result.get("third", {}).get("keyword", "")

                    network_issue = product_result.get("network_issue", {})
                    issue_type = network_issue.get("issue_type", "")
                    sub_reason = network_issue.get("sub_reason", "")
                    problem_statement_list = network_issue.get("problem_statement_list", [])
                    churn_probability = network_issue.get("churn_probability", "")
                    area_tag_province = network_issue.get("area", {}).get("area_tag_province", "")
                    area_tag_district = network_issue.get("area", {}).get("area_tag_district", "")
                    area_tag_sub_district = network_issue.get("area", {}).get("area_tag_sub_district", "")
                    area_tag_landmark = network_issue.get("area", {}).get("area_tag_landmark", "")

                    all_data.append({
                        'call_id': call_id, 
                        'phone_number': phone_number,
                        'call_month': call_date[:-2],
                        'call_date' : call_date,
                        'call_time' : call_time,
                        'cost(thb)' : audio_price,
                        'call_duration(min)' : audio_duration,
                        'file_name' : file_name,
                        'user' : user,
                        # ================== AI result ==================
                        'call_result' : product_result.get("retention_outcome", ""),
                        'product' : product_name,
                        'main' : main,
                        'secondary' : secondary,
                        'third' : third,
                        'keyword(main)' : keyword,
                        'keyword(secondary)' : keyword_secondary,
                        'keyword(third)' : keyword_third,
                        'call_event_detection' : ai_result.get("call_event_detection", ""),
                        'AI_recommendation' : ai_result.get("recommendation", ""),
                        # ================== AI result ==================
                        'input_folder' : input_folder,
                        'output_folder' : output_folder,
                        'run_date' : run_date,
                        'status' : 'success',
                        'message' : "",
                        # ================== AI result (network) ==================
                        'issue_type': issue_type,
                        'sub_reason': sub_reason,
                        'problem_statement': ", ".join(problem_statement_list) if len(problem_statement_list) > 0 else "",
                        'churn_probability': churn_probability,
                        'area_tag_province': area_tag_province,
                        'area_tag_district': area_tag_district,
                        'area_tag_sub_district': area_tag_sub_district,
                        'area_tag_landmark': area_tag_landmark,
                        'product_type': product_name
                    })

                passed_files_uri_set.add(file_uri)
                
            except Exception as e:
                logger.error(f"Failed to parse line: {e}.")
                all_data.append({
                    'call_id': call_id, 
                    'phone_number': phone_number,
                    'call_month': call_date[:-2],
                    'call_date' : call_date,
                    'call_time' : call_time,
                    'cost(thb)' : audio_price,
                    'call_duration(min)' : audio_duration,
                    'file_name' : file_name,
                    'user' : user,

                    'input_folder' : input_folder,
                    'output_folder' : output_folder,
                    'run_date' : run_date,
                    'status' : 'fail',
                    'message' : f"{str(e)}"
                })
                failed_files_uri_set.add(file_uri if 'file_uri' in locals() else 'unknown')

    # 4. Append data to master.xlsx file
    master_df = upload_master_files(all_data, **config_dicts) 

    all_data_df = pd.DataFrame(all_data)
    grouped_series = all_data_df.groupby('call_month')['call_date'].unique()
    # 5. Append data to daily.xlsx file
    upload_daily_files(master_df, grouped_series, "retention", **config_dicts) # retention is team name
    upload_daily_files(master_df, grouped_series, "network", **config_dicts)   # network is team name

    # 6. Create Bar-Chart plot
    create_daily_bar_chart_daily(master_df, grouped_series, **config_dicts)

    # 7. Create summary.txt file
    for i in range(3):
        try:
            create_daily_summary_files(master_df, grouped_series, **config_dicts)
            logger.info("summary process run succcessfully, exists the loop")
            break
        except:
            logger.error(f"Summary process file with error : {e}")

    # 8.Feed Log
    verint_access_token = get_access_token("verint")
    feed_log_to_sharepoint(lines, folder_prefix, verint_access_token, **config_dicts)

    # 9. Move passed files from 'processing/' to 'archieve/'
    move_to_archived_files(passed_files_uri_set)

    # 10. Move failed files from 'processing/' to 'input/'
    move_to_input_files(failed_files_uri_set)
    
    # 11. Delete the entire 'batchs/{batch_datetime}/' folder
    delete_gcs_folder(folder_prefix)
    
    return True

# ------------------------------------------------------------------------
# Step 2: Download files from sharepoint and prompt config
# ------------------------------------------------------------------------
async def download_upload(file_meta, session, gcs_client):
    """
    Downloads a file from SharePoint and uploads it to Google Cloud Storage.
    This function runs concurrently with other download_upload tasks.
    """
    # Extract the path from file metadata and clean it up
    # Removes '/drive/root:/' prefix (SharePoint specific) and strips slashes
    # Example: '/drive/root:/202511/20251106' → '202511/20251106'
    raw_path = file_meta["path"].replace(f"/drive/root:/", "", 1).strip("/")
    
    # Combine path and filename to create the full GCS path
    # Example: '202511/20251106/file.wav'
    gcs_path = f"{raw_path}/{file_meta['file_name']}"
    gcs_path = gcs_path.replace(f"{VERINT_SITE_BASE_ROOT}/", "")
    gcs_path = gcs_path.replace(f"{PRODUCT_INPUT}/", "")
    # Get a reference to the GCS bucket (no network call yet, just creating reference)
    # gcs_client is the Google Cloud Storage client
    # PROCESSING_BUCKET is your bucket name
    bucket = gcs_client.bucket(PROCESSING_BUCKET)
    
    # Create a blob (file) reference in the bucket
    # Still no network call - just creating a reference object
    blob = bucket.blob(gcs_path)

    loop = asyncio.get_event_loop()
    
    exists = await loop.run_in_executor(None, blob.exists)
    
    if exists:
        logger.info(f"⚠️ File already exists: gs://{PROCESSING_BUCKET}/{gcs_path}")
        return
    
    download_url = await loop.run_in_executor(
        None, 
        get_item_download_url_by_path, 
        "verint", 
        f"{raw_path}/{file_meta['file_name']}"
    )

    if not isinstance(download_url, str):
        logger.error(
            f"❌ Failed to get download URL for {gcs_path}. "
            f"Received type: {type(download_url)}. Skipping file."
        )
        return

    if not file_meta["file_name"].lower().endswith('.wav'):
        logger.error(f"❌ Not a WAV file: {file_meta['file_name']}")
        return
    mime_type = "audio/wav"
    
    async with session.get(download_url) as resp:
        resp.raise_for_status()
        data = await resp.read()

    await loop.run_in_executor(None, blob.upload_from_string, data, mime_type)
    
    # Log success message
    logger.info(f"✅ Uploaded: gs://{PROCESSING_BUCKET}/{gcs_path}")

async def download_files_async_from_sharepoint(files):
    """
    Main orchestrator function that manages concurrent file downloads/uploads.
    Processes all files in the list concurrently with controlled parallelism.
    
    Args:
        files: List of file metadata dictionaries, each containing 'path' and 'file_name'
    """
    access_token = get_access_token("verint") 
    async with aiohttp.ClientSession(headers={"Authorization": f"Bearer {access_token}"}) as session:
        gcs_client = storage.Client(project=project_id)
        sem = asyncio.Semaphore(MAX_CONCURRENT_UPLOAD)
        async def bounded_task(file_meta):
            async with sem:
                await download_upload(file_meta, session, gcs_client)

        await asyncio.gather(*(bounded_task(f) for f in files))


# ------------------------------------------------------------------------
# Step 3: Create New Batch
# ------------------------------------------------------------------------

def get_analysis_schema() -> dict:
    """
    Defines the function calling schema for the call analysis.
    The function 'analyze_call' is the structure the model must return.
    """
    
    # --- Shared Definitions for Reusability ---
    
    # Predefined categories for reasons (for documentation in the schema)
    REASON_CATEGORIES = "network, promotion related, device promotion related, save cost, contract end, sale upsell problem, dissatisfied service, other, post to pre, customer reason, down sell not success"
    
    # Define the structure for main, secondary, and third reasons
    PRODUCT_REASON_SCHEMA = {
        "type": "OBJECT",
        "properties": {
            "reason": {
                "type": "STRING",
                "enum": ["network", "promotion related", "device promotion related", "save cost", "contract end", "sale upsell problem", "dissatisfied service", "post to pre", "customer reason", "down sell not success", "other"],
                "description": f"Select reason from: {REASON_CATEGORIES}. Use empty string if not applicable."
            },
            "keyword": {
                "type": "STRING",
                "description": "List keywords or short phrases directly from the audio that explicitly indicate or support the reason. Use comma separation. Use empty string if not applicable."
            }
        },
        "required": ["reason", "keyword"]
    }

    NETWORK_ISSUE_SCHEMA = {
        "type": "OBJECT",
        "nullable": True, # Fixes the NULL error in Vertex AI
        "properties": {
            "issue_type": {
                "type": "STRING",
                "enum": ["Speed", "Outage", "Drop", "Coverage", "FUP", "Installation", "Support", "Voice Quality"]
            },
            "sub_reason": {"type": "STRING"},
            "problem_statement_list": {
                "type": "ARRAY", 
                "items": {"type": "STRING"}
            },
            "churn_probability": {"type": "INTEGER"},
            "area": {
                "type": "OBJECT",
                "properties": {
                    "area_tag_province": {"type": "STRING", "nullable": True},
                    "area_tag_district": {"type": "STRING", "nullable": True},
                    "area_tag_sub_district": {"type": "STRING", "nullable": True},
                    "area_tag_landmark": {"type": "STRING", "nullable": True}
                }
            }
        }
    }

    # Define the structure for an individual product (e.g., Postpaid, TOL)
    PRODUCT_ANALYSIS_SCHEMA = {
        "type": "OBJECT",
        "properties": {
            "main": PRODUCT_REASON_SCHEMA,
            "secondary": PRODUCT_REASON_SCHEMA,
            "third": PRODUCT_REASON_SCHEMA,
            "retention_outcome": {
                "type": "STRING",
                "enum": ["churn", "save", "unknown", "undefined"],
                "description": "Determine the final decision of the client: churn, save, unknown or undefined."
            },
            "network_issue": NETWORK_ISSUE_SCHEMA
        },
        "required": ["main", "retention_outcome"]
    }
    
    # --- Main Function Declaration ---
    return {
        "name": "analyze_call",
        "description": "Performs a comprehensive analysis of the client's call based on cancellation reasons, emotional state, agent's response, and retention outcome, strictly adhering to the defined structure.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "product": {
                    "type": "OBJECT",
                    "description": "Analysis of the specific products (Postpaid, TOL, TVS, unknown) mentioned in the call. Only include product keys that were mentioned.",
                    "properties": {
                        "Postpaid": PRODUCT_ANALYSIS_SCHEMA,
                        "TOL": PRODUCT_ANALYSIS_SCHEMA,
                        "TVS": PRODUCT_ANALYSIS_SCHEMA,
                        "unknown": PRODUCT_ANALYSIS_SCHEMA
                    },
                    "additionalProperties": False # Enforce use of only the defined product keys
                },
                "call_event_detection": {
                    "type": "STRING",
                    "enum": ["Market-Driven Events (เหตุการณ์ทางการตลาด)", "Crisis & Emergency Events (เหตุการณ์วิกฤตหรือภัยพิบัติ)", "Campaign-Drvien Events (เหตุการณ์ด้านเคมเปญต่างๆของบริษัท)", "Technology & Service Events (เหตุการณ์ด้านเทคโนโลยี/บริการ)", "True-DTAC Merger(การรวมกิจการของ True และ ดีแทค)", "Emerging or Undefined Events (เหตุผลที่ยังไม่สามารถจัดกลุ่มได้)"],
                    "description": "Determine what caused the client to call: Market-Driven Events (เหตุการณ์ทางการตลาด), Crisis & Emergency Events (เหตุการณ์วิกฤตหรือภัยพิบัติ), Campaign-Drvien Events (เหตุการณ์ด้านเคมเปญต่างๆของบริษัท), Technology & Service Events (เหตุการณ์ด้านเทคโนโลยี/บริการ), True-DTAC Merger(การรวมกิจการของ True และ ดีแทค), or Emerging or Undefined Events (เหตุผลที่ยังไม่สามารถจัดกลุ่มได้)."
                },
                "recommendation": {
                    "type": "STRING",
                    "description": "Suggestion on how to keep the client loyal to the brand. Use empty string if unable to provide a recommendation."
                }
            },
            "required": ["product", "call_event_detection", "recommendation"]
        }
    }

# --- Note: You should replace your current get_analysis_schema() with the one above. ---

async def create_jsonl_record(blob, session, gcs_client, prompt_text, bucket) -> dict:
    """Creates the JSONL record structure for a single file."""
    try:
        # 1. Initial loop for sync task
        loop = asyncio.get_event_loop()
        
        # 2. Move file from 'input/' to 'processing/'
        original_path_part = blob.name[len(INPUT_PREFIX):] # e.g., 'yyyymm/yyyymmdd/voice.mav'
        processing_blob_name = f"{PROCESSING_PREFIX}{original_path_part}"
        
        # Use copy + delete to "move"
        new_blob = await loop.run_in_executor(None, bucket.copy_blob, blob, bucket, processing_blob_name) # *args
        await loop.run_in_executor(None, blob.delete)
        
        # 3. Create JSONL record using the *new* processing path
        gcs_uri = f"gs://{PROCESSING_BUCKET}/{new_blob.name}"
        
        mime_type = new_blob.content_type
        if not mime_type: # Guess if metadata is missing
            if new_blob.name.lower().endswith(".mp3"):
                mime_type = "audio/mpeg"
            elif new_blob.name.lower().endswith(".wav"):
                mime_type = "audio/wav"
            else:
                logger.warning(f"Skipping {new_blob.name}: Unknown MIME type.")
        
        function_declaration = get_analysis_schema()
        
        return {
        "request": {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"text": prompt_text},
                        {
                            "fileData": {
                                "fileUri": gcs_uri,
                                "mimeType": mime_type
                            }
                        }
                    ]
                }
            ],
            "tools": [
                {
                    "functionDeclarations": [function_declaration]
                }
            ],
            "toolConfig": {
                "functionCallingConfig": {
                    # Setting to 'ANY' strongly encourages the model to use the defined function.
                    "mode": "ANY" 
                }
            },
            "generationConfig": {
                "temperature": 0.0,
                "topP": 0,
                "maxOutputTokens": 65535,
                "seed": 0,
                "thinkingConfig" : {
                    "thinkingBudget": 0
                }
            }
        }
    }

    except Exception as e:
        logger.error(f"Failed to move or process file {blob.name}: {e}", exc_info=True)
        return None


async def create_jsonl_file(files_to_batch) -> list:
    input_path = f"{CONTROL_SITE_BASE_ROOT}/{CONTROL_SITE_PROMPTS_ROOT}/sentiment"
    
    loop = asyncio.get_event_loop()
    # de-blocking task in asyncio task
    file_name_list = list_file_name_in_sharepoint_folder("control", input_path)
    file_name_list.sort() # latest file version will be last index

    prompt_url = f"{input_path}/{file_name_list[-1]}"
    prompt_download_url = await loop.run_in_executor(
        None,
        get_item_download_url_by_path,
        "control",
        prompt_url
    )
    # define aiohttp
    access_token = get_access_token("control")
    async with aiohttp.ClientSession(headers={"Authorization": f"Bearer {access_token}"}) as session:
        # perform rest api to get file
        async with session.get(prompt_download_url) as resp:
            resp.raise_for_status()
            prompt_file_bytes = await resp.read()

        # decode byte to string 
        user_prompt = prompt_file_bytes.decode("utf-8").strip()
        with open('./config/system_prompt/retention.yml', 'r', encoding='utf-8') as file:
            config = yaml.safe_load(file)
        system_prompt = config['system_prompt']
        prompt_text = system_prompt.replace("{user_prompt}", user_prompt)
        # # perform creation of create json record
        gcs_client = storage.Client(project=project_id)
        sem = asyncio.Semaphore(BATCH_SIZE)
        async def bounded_task(blob):
            # continue create with available emp_id with respect to semaphore batch number limit
            async with sem:
                return await create_jsonl_record(blob, session, gcs_client, prompt_text, bucket)
            
        jsonl_records = await asyncio.gather(*(bounded_task(blob) for blob in files_to_batch))
        jsonl_records = [r for r in jsonl_records if r is not None]  # Filter out failures

        if not jsonl_records:
            logger.warning("No valid JSONL records created. Skipping batch creation.")
            return

        return jsonl_records

# ------------------------------------------------------------------------
# Main Orchestrator
# ------------------------------------------------------------------------
def main():
    logger.info("🚀 Starting batch processing job...")
    
    # Step 1 — Process completed batches
    try:
        logger.info("--- Step 1: Processing Completed Batches ---")
        
        # List all top-level folders in 'batches/'
        blob_iterator = bucket.list_blobs(prefix=f"{BATCH_PREFIX}/", delimiter='/')
        _ = list(blob_iterator)  # Consume the iterator
        batch_folders = list(blob_iterator.prefixes)
        
        if not batch_folders:
            logger.info("No batch folders found in 'batches/'. Skipping step 1.")

        logger.info(f"Found {len(batch_folders)} batch folders to check.", extra={'json_payload': str(batch_folders)})
        processed_count = 0
        for folder_prefix in batch_folders:
            try:
                if check_and_process_batch(folder_prefix):
                    processed_count += 1
            except Exception as e:
                logger.error(f"Unhandled error processing batch {folder_prefix}: {e}", exc_info=True)
                
        if processed_count == 0:
            logger.info("No batches were ready for processing.")
        else:
            logger.info(f"Successfully processed {processed_count} completed batches.")
    except Exception as e:
        logger.error(f"❌ Unhandled error during 'Step 1: Process Completed Batches': {e}", exc_info=True)

    # Step 2 - Download files from sharepoint and prompt config
    try:
        logger.info("--- Step 2: Processing Completed Batches ---")
        input_path = f"{VERINT_SITE_BASE_ROOT}/{INPUT_PREFIX}/{PRODUCT_INPUT}"
    
        # === Step 2.1: Get control file ===
        control_url = f"{CONTROL_SITE_BASE_ROOT}/{CONTROL_SITE_CONTROL_PATH}/control_file.xlsx"
        control_download_url = get_item_download_url_by_path("control", control_url)
        access_token = get_access_token("control")
        control_file = requests.get(control_download_url, headers={"Authorization": f"Bearer {access_token}"})
        control_file.raise_for_status()
        control_file_bytes = control_file.content

        validate_df = pd.read_excel(io.BytesIO(control_file_bytes))
        validate_df["yyyymm"] = validate_df["yyyymm"].astype(str)
        validate_df["yyyymmdd"] = validate_df["yyyymmdd"].astype(str)
        validate_df = (
            validate_df.drop_duplicates(subset=["yyyymmdd"], keep="last")
        ).copy()
        # === Step 2.2: Find unprocessed items ===
        processed_df = validate_df[validate_df.process == "Y"]
        processed_array = processed_df.yyyymmdd.astype(str).values

        logger.info(f"✅ Already processed: {processed_array}")

        # === Step 2.3: Iterate through folders ===
        today_date = datetime.now().date()        
        yesterday_date = today_date - timedelta(days=1)
        num_days = LOOKBACK_DAYS
        date_list_dt = [yesterday_date - timedelta(days=i) for i in range(num_days - 1, -1, -1)]
        yyyymmdd_folders = [date_obj.strftime('%Y%m%d') for date_obj in date_list_dt]
        yyyymmdd_today = today_date.strftime('%Y%m%d')

        for yyyymmdd in yyyymmdd_folders:
            if yyyymmdd not in processed_array:
                yyyymm = yyyymmdd[:6] # 202511/01
                logger.info(f"📂 Processing folder {input_path}/{yyyymm}/{yyyymmdd} ...")
                yyyymmdd_voice_files = list_files_in_folder("verint", f"{input_path}/{yyyymm}/{yyyymmdd}")
                if len(yyyymmdd_voice_files) == 0:
                    append_dict = {
                        "run_date": yyyymmdd_today,
                        "yyyymm": yyyymm, 
                        "yyyymmdd" : yyyymmdd, 
                        "process": "N",
                        "remark": "file not found"
                    }
                else:
                    asyncio.run(download_files_async_from_sharepoint(yyyymmdd_voice_files))
                    append_dict = {
                        "run_date": yyyymmdd_today,
                        "yyyymm": yyyymm, 
                        "yyyymmdd" : yyyymmdd, 
                        "process": "Y",
                        "remark": ""
                    }
                validate_df = pd.concat([validate_df, pd.DataFrame([append_dict])], ignore_index=True)

        # === Step 2.4: Upload updated control file (overwrite same path) ===
        logger.info("📤 Uploading updated control_file.xlsx back to SharePoint...")
        validate_df = validate_df.sort_values(by='yyyymmdd')
        output_post_buffer = io.BytesIO()
        validate_df.to_excel(output_post_buffer, index=False)
        output_post_buffer.seek(0)

        upload_file_to_sharepoint("control", output_post_buffer, control_url)
    except Exception as e:
        logger.error(f"❌ Unhandled error during 'Step 2: Download files from sharepoint and prompt config': {e}", exc_info=True)

    # Step 3 — Create new batch (always executed)
    try:
        """Scans 'input/', moves files, and creates a new batch job."""
        logger.info("--- Step 3: Creating New Batch ---")
        # === Step 3.1 Move all outdated 'processing/' files back to 'input/' ===
        processing_blobs = list(bucket.list_blobs(prefix=PROCESSING_PREFIX))
        
        unprocessed_files = [b for b in processing_blobs if not b.name.endswith('/') and b.name != PROCESSING_PREFIX]
        for blob in unprocessed_files:
            elapsed_time = datetime.now(thailand_tz) - blob.updated.replace(tzinfo=thailand_tz)
            if elapsed_time.total_seconds() > 24 * 3600:
                try:
                    original_path_part = blob.name[len(PROCESSING_PREFIX):] # e.g., '/yyyymm/yyyymmdd/voice.mav'
                    logger.info(f"==================== original_path_part: {original_path_part}")
                    input_blob_name = f"{INPUT_PREFIX}{original_path_part}" # original_path_part has '/' at index 0
                    
                    # Use copy + delete to "move"
                    new_blob = bucket.copy_blob(blob, bucket, input_blob_name)
                    blob.delete()
                    logger.info(f"Moved unprocessed files in processing back to input: {blob.name} → {input_blob_name}")
                except Exception as e:
                    logger.error(f"Failed to move stale processing file {blob.name} back to input: {e}", exc_info=True)

        # === Step 3.2 Scan all files in input/ ===
        blobs = list(bucket.list_blobs(prefix=INPUT_PREFIX))
        audio_files = []
        for b in blobs:
            # Filter out non-files (e.g., the folder itself)
            if b.name.endswith('/') or b.name == INPUT_PREFIX:
                continue
            try:
                input, yyyymm, yyyymmdd, file_name = b.name.split('/') #['Input', '202601', '20260117', '9156947149050003951_0894268077_093130_90045848_xxxxxx_xxxxxx_T_20260223_000_OUT.wav']
                if yyyymmdd in yyyymmdd_folders:
                    audio_files.append(b)
            except ValueError:
                continue
        if not audio_files:
            logger.info(f"No files found in '{INPUT_PREFIX}'. Skipping Step 3.")
            return

        logger.info(f"Found {len(audio_files)} total files in 'input/'.")
        
        # === Step 3.3 Sort by oldest first (blob updated time) ===
        audio_files.sort(key=lambda b: b.updated)
        
        # === Step 3.4 Take the first BATCH_SIZE files ===
        files_to_batch = audio_files[:BATCH_SIZE]
        logger.info(f"Creating a new batch with the {len(files_to_batch)} oldest files.")
        
        # === Step 3.5. Create jsonl file ===
        jsonl_records = asyncio.run(create_jsonl_file(files_to_batch)) # move file to processing/
            
        # === Step 3.6. Create new batch folder and upload JSONL ===
        now = datetime.now(thailand_tz)
        batch_timestamp = now.strftime('%Y-%m-%dT%H-%M-%S.%f')
        json_timestamp = now.strftime('%Y%m%d%H%M%S')
        
        jsonl_blob_name = f"{BATCH_PREFIX}/{batch_timestamp}/input_json/json_{json_timestamp}.jsonl" # Use .jsonl
        
        jsonl_content = "\n".join(json.dumps(r, ensure_ascii=False) for r in jsonl_records)
        bucket.blob(jsonl_blob_name).upload_from_string(jsonl_content, content_type="application/jsonl")
        
        logger.info(f"Uploaded batch input JSONL to: {jsonl_blob_name}")
        
        # === Step 3.7. Call client.batches.create() ===
        src_uri = f"gs://{PROCESSING_BUCKET}/{jsonl_blob_name}"
        dest_uri_prefix = f"gs://{PROCESSING_BUCKET}/{BATCH_PREFIX}/{batch_timestamp}/output/"
        fail_status = [
            'JOB_STATE_FAILED',
            'JOB_STATE_CANCELLED',
            'JOB_STATE_EXPIRED',
        ]
        try:
            job = genai_client.batches.create(
                model="gemini-2.5-flash",
                src=src_uri,
                config=CreateBatchJobConfig(dest=dest_uri_prefix),
            )
            logger.info(f"Submitted new batch job name: {job.name}")
            logger.info(f"Checking Status from Job name")
            logger.info(f"Source: {src_uri}")
            logger.info(f"Destination: {dest_uri_prefix}")
            time.sleep(5)
            batch_job = genai_client.batches.get(name=job.name) # Initial get
            if batch_job.state.name in fail_status:
                logger.error(f"❌ Initial batch job state is {batch_job.state.name}. Please re-run.")
                    
        except Exception as e:
            logger.error(f"❌ Failed to submit batch job to Gemini API: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"❌ Unhandled error during 'Step 3: Create New Batch': {e}", exc_info=True)

    logger.info("🏁 Batch processing job finished.")

if __name__ == "__main__":
    main()