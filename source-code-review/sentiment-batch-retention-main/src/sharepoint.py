from msal import ConfidentialClientApplication
from dotenv import load_dotenv
import requests
import os
from datetime import datetime
from zoneinfo import ZoneInfo
import json
import time
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import PatternFill, Border, Side, Alignment, Font
from openpyxl.utils import get_column_letter
import io
import google.auth
from src.log import logger

load_dotenv(override=True)
        
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

SANDBOX_SITE_CLIENT_ID = os.environ["SANDBOX_SITE_CLIENT_ID"]
SANDBOX_SITE_CLIENT_SECRET = os.environ["SANDBOX_SITE_CLIENT_SECRET"]
SANDBOX_SITE_TENANT_ID = os.environ["SANDBOX_SITE_TENANT_ID"]
SANDBOX_SITE_SITE_DOMAIN = os.environ["SANDBOX_SITE_SITE_DOMAIN"]
SANDBOX_SITE_SITE_PATH = os.environ["SANDBOX_SITE_SITE_PATH"]
SANDBOX_SITE_BASE_ROOT = os.environ["SANDBOX_SITE_BASE_ROOT"] 

LOG_NAME = os.environ["LOG_NAME"]

thailand_tz = ZoneInfo("Asia/Bangkok")

scope = ["https://graph.microsoft.com/.default"]

verint_authority = f"https://login.microsoftonline.com/{VERINT_SITE_TENANT_ID}"
verint_app = ConfidentialClientApplication(VERINT_SITE_CLIENT_ID, authority=verint_authority, client_credential=VERINT_SITE_CLIENT_SECRET)

control_authority = f"https://login.microsoftonline.com/{CONTROL_SITE_TENANT_ID}"
control_app = ConfidentialClientApplication(CONTROL_SITE_CLIENT_ID, authority=control_authority, client_credential=CONTROL_SITE_CLIENT_SECRET)

sandbox_authority = f"https://login.microsoftonline.com/{SANDBOX_SITE_TENANT_ID}"
sandbox_app = ConfidentialClientApplication(SANDBOX_SITE_CLIENT_ID, authority=sandbox_authority, client_credential=SANDBOX_SITE_CLIENT_SECRET)

def get_access_token(site_name: str):
    global verint_app
    global control_app
    global sandbox_app
    access_token = ""
    try:
        if site_name == "verint": # Verint Voice File for AI
            token_response = verint_app.acquire_token_for_client(scopes=scope) # access_token, expires_in, token_source
            if token_response["expires_in"] < 1800:
                verint_app = ConfidentialClientApplication(VERINT_SITE_CLIENT_ID, authority=verint_authority, client_credential=VERINT_SITE_CLIENT_SECRET)
                token_response = verint_app.acquire_token_for_client(scopes=scope)
            access_token = token_response["access_token"]
        elif site_name == "control": # AI and Automation Team Control Management
            token_response = control_app.acquire_token_for_client(scopes=scope) # access_token, expires_in, token_source
            if token_response["expires_in"] < 1800:
                control_app = ConfidentialClientApplication(CONTROL_SITE_CLIENT_ID, authority=control_authority, client_credential=CONTROL_SITE_CLIENT_SECRET)
                token_response = control_app.acquire_token_for_client(scopes=scope)
            access_token = token_response["access_token"]
        elif site_name == "sandbox": # AI-Automation
            token_response = sandbox_app.acquire_token_for_client(scopes=scope) # access_token, expires_in, token_source
            if token_response["expires_in"] < 1800:
                sandbox_app = ConfidentialClientApplication(SANDBOX_SITE_CLIENT_ID, authority=sandbox_authority, client_credential=SANDBOX_SITE_CLIENT_SECRET)
                token_response = sandbox_app.acquire_token_for_client(scopes=scope)
            access_token = token_response["access_token"]
        else:
            logger.error(f"invalid site_name while get access token: {site_name}")
        return access_token
    except Exception as e:
        logger.error(f"Error get access token: {str(e)}")

    
def get_site_id(site_name: str, access_token: str):
    site_url = ""
    try:
        if site_name == "verint":
            site_url = f"https://graph.microsoft.com/v1.0/sites/{VERINT_SITE_SITE_DOMAIN}:{VERINT_SITE_SITE_PATH}"
        elif site_name == "control":
            site_url = f"https://graph.microsoft.com/v1.0/sites/{CONTROL_SITE_SITE_DOMAIN}:{CONTROL_SITE_SITE_PATH}"
        elif site_name == "sandbox":
            site_url = f"https://graph.microsoft.com/v1.0/sites/{SANDBOX_SITE_SITE_DOMAIN}:{SANDBOX_SITE_SITE_PATH}"
        else:
            logger.error(f"invalid site_name while get site id: {site_name}")
        site_resp = requests.get(site_url, headers={"Authorization": f"Bearer {access_token}"})
        site_id = site_resp.json()["id"]
        return site_id
    except Exception as e:
        logger.error(f"Error get site id: {str(e)}")
    
def get_drive_id(access_token: str, site_id: str):
    drive_url = f"https://graph.microsoft.com/v1.0/sites/{site_id}/drive"
    headers = {"Authorization": f"Bearer {access_token}"}
    drive_resp = requests.get(drive_url, headers=headers)
    drive_resp.raise_for_status()
    drive_id = drive_resp.json()["id"]
    return drive_id

def list_folders_in_folder(site_name: str, input_folder_name):
    try:
        access_token = get_access_token(site_name)
        site_id = get_site_id(site_name, access_token)

        folder_url = f"https://graph.microsoft.com/v1.0/sites/{site_id}/drive/root:/{input_folder_name}:/children"
        folder_resp = requests.get(folder_url, headers={"Authorization": f"Bearer {access_token}"})
        root_dir = folder_resp.json().get("value", [])
    
        folder_list = []
    
        for item in root_dir:
            if item.get("folder"):
                folder_name = item.get("name")
                folder_list.append(folder_name)
        return folder_list
    except Exception as e:
        logger.error(f"Error list folder in folder", extra={'json_payload': str(e)})
        return []

def list_files_in_folder(site_name: str, input_folder_name):
    try:
        access_token = get_access_token(site_name)
        site_id = get_site_id(site_name, access_token)
        folder_url = f"https://graph.microsoft.com/v1.0/sites/{site_id}/drive/root:/{input_folder_name}:/children"
    
        file_list = []
    
        while folder_url:
            folder_resp = requests.get(folder_url, headers={"Authorization": f"Bearer {access_token}"})
            response_data = folder_resp.json()
            yyyymmdd_dir = response_data.get("value", [])      
                        
            for item in yyyymmdd_dir:                
                file_name = item.get("name", None)  
                logger.info(f"SharePoint Found file name: {file_name}")
                file_id = item.get("id", None)
                created_date_time = item.get("createdDateTime", None)
                path = item.get("parentReference", {}).get("path", None)              
                if not item.get("folder") and file_name.lower().endswith(".wav"): # Check for .wav extension (case-insensitive)
                    file_list.append({
                        "file_name": file_name, 
                        "file_id": file_id,
                        "created_date_time": created_date_time,
                        'path': path
                    })
                else:
                    logger.warning(f"Found non-JSON file in folder '{input_folder_name}': file name {file_name}")
            folder_url = response_data.get("@odata.nextLink")
        logger.info(f"Found {len(file_list)} files in {input_folder_name}")
        return file_list
    except Exception as e:
        logger.error(f"Error list folder in folder", extra={'json_payload': str(e)})
        return []

def list_file_name_in_sharepoint_folder(site_name: str, folder_path: str) -> list:
    """
    Lists all file names in a specified SharePoint folder.
    """
    access_token = get_access_token(site_name)
    site_id = get_site_id(site_name, access_token)
    files = []
    folder_url = f"https://graph.microsoft.com/v1.0/sites/{site_id}/drive/root:/{folder_path}:/children"
    headers = {"Authorization": f"Bearer {access_token}"}
    
    try:
        while folder_url:
            response = requests.get(folder_url, headers=headers)
            response.raise_for_status()  # Raises an HTTPError for bad responses
            response_data = response.json()
            
            # Extract file names from the response
            for item in response_data.get("value", []):
                # The "file" facet indicates it's a file, not a folder
                if "file" in item:
                    files.append(item.get("name"))
            
            # Check for pagination (if there are more items than returned in the first call)
            folder_url = response_data.get("@odata.nextLink")     
        return files
        
    except requests.exceptions.RequestException as e:
        logger.error(f"Error requesting files from folder '{folder_path}': {e}")
        return []
    except Exception as e:
        logger.error(f"An unexpected error occurred: {e}")
        return []

def get_item_download_url_by_path(site_name: str, item_path: str) -> str | None:
    """
    Gets the download URL for a SharePoint item (file or folder) by its path.
    Returns the download URL if found, None if not found, or raises an exception for other errors.
    """
    access_token = get_access_token(site_name)
    site_id = get_site_id(site_name, access_token)
    item_url = f"https://graph.microsoft.com/v1.0/sites/{site_id}/drive/root:/{item_path}"
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        item_resp = requests.get(item_url, headers=headers)
        item_resp.raise_for_status()
        download_url = item_resp.json().get("@microsoft.graph.downloadUrl")
        return download_url
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            logger.info(f"Info request item download_url for '{item_path}' not found")
        else:
            logger.error(f"Error request getting item download_url for '{item_path}'", extra={'json_payload': str(e)})
            raise
    except Exception as e:
        logger.error(f"Error exception getting item download_url for '{item_path}'", extra={'json_payload': str(e)})
        raise

def get_item_id_by_path(access_token: str, site_id: str, item_path: str) -> str | None:
    """
    Resolves a SharePoint item (file or folder) path to its ID.
    The item_path should be relative to the drive root (e.g., "input/202301/20230101/file.wav").
    Returns the item ID if found, None if not found, or raises an exception for other errors.
    """
    item_url = f"https://graph.microsoft.com/v1.0/sites/{site_id}/drive/root:/{item_path}"
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        item_resp = requests.get(item_url, headers=headers)
        item_resp.raise_for_status()
        item_id = item_resp.json()["id"]
        return item_id
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            logger.info(f"Info request item ID for '{item_path}' not found")
        else:
            logger.error(f"Error request getting item ID for '{item_path}'", extra={'json_payload': str(e)})
            raise
    except Exception as e:
        logger.error(f"Error exception getting item ID for '{item_path}'", extra={'json_payload': str(e)})
        raise

def move_sharepoint_file(access_token: str, drive_id: str, file_id: str, destination_folder_id: str, destination_folder_name: str, new_file_name: str = None) -> bool:
    """
    Moves a file within SharePoint to a new destination folder.
    Optionally, the file can be renamed during the move.
    """
    move_url = f"https://graph.microsoft.com/v1.0/drives/{drive_id}/items/{file_id}"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Prefer": "bypass-shared-lock"
    }
    payload = {
        "parentReference": {
            "id": destination_folder_id
        }
    }
    if new_file_name:
        payload["name"] = new_file_name

    try:
        move_resp = requests.patch(move_url, headers=headers, json=payload)
        move_resp.raise_for_status() # Raise an exception for HTTP errors
        logger.info(f"Successfully moved file '{new_file_name}' to '{destination_folder_name}'")
        return True
    except requests.exceptions.RequestException as e:
        logger.error(f"Error request moving file '{new_file_name}' to '{destination_folder_name}'", extra={'json_payload': str(e)})
    except Exception as e:
        logger.error(f"Error exception moving file '{new_file_name}' to '{destination_folder_name}'", extra={'json_payload': str(e)})
        raise

def upload_file_to_sharepoint(site_name: str, byte_buffer, url):
    access_token = get_access_token(site_name)
    site_id = get_site_id(site_name, access_token)
    drive_id = get_drive_id(access_token, site_id)
    upload_url = f"https://graph.microsoft.com/v1.0/sites/{site_id}/drive/root:/{url}:/content"

    upload_resp = requests.put(
        upload_url,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/octet-stream",
        },
        data=byte_buffer.getvalue(),
    )

    if upload_resp.status_code in (200, 201):
        logger.info(f"✅ file successfully updated: {url}")
    elif upload_resp.status_code == 423:
        try:
            logger.warning(f"Response status 423 Someone currently open '{url}'")
            logger.info(f"Retry uploading '{url}' in sharepoint")
                    
            file_id = get_item_id_by_path(access_token, site_id, url)
            url_split = url.split("/")
            destination_folder = "/".join(url_split[:-1])
            destination_folder_id = get_item_id_by_path(access_token, site_id, destination_folder)
                    
            
            file_name_split = url_split[-1].split(".")
            timestamp = datetime.now(thailand_tz).strftime("%Y-%m-%d %H-%M-%S")
            move_sharepoint_file(access_token, drive_id, file_id, destination_folder_id, destination_folder, f"archive_{file_name_split[0]}_at_({timestamp}).xlsx")
                
            upload_resp = requests.put(upload_url, headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/octet-stream",
            }, data=byte_buffer.getvalue())
            upload_resp.raise_for_status() # Raise an exception for HTTP errors
                
            logger.info(f"Successfully retry uploaded dataframe to sharepoint folder '{destination_folder}': file name '{url_split[-1]}'.")
        except Exception as e:
            logger.error(f"Error exception retry uploading dataframe '{url_split[-1]}' to sharepoint folder '{destination_folder}'", extra={'json_payload': str(e)})
            raise
    else:
        logger.info(f"❌ Failed to upload file: {upload_resp.status_code} - {upload_resp.text}")