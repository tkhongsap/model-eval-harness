#!/usr/bin/env python3
# add move unable to process file in archieve to input when read prodiction.jsonl files
from datetime import datetime, timedelta
from typing import Any, Dict
from concurrent.futures import ThreadPoolExecutor
from google.cloud import storage
from google import genai
from google.genai.types import CreateBatchJobConfig, JobState, HttpOptions
from dotenv import load_dotenv
from zoneinfo import ZoneInfo
from pydantic import ValidationError
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
import time
import yaml
import google.auth
import google.auth.exceptions
import pandas as pd
# ------------------------------------------------------------------------
# Import from other file
# ------------------------------------------------------------------------
from src.sharepoint import (
    get_access_token,
    list_folders_in_folder, 
    list_files_in_folder, 
    get_item_download_url_by_path,
    upload_file_to_sharepoint,
    get_drive_id,
    get_site_id,
)
from src.data_model import (
    upload_master_files, 
    upload_daily_files,
    create_daily_bar_chart_daily,
    create_daily_summarize_files,
    upload_daily_files_with_additional
)
from src.output_schema import OutputValidation
from src.ai_oper_log import ai_oper_log

from src.log import Logger
logger = Logger(__name__, env=os.environ.get("LOG_ENVIRONMENT", "prod"))
import copy

# ------------------------------------------------------------------------
# Define Output Schema
# ------------------------------------------------------------------------
# Resolve Pydantic `$ref` references in model JSON schemas so they are
# compatible with BigQuery / Vertex batch inputs (which reject `$ref`).
def resolve_refs(schema: dict) -> dict:
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

# Pre-compute a resolved schema to avoid embedding `$ref` in batch inputs
RAW_RESPONSE_SCHEMA = OutputValidation.model_json_schema()
RESPONSE_SCHEMA = resolve_refs(RAW_RESPONSE_SCHEMA)

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
threadpoolexecutor = ThreadPoolExecutor(max_workers=64)
thailand_tz = ZoneInfo("Asia/Bangkok")

################################### Initialize google config ###################################
storage_client = storage.Client()
# Get model settings
with open("./config/model_setting/mnp_retention.yml", 'r') as file:
    yaml_dict = yaml.safe_load(file)
location = yaml_dict.get("location", "global")
genai_client = genai.Client(
    location=location,
    http_options=HttpOptions(api_version="v1")
)

PROCESSING_BUCKET = os.environ["PROCESSING_BUCKET"]
    
# GCS Folder Layout
INPUT_GCP_PREFIX = os.environ["INPUT_GCP_PREFIX"]
PROCESSING_PREFIX = os.environ["PROCESSING_PREFIX"]
ARCHIVE_PREFIX = os.environ["ARCHIVE_PREFIX"]
BATCH_PREFIX = os.environ["BATCH_PREFIX"]
OUTPUT_EXCEL_PREFIX = os.environ["OUTPUT_EXCEL_PREFIX"]

# Batching Logic
BATCH_SIZE = int(os.environ["BATCH_SIZE"])
MODEL_NAME = os.environ["MODEL_NAME"] # Use 1.5-flash as 2.5 is not a public model name

# SETTINGS FW
LOOKBACK_DAYS = int(os.environ["LOOKBACK_DAYS"])

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
CONTROL_SITE_TRANSACTION_LOG_PATH=os.environ["CONTROL_SITE_TRANSACTION_LOG_PATH"]
CONTROL_SITE_PERFORMANCE_LOG_PATH=os.environ["CONTROL_SITE_PERFORMANCE_LOG_PATH"]
PROJECT_NAME=os.environ["GOOGLE_CLOUD_PROJECT"]
CONTROL_SITE_COST_PATH =  os.environ["CONTROL_SITE_COST_PATH"]

PLAYGROUND_SITE_CLIENT_ID = os.environ["PLAYGROUND_SITE_CLIENT_ID"]
PLAYGROUND_SITE_CLIENT_SECRET = os.environ["PLAYGROUND_SITE_CLIENT_SECRET"]
PLAYGROUND_SITE_TENANT_ID = os.environ["PLAYGROUND_SITE_TENANT_ID"]
PLAYGROUND_SITE_SITE_DOMAIN = os.environ["PLAYGROUND_SITE_SITE_DOMAIN"]
PLAYGROUND_SITE_SITE_PATH = os.environ["PLAYGROUND_SITE_SITE_PATH"]
PLAYGROUND_SITE_BASE_ROOT = os.environ["PLAYGROUND_SITE_BASE_ROOT"]
PLAYGROUND_NETWORK_PATH = os.environ["PLAYGROUND_NETWORK_PATH"]

# Sharedpoint Folder Layout
INPUT_PREFIX = os.environ["INPUT_PREFIX"]
INPUT_PRODUCT = os.environ["INPUT_PRODUCT"]
OUTPUT_PRODUCT = os.environ["OUTPUT_PRODUCT"]
OUTPUT_EXCEL_DAILY_PREFIX_FILENAME = os.environ["OUTPUT_EXCEL_DAILY_PREFIX_FILENAME"]

# passing config function
config_dicts = {
    "PROCESSING_BUCKET": PROCESSING_BUCKET,
    "INPUT_GCP_PREFIX": INPUT_GCP_PREFIX,
    "PROCESSING_PREFIX": PROCESSING_PREFIX,
    "ARCHIVE_PREFIX": ARCHIVE_PREFIX,
    "BATCH_PREFIX": BATCH_PREFIX,
    "BATCH_SIZE": BATCH_SIZE,
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
    "INPUT_PREFIX": INPUT_PREFIX,
    "INPUT_PRODUCT": INPUT_PRODUCT,
    "OUTPUT_EXCEL_PREFIX": OUTPUT_EXCEL_PREFIX,
    "OUTPUT_PRODUCT": OUTPUT_PRODUCT,
    "OUTPUT_EXCEL_DAILY_PREFIX_FILENAME": OUTPUT_EXCEL_DAILY_PREFIX_FILENAME,
    "PLAYGROUND_SITE_CLIENT_ID": PLAYGROUND_SITE_CLIENT_ID,
    "PLAYGROUND_SITE_CLIENT_SECRET": PLAYGROUND_SITE_CLIENT_SECRET,
    "PLAYGROUND_SITE_TENANT_ID": PLAYGROUND_SITE_TENANT_ID,
    "PLAYGROUND_SITE_SITE_DOMAIN": PLAYGROUND_SITE_SITE_DOMAIN,
    "PLAYGROUND_SITE_SITE_PATH": PLAYGROUND_SITE_SITE_PATH,
    "PLAYGROUND_SITE_BASE_ROOT": PLAYGROUND_SITE_BASE_ROOT,
    "PLAYGROUND_NETWORK_PATH": PLAYGROUND_NETWORK_PATH,
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
            destination_blob_name = blob_name.replace(PROCESSING_PREFIX, INPUT_GCP_PREFIX, 1)

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
                blob.delete()
        logger.info(f"✅ Successfully deleted {len(blobs)} files from {folder_prefix}.")
    except Exception as e:
        logger.error(f"Error during bulk deletion of {folder_prefix}: {e}", exc_info=True)
    
def calculate_token_usage(llm_response: Any) -> float:
    """Calculates total audio and text tokens from prompt tokens details."""
    usage = llm_response['response']['usageMetadata']
    prompt_audio_token = 0
    prompt_text_token = 0
    for values in usage.get('promptTokensDetails', []):
        if values['modality'].upper() == "AUDIO":
            prompt_audio_token += int(values['tokenCount'])
        else:
            prompt_text_token += int(values['tokenCount'])
    output_token = 0
    for values in usage.get('candidatesTokensDetails', []):
        if values['modality'].upper() == "TEXT":
            output_token += int(values['tokenCount'])
    # Cost for batch processing
    input_text_cost = prompt_text_token * (0.15 / 1000000)
    input_audio_cost = prompt_audio_token * (0.5 / 1000000)
    output_cost = output_token * (1.25 / 1000000)
    # Convert to THB (1 USD = 33 THB)
    total_cost = (input_text_cost + input_audio_cost + output_cost)*33
    return round(total_cost, 2)

def feed_log_to_sharepoint(lines: list[str], folder_prefix: str, access_token: str, **config_dicts):
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
        audio_duration = (
            data.get('response', {})
            .get("usageMetadata", {})
            .get("billablePromptUsage", {})
            .get("audioDurationSeconds", 0)
        )

        text_input_token = 0
        audio_input_token = 0
        text_output_token = 0

        for token_detail in data.get('response', {}).get("usageMetadata", {}).get("promptTokensDetails", []):
            if token_detail.get("modality") == "AUDIO":
                audio_input_token += token_detail.get("tokenCount", 0)
            elif token_detail.get("modality") == "TEXT":
                text_input_token += token_detail.get("tokenCount", 0)

        for token_detail in data.get('response', {}).get("usageMetadata", {}).get("candidatesTokensDetails", []):
            if token_detail.get("modality") == "TEXT":
                text_output_token += token_detail.get("tokenCount", 0)

        audio_price = (
            text_input_token * text_input_price_usd
            + audio_input_token * audio_input_price_usd
            + text_output_token * audio_output_price_usd
        )

        start_time = data.get("processed_time") # Time when request is sent
        end_time = data.get("response", {}).get("createTime") # Time when response is created
        
        if isinstance(start_time, str) and isinstance(end_time, str):
            # Normalize ISO format: replace 'Z' with '+00:00' for consistent parsing
            start_time_normalized = start_time.replace('Z', '+00:00')
            end_time_normalized = end_time.replace('Z', '+00:00')
            # Use absolute value to handle cases where timestamps might be in wrong order
            process_time = abs((datetime.fromisoformat(end_time_normalized) - datetime.fromisoformat(start_time_normalized)).total_seconds())
        else:
            process_time = 0

        # Use the tokens already calculated above (lines 356-373)
        input_token = text_input_token + audio_input_token
        output_token = text_output_token

        try:
            output = (
                data.get("response", {})
                .get("candidates", [{}])[0]
                .get("content", {})
                .get("parts", [{}])[0]
                .get("text", "")
            )
            OutputValidation.model_validate_json(output)

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
                f"https://{VERINT_SITE_SITE_DOMAIN}/{VERINT_SITE_SITE_PATH}/{VERINT_SITE_BASE_ROOT}/{INPUT_PREFIX}/{INPUT_PRODUCT}",
                f"{file_uri.split('/')[-2][0:4]}/{file_uri.split('/')[-2]}",
                file_uri.split('/')[-1],
                f"{audio_duration // 60}.{(audio_duration % 60):02d}",
                "Pass",
                "",
                f"{process_time*1000}", 
                input_token,
                output_token,
                audio_price
            ])
        except Exception as e:
            logger.error(f"Datadate: {file_uri.split('/')[-2]} Failed to validate output JSON: {e}.")
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
                f"https://{VERINT_SITE_SITE_DOMAIN}/{VERINT_SITE_SITE_PATH}/{VERINT_SITE_BASE_ROOT}/{INPUT_PREFIX}/{INPUT_PRODUCT}",
                f"{file_uri.split('/')[-2][0:4]}/{file_uri.split('/')[-2]}",
                file_uri.split('/')[-1],
                f"{audio_duration // 60}.{(audio_duration % 60):02d}",
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
    ai_oper_log(transaction_df=transaction_df, tz=thailand_tz)
    yyyymmdd = folder_prefix.split('T')[0].split('/')[-1].replace("-", "")
    transaction_log_path = f"{CONTROL_SITE_BASE_ROOT}/{CONTROL_SITE_TRANSACTION_LOG_PATH}/{yyyymmdd[:4]}"
    log_name = f"transaction_log_{yyyymmdd[0:6]}.csv"
    transaction_path = f"{transaction_log_path}/{log_name}"

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
            run_date_df = data_date_df[data_date_df["run_date"] == run_date]

            total_transaction = len(run_date_df)
            total_completed = (run_date_df["status_pass_failed_retry"] == "Pass").sum()
            total_failed = (run_date_df["status_pass_failed_retry"] == "Failed").sum()
            min_time = run_date_df['start_time'].min()
            max_time = run_date_df['end_time'].max()
            seconds = (max_time - min_time).total_seconds()
            if pd.isna(seconds):
                minutes = "0.00"
            else:
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

    performance_new_df = pd.DataFrame(performance_rows)

    yyyymmdd = folder_prefix.split('T')[0].split('/')[-1].replace("-", "")
    performance_log_path = f"{CONTROL_SITE_BASE_ROOT}/{CONTROL_SITE_PERFORMANCE_LOG_PATH}/{yyyymmdd[:4]}"
    log_name = f"performance_log_{yyyymmdd[0:6]}.csv"
    performance_path = f"{performance_log_path}/{log_name}"

    # Load existing performance log and merge with new data
    try:
        perf_url = get_item_download_url_by_path("control", performance_path)
        perf_file = requests.get(
            perf_url,
            headers={"Authorization": f"Bearer {access_token}"}
        )
        perf_file.raise_for_status()

        # Load existing CSV into DataFrame
        existing_perf_df = pd.read_csv(io.BytesIO(perf_file.content))
        existing_perf_df['data_date'] = existing_perf_df['data_date'].astype(str)
        existing_perf_df['run_date'] = existing_perf_df['run_date'].astype(str)
        
        # Concatenate existing and new performance data
        performance_combined_df = pd.concat([existing_perf_df, performance_new_df], ignore_index=True)
        
        # Remove duplicates: keep the latest record for each (data_date, run_date) combination
        # This prevents double-counting if the same batch is processed multiple times
        performance_final_df = performance_combined_df.drop_duplicates(
            subset=['data_date', 'run_date'], 
            keep='last'
        )
        
        logger.info(f"Merged existing performance log with {len(performance_new_df)} new records")
    except Exception as e:
        logger.error(f"Could not load existing performance log: {performance_path}. Creating new file.")
        performance_final_df = performance_new_df

    performance_final_df = performance_final_df.sort_values(by=['data_date', 'run_date'])
    csv_buffer = io.BytesIO()
    performance_final_df.to_csv(csv_buffer, index=False, encoding="utf-8-sig")
    csv_buffer.seek(0)

    upload_file_to_sharepoint("control", csv_buffer, performance_path)

    logger.info(f"Performance log uploaded to SharePoint: {performance_path}") 

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
            file_uri_split =  file_uri.split("/") # file_uri : gs://sentiment-retention-bucket-01/processing/202511/20251111/9155832402870003681_0828192340_090028_90003867_firstname_lastname_title_T.wav
            base_file_name = os.path.basename(file_uri)
            tmp = base_file_name.replace(".wav", "").split("_") # "call_id|msisdn|call_time|agent_id|first_name|last_name|provider(D/T)|record_date|record_duration|record_type(IN/OUT)".wav
            call_id = safe_list_get(tmp, 0, "")
            phone_number = safe_list_get(tmp, 1, "")
            call_time = safe_list_get(tmp, 2, "")
            agent_id = safe_list_get(tmp, 3, "")
            first_name = safe_list_get(tmp, 4, "")
            last_name = safe_list_get(tmp, 5, "")
            provider = safe_list_get(tmp, 6, "")
            record_date = safe_list_get(tmp, 7, safe_list_get(file_uri_split, -2, ""))
            record_duration = safe_list_get(tmp, 8, 0)
            record_type = safe_list_get(tmp, 9, "")

            # For calculate to minutes
            call_duration_sec = int(record_duration)
            call_duration = f'{call_duration_sec // 60}.{(call_duration_sec % 60):02d}'  # Convert seconds to minutes
            
            file_name = base_file_name
            user = f"{first_name.capitalize()} {last_name.capitalize()}"

            input_folder  = "/".join(file_uri_split[:-1])
            output_folder = input_folder.replace("processing", "archive")
            
            process_time      = data.get("processed_time", {})
            process_time_gmt7 = datetime.fromisoformat(process_time) + timedelta(hours=7)
            run_date          = process_time_gmt7.isoformat()
            llm_cost          = None

            # Post processing AI result 
            try:
                if data['status'] != "":
                    status_obj = json.loads(data['status'])
                    raise Exception(f"code: {status_obj['code']} : {status_obj['message']}")
                raw_text = data['response']['candidates'][0]['content']['parts'][0]['text']
                llm_cost = calculate_token_usage(data)
                json_str = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_text.strip())

                # Validate and fix data using Pydantic model (handles deduplication)
                validated_data = OutputValidation.model_validate_json(json_str)
                ai_result = validated_data.model_dump()

                # print(json.dumps(ai_result, indent=4))
                # print("==================================")
                
                secondary = {}
                if ai_result.get("reasons", {}).get("secondary", None) is not None:
                    secondary = ai_result.get("reasons", {}).get("secondary", {})
                    
                third = {}
                if ai_result.get("reasons", {}).get("third", None) is not None:
                    third = ai_result.get("reasons", {}).get("third", {})
                
                all_data.append({
                    'call_id': call_id, 
                    'phone_number': phone_number,
                    'call_month': record_date[:-2],
                    'call_date' : record_date,
                    'call_time' : call_time,
                    'cost(thb)' : llm_cost,
                    'call_duration(min)' : call_duration,
                    'file_name' : file_name,
                    'user' : user,
                    # ================== AI result ==================
                    'call_result' : ai_result.get("call_result", ""),
                    'main' : ai_result.get("reasons", {}).get("main", {}).get("reason", ""),
                    'secondary' : secondary.get("reason", ""),
                    'third' : third.get("reason", ""),
                    'keyword(main)' : ai_result.get("reasons", {}).get("main", {}).get("keyword", ""),
                    'keyword(secondary)' : secondary.get("keyword", ""),
                    'keyword(third)' : third.get("keyword", ""),
                    'call_event_detection' : ai_result.get("call_event_detection", ""),
                    'AI_recommendation' : ai_result.get("ai_recommendation", ""),
                    'additional_fields' : ai_result.get("additional_fields", {}),
                    # ================== AI result ==================
                    'input_folder' : input_folder,
                    'output_folder' : output_folder,
                    'run_date' : run_date,
                    'status' : 'success',
                    'message' : ""
                })
                passed_files_uri_set.add(file_uri)
                
            except (json.JSONDecodeError, KeyError, IndexError, TypeError, Exception, ValidationError) as e:
                logger.error(f"Datadate: {record_date} Failed to parse line {e}.")
                all_data.append({
                    'call_id': call_id, 
                    'phone_number': phone_number,
                    'call_month': record_date[:-2],
                    'call_date' : record_date,
                    'call_time' : call_time,
                    'cost(thb)' : llm_cost,
                    'call_duration(min)' : call_duration,
                    'file_name' : file_name,
                    'user' : user,
                    # ================== AI result (Empty for fail) ==================
                    'call_result' : "",
                    'main' : "",
                    'secondary' : "",
                    'third' : "",
                    'keyword(main)' : "",
                    'keyword(secondary)' : "",
                    'keyword(third)' : "",
                    'call_event_detection' : "",
                    'AI_recommendation' : "",
                    'additional_fields' : {},
                    # ================== AI result ==================
                    'input_folder' : input_folder,
                    'output_folder' : output_folder,
                    'run_date' : run_date,
                    'status' : 'fail',
                    'message' : f"{str(e)}"
                })
                failed_files_uri_set.add(file_uri if 'file_uri' in locals() else 'unknown')

    verint_access_token = get_access_token("verint")
    control_access_token = get_access_token("control")

    # Create a user_all_data list without 'additional_fields'
    user_all_data = []
    for row in all_data:
        filtered_fields = {}
        for key, value in row.items():
            if key != 'additional_fields':
                filtered_fields[key] = value
        user_all_data.append(filtered_fields)

    # 4. Append data to master.xlsx file
    master_df = upload_master_files(user_all_data, verint_access_token, **config_dicts) 

    # 5.1. Append data to daily.xlsx file
    upload_daily_files(user_all_data, verint_access_token, **config_dicts)

    # 5.2 Append data to daily.xlsx file (with additional_fields)
    upload_daily_files_with_additional(all_data, control_access_token, **config_dicts)

    # 6. Create Bar-Chart plot
    all_data_df = pd.DataFrame(user_all_data)
    grouped_series = all_data_df.groupby('call_month')['call_date'].unique()
    create_daily_bar_chart_daily(master_df, grouped_series, **config_dicts)

    # 7. Create Text Summary.txt file
    control_site_id = get_site_id("control", control_access_token)
    control_drive_id = get_drive_id(control_access_token, control_site_id)

    verint_site_id = get_site_id("verint", verint_access_token)
    verint_drive_id = get_drive_id(verint_access_token, verint_site_id)

    for i in range(3):
        try:
            create_daily_summarize_files(
                master_df=master_df,
                data_list=user_all_data,
                control_dict={
                    "control_site_id": control_site_id,
                    "control_access_token": control_access_token,
                    "control_drive_id": control_drive_id,
                },
                verint_dict={
                    "site_id": verint_site_id,
                    "access_token": verint_access_token,
                    "drive_id": verint_drive_id,
                },
                **config_dicts
            )
            logger.info("Daily summary file created successfully.")
            break
        except Exception as e:
            logger.error(f"Attempt {i+1} - Failed to create daily summary file: {e}")

    # 8.Feed Log
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
    try:
        # print("+++++++++++++++++++++++++++++++++++++++++++++++++++++")
        # print(file_meta["path"])
        # Extract the path from file metadata and clean it up
        # Removes '/drive/root:/' prefix (SharePoint specific) and strips slashes
        # Example: '/drive/root:/202511/20251106' → '202511/20251106'
        raw_path = file_meta["path"].replace(f"/drive/root:/", "", 1).strip("/")
        
        # Combine path and filename to create the full GCS path
        # Example: '202511/20251106/file.wav'
        gcs_path = f"{raw_path}/{file_meta['file_name']}"
        gcs_path = (
            gcs_path.replace(f"{VERINT_SITE_BASE_ROOT}/", "")
            .replace(INPUT_PREFIX, INPUT_GCP_PREFIX)
            .replace(f"{INPUT_PRODUCT}/", "")
        )

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

        logger.info(f"file path : {gcs_path}")
        
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
        
        async with session.get(download_url) as resp:
            resp.raise_for_status()
            data = await resp.read()

        if not file_meta["file_name"].lower().endswith('.wav'):
            logger.error(f"❌ Not a WAV file: {file_meta['file_name']}")
            return
        
        mime_type = "audio/wav"

        await loop.run_in_executor(threadpoolexecutor, blob.upload_from_string, data, mime_type)
        
        # Log success message
        logger.info(f"✅ Uploaded: gs://{PROCESSING_BUCKET}/{gcs_path}")
    except Exception as e:
        logger.error(f"❌ Error processing {file_meta['file_name']}: {e}")

async def download_files_async_from_sharepoint(files):
    """
    Main orchestrator function that manages concurrent file downloads/uploads.
    Processes all files in the list concurrently with controlled parallelism.
    
    Args:
        files: List of file metadata dictionaries, each containing 'path' and 'file_name'
    """
    verint_access_token = get_access_token("verint") 
    async with aiohttp.ClientSession(headers={"Authorization": f"Bearer {verint_access_token}"}) as session:
        gcs_client = storage.Client(project=project_id)
        sem = asyncio.Semaphore(int(os.environ.get("MAX_CONCURRENT_UPLOADS",10)))
        async def bounded_task(file_meta):
            async with sem:
                await download_upload(file_meta, session, gcs_client)

        await asyncio.gather(*(bounded_task(f) for f in files))


# ------------------------------------------------------------------------
# Step 3: Create New Batch
# ------------------------------------------------------------------------

async def create_jsonl_record(blob, session, gcs_client, prompt_text, bucket) -> dict | None:
    """Creates the JSONL record structure for a single file."""
    try:
        # 1. Initial loop for sync task
        loop = asyncio.get_event_loop()
        
        # 2. Move file from 'input/' to 'processing/'
        original_path_part = blob.name[len(INPUT_GCP_PREFIX):] # e.g., 'yyyymm/yyyymmdd/voice.mav'
        processing_blob_name = f"{PROCESSING_PREFIX}{original_path_part}"
        
        # Use copy + delete to "move"
        new_blob = await loop.run_in_executor(None, bucket.copy_blob, blob, bucket, processing_blob_name) # *args
        await loop.run_in_executor(None, blob.delete)
        
        # 3. Create JSONL record using the *new* processing path
        gcs_uri = f"gs://{PROCESSING_BUCKET}/{new_blob.name}"
        
        mime_type = new_blob.content_type
        if not mime_type: # Guess if metadata is missing
            if new_blob.name.lower().endswith(".wav"):
                mime_type = "audio/wav"
            # elif new_blob.name.lower().endswith(".mp3"):
            #     mime_type = "audio/mpeg"
            else:
                logger.warning(f"Skipping {new_blob.name}: Unknown MIME type.")
        
        # Get model settings
        with open("./config/model_setting/mnp_retention.yml", 'r') as file:
            yaml_dict = yaml.safe_load(file)
        generation_config = yaml_dict.get("generation_config")
        generation_config.update({"responseSchema": RESPONSE_SCHEMA})
        logger.debug(f"Generation config: {generation_config}")

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
            "generationConfig": generation_config
        }
    }

    except Exception as e:
        logger.error(f"Failed to move or process file {blob.name}: {e}", exc_info=True)
        return None


async def create_jsonl_file(files_to_batch) -> list | None:
    input_path = f"{CONTROL_SITE_BASE_ROOT}/{CONTROL_SITE_PROMPTS_ROOT}"
    
    loop = asyncio.get_event_loop()
    # de-blocking task in asyncio task
    prompt_url = f"{input_path}/prompt.txt"
    prompt_download_url = await loop.run_in_executor(
        None,
        get_item_download_url_by_path,
        "control",
        prompt_url
    )
    # define aiohttp
    verint_access_token = get_access_token("verint")
    async with aiohttp.ClientSession(headers={"Authorization": f"Bearer {verint_access_token}"}) as session:
        # perform rest api to get file
        async with session.get(prompt_download_url) as resp:
            resp.raise_for_status()
            prompt_file_bytes = await resp.read()

        # decode byte to string 
        user_prompt = prompt_file_bytes.decode("utf-8").strip()
        with open("./config/system_prompt/mnp_retention.yml", 'r') as file:
            yaml_dict = yaml.safe_load(file)
        system_prompt = yaml_dict.get("system_prompt", "")
        prompt_text = system_prompt.replace("${USER_PROMPT}", user_prompt)

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
        
        # List all top-level folders in 'batchs/'
        blob_iterator = bucket.list_blobs(prefix=BATCH_PREFIX, delimiter='/')
        _ = list(blob_iterator)  # Consume the iterator
        batch_folders = list(blob_iterator.prefixes)
        
        if not batch_folders:
            logger.info("No batch folders found in 'batchs/'. Skipping step 1.")

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
        input_path = f"{VERINT_SITE_BASE_ROOT}/{INPUT_PREFIX}{INPUT_PRODUCT}"
    
        # === Step 2.1: Get control file ===
        control_url = f"{CONTROL_SITE_BASE_ROOT}/{CONTROL_SITE_CONTROL_PATH}/control_file.xlsx"
        control_download_url = get_item_download_url_by_path("control", control_url)
        control_access_token = get_access_token("control")
        control_file = requests.get(control_download_url, headers={"Authorization": f"Bearer {control_access_token}"})
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
        un_process_files = [b for b in processing_blobs if not b.name.endswith('/') and b.name != PROCESSING_PREFIX]
        for blob in un_process_files:
            elapsed_time = datetime.now(thailand_tz) - blob.updated.replace(tzinfo=thailand_tz)
            if elapsed_time.total_seconds() > 24 * 3600:
                try:
                    original_path_part = blob.name[len(PROCESSING_PREFIX):] # e.g., 'yyyymm/yyyymmdd/voice.mav'
                    logger.info(f"==================== original_path_part: {original_path_part}")
                    input_blob_name = f"{INPUT_GCP_PREFIX}{original_path_part}"
                    
                    # Use copy + delete to "move"
                    new_blob = bucket.copy_blob(blob, bucket, input_blob_name)
                    blob.delete()
                    logger.info(f"Moved un-process files in processing back to input: {blob.name} → {input_blob_name}")
                except Exception as e:
                    logger.error(f"Failed to move stale processing file {blob.name} back to input: {e}", exc_info=True)

        # === Step 3.2 Scan all files in input/ ===
        blobs = list(bucket.list_blobs(prefix=INPUT_GCP_PREFIX))

        # Filter out non-files (e.g., the folder itself)
        audio_files = [b for b in blobs if not b.name.endswith('/') and b.name != INPUT_GCP_PREFIX]
        if not audio_files:
            logger.info("No files found in 'input/'. Skipping Step 3.")
            return

        logger.info(f"Found {len(audio_files)} total files in 'input/'.")
        
        # === Step 3.3 Sort by oldest first (blob updated time) ===
        audio_files.sort(key=lambda b: b.updated)
        
        # === Step 3.4 Take the first BATCH_SIZE files ===
        files_to_batch = audio_files[:BATCH_SIZE]
        logger.info(f"Creating a new batch with the {len(files_to_batch)} oldest files.")
        
        # === Step 3.5. Create jsonl file ===
        jsonl_records = asyncio.run(create_jsonl_file(files_to_batch))
            
        # === Step 3.6. Create new batch folder and upload JSONL ===
        now = datetime.now(thailand_tz)
        batch_timestamp = now.strftime('%Y-%m-%dT%H-%M-%S.%f')
        json_timestamp = now.strftime('%Y%m%d%H%M%S')
        
        batch_folder_prefix = f"{BATCH_PREFIX}{batch_timestamp}/"
        jsonl_blob_name = f"{batch_folder_prefix}input_json/json_{json_timestamp}.jsonl" # Use .jsonl
        
        jsonl_content = "\n".join(json.dumps(r, ensure_ascii=False) for r in jsonl_records)
        bucket.blob(jsonl_blob_name).upload_from_string(jsonl_content, content_type="application/jsonl")
        
        logger.info(f"Uploaded batch input JSONL to: {jsonl_blob_name}")
        
        # === Step 3.7. Call client.batches.create() ===
        src_uri = f"gs://{PROCESSING_BUCKET}/{jsonl_blob_name}"
        dest_uri_prefix = f"gs://{PROCESSING_BUCKET}/{batch_folder_prefix}output/"
        fail_status = set([
            'JOB_STATE_FAILED',
            'JOB_STATE_CANCELLED',
            'JOB_STATE_EXPIRED',
        ])
        try:
            job = genai_client.batches.create(
                model=config_dicts.get("MODEL_NAME", "gemini-2.5-flash"),
                src=src_uri,
                config=CreateBatchJobConfig(dest=dest_uri_prefix),
            )
            logger.info(f"🚀 Submitted new batch job name: {job.name}")
            logger.info(f"  Source: {src_uri}")
            logger.info(f"  Destination: {dest_uri_prefix}")
            logger.info(f"Checking Status from Job name")
            batch_job = genai_client.batches.get(name=job.name) # Initial get
            time.sleep(5)
            if batch_job.state.name in fail_status:
                logger.error(f"❌ Initial batch job state is {batch_job.state.name}. Exiting creating batch job.")
                raise Exception(f"Batch job failed with state: {batch_job.state.name}")
                    
        except Exception as e:
            logger.error(f"❌ Failed to submit batch job to Gemini API: {e}", exc_info=True)
            raise
    except Exception as e:
        logger.error(f"❌ Unhandled error during 'Step 3: Create New Batch': {e}", exc_info=True)
        raise

    logger.info("🏁 Batch processing job finished.")

if __name__ == "__main__":
    main()