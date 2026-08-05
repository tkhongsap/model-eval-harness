import os
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv
from msal import ConfidentialClientApplication

from .share_log import logger

load_dotenv(override=True)

# Sharepoint Output site
FRAUD_SITE_CLIENT_ID = os.environ["FRAUD_SITE_CLIENT_ID"]
FRAUD_SITE_CLIENT_SECRET = os.environ["FRAUD_SITE_CLIENT_SECRET"]
FRAUD_SITE_TENANT_ID = os.environ["FRAUD_SITE_TENANT_ID"]
FRAUD_SITE_SITE_DOMAIN = os.environ["FRAUD_SITE_SITE_DOMAIN"]
FRAUD_SITE_SITE_PATH = os.environ["FRAUD_SITE_SITE_PATH"]
FRAUD_SITE_BASE_ROOT = os.environ["FRAUD_SITE_BASE_ROOT"]

INPUT_FOLDER = os.environ["INPUT_FOLDER"]
BACKUP_FOLDER = os.environ["BACKUP_FOLDER"]
OUTPUT_FOLDER = os.environ["OUTPUT_FOLDER"]
ARCHIVE_FOLDER = os.environ["ARCHIVE_FOLDER"]

# Sharepoint Control site
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

BATCH_SIZE = os.environ["BATCH_SIZE"]

RECIPIENT_EMAIL = os.environ["RECIPIENT_EMAIL"]
TEAM_EMAIL = os.environ["TEAM_EMAIL"]
SENDER_EMAIL = os.environ["SENDER_EMAIL"]

GCS_BUCKET_NAME = os.environ["GCS_BUCKET_NAME"]

S3_AWS_ACCESS_KEY = os.environ["S3_AWS_ACCESS_KEY"]
S3_AWS_SECRET_KEY = os.environ["S3_AWS_SECRET_KEY"]
S3_BUCKET_NAME = os.environ["S3_BUCKET_NAME"]

thailand_tz = ZoneInfo("Asia/Bangkok")

scope = ["https://graph.microsoft.com/.default"]

fraud_authority = f"https://login.microsoftonline.com/{FRAUD_SITE_TENANT_ID}"
fraud_app = ConfidentialClientApplication(FRAUD_SITE_CLIENT_ID, authority=fraud_authority, client_credential=FRAUD_SITE_CLIENT_SECRET)

control_authority = f"https://login.microsoftonline.com/{CONTROL_SITE_TENANT_ID}"
control_app = ConfidentialClientApplication(CONTROL_SITE_CLIENT_ID, authority=control_authority, client_credential=CONTROL_SITE_CLIENT_SECRET)

def get_access_token(site_name: str):
    global fraud_app
    global control_app
    access_token = ""
    try:
        if site_name == "fraud":
            token_response = fraud_app.acquire_token_for_client(scopes=scope) # access_token, expires_in, token_source
            if token_response["expires_in"] < 1800:
                fraud_app = ConfidentialClientApplication(FRAUD_SITE_CLIENT_ID, authority=fraud_authority, client_credential=FRAUD_SITE_CLIENT_SECRET)
                token_response = fraud_app.acquire_token_for_client(scopes=scope)
            access_token = token_response["access_token"]
        elif site_name == "control":
            token_response = control_app.acquire_token_for_client(scopes=scope) # access_token, expires_in, token_source
            if token_response["expires_in"] < 1800:
                control_app = ConfidentialClientApplication(CONTROL_SITE_CLIENT_ID, authority=control_authority, client_credential=CONTROL_SITE_CLIENT_SECRET)
                token_response = control_app.acquire_token_for_client(scopes=scope)
            access_token = token_response["access_token"]
        else:
            logger.error(f"invalid site_name while get access token: {site_name}")
        return access_token
    except Exception as e:
        logger.error(f"Error get access token: {e!s}")

def get_site_id(site_name: str, access_token: str):
    site_url = ""
    try:
        if site_name == "fraud":
            site_url = f"https://graph.microsoft.com/v1.0/sites/{FRAUD_SITE_SITE_DOMAIN}:{FRAUD_SITE_SITE_PATH}"
        elif site_name == "control":
            site_url = f"https://graph.microsoft.com/v1.0/sites/{CONTROL_SITE_SITE_DOMAIN}:{CONTROL_SITE_SITE_PATH}"
        else:
            logger.error(f"invalid site_name while get site id: {site_name}")
        site_resp = requests.get(site_url, headers={"Authorization": f"Bearer {access_token}"})
        site_id = site_resp.json()["id"]
        return site_id
    except Exception as e:
        logger.error(f"Error get site id: {e!s}")

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
        logger.error("Error list folder in folder", extra={'json_payload': str(e)})
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
                file_id = item.get("id", None)
                created_date_time = item.get("createdDateTime", None)
                path = item.get("parentReference", {}).get("path", None)
                if not item.get("folder"):
                    file_list.append({
                        "file_name": file_name,
                        "file_id": file_id,
                        "created_date_time": created_date_time,
                        'path': path
                    })
                else:
                    logger.warning(f"Found non-JSON file in folder '{input_folder_name}': file name {file_name}")
            folder_url = response_data.get("@odata.nextLink")
        return file_list
    except Exception as e:
        logger.error("Error list folder in folder", extra={'json_payload': str(e)})
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


def move_file_to_archive(
    input_file_name, sharepoint_folder_path
):
    try:
        # Define the archive paths
        access_token = get_access_token("fraud")
        site_id = get_site_id("fraud", access_token)
        drive_id = get_drive_id(access_token, site_id)
        parent_archive_path = ARCHIVE_FOLDER
        monthly_folder_name = datetime.now().strftime("%Y%m")
        monthly_archive_path = f"{parent_archive_path}/{monthly_folder_name}"

        # Step 1: Check for or create the monthly archive folder
        archive_folder_id = None
        try:
            archive_url = f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root:/{monthly_archive_path}"
            archive_resp = requests.get(
                archive_url, headers={"Authorization": f"Bearer {access_token}"}
            )
            archive_resp.raise_for_status()
            archive_folder_id = archive_resp.json().get("id")
            print("Monthly archive folder found.")
        except requests.exceptions.HTTPError as err:
            if err.response.status_code == 404:
                print("Monthly archive folder not found. Creating it now...")

                # Get the ID of the parent "archive" folder
                parent_url = f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root:/{parent_archive_path}"
                parent_resp = requests.get(
                    parent_url, headers={"Authorization": f"Bearer {access_token}"}
                )
                parent_resp.raise_for_status()
                parent_id = parent_resp.json().get("id")

                # Create the new monthly folder
                create_folder_url = f"https://graph.microsoft.com/v1.0/drives/{drive_id}/items/{parent_id}/children"
                payload = {"name": monthly_folder_name, "folder": {}}
                create_resp = requests.post(
                    create_folder_url,
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                create_resp.raise_for_status()
                archive_folder_id = create_resp.json().get("id")
                logger.info(f"Folder '{monthly_folder_name}' created successfully.")

            else:
                # Re-raise the exception if it's not a 404
                raise

        # Step 2: Get the file's item ID
        get_file_url = f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root:/{sharepoint_folder_path}/{input_file_name}"
        get_resp = requests.get(
            get_file_url, headers={"Authorization": f"Bearer {access_token}"}
        )
        get_resp.raise_for_status()
        file_item_id = get_resp.json().get("id")

        # Step 3: Move the file to the archive folder
        move_payload = {
            "parentReference": {"id": archive_folder_id},
            "name": input_file_name,
        }

        move_url = (
            f"https://graph.microsoft.com/v1.0/drives/{drive_id}/items/{file_item_id}"
        )
        move_resp = requests.patch(
            move_url,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            json=move_payload,
        )
        move_resp.raise_for_status()
        logger.info(f"Moved {input_file_name} to sharepoint archive Successfully!")
        return True

    except requests.exceptions.RequestException as e:
        logger.error(f"Moved {input_file_name} to sharepoint archive Failed! : {e}")
        return False

def upload_file_to_sharepoint(site_name: str, byte_buffer, url):

    # print(f"url: {url}")
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

def get_file_content_by_id_csv(access_token, drive_id: str, item_id: str) -> bytes | None:
    """
    Downloads the raw content of a file from SharePoint using its ID.

    Args:
        item_id: The ID of the file to download.

    Returns:
        The raw content of the file as bytes, or None if an error occurs.
    """
    try:
        item_url = f"https://graph.microsoft.com/v1.0/drives/{drive_id}/items/{item_id}"
        item_resp = requests.get(item_url, headers={"Authorization": f"Bearer {access_token}"})
        item_resp.raise_for_status()
        download_url = item_resp.json().get("@microsoft.graph.downloadUrl")

        if not download_url:
             logger.error(f"Download URL not found for item ID: {item_id}")
             return None

        # Download the content using the direct download URL
        content_resp = requests.get(download_url)
        content_resp.raise_for_status()
        file_content = content_resp.content
        logger.info(f"Successfully downloaded file content for item ID: {item_id}")
        return file_content

    except requests.exceptions.RequestException as e:
        logger.error(f"Error request downloading file content for item ID: {item_id}", extra={'json_payload': str(e)})
        return None
    except Exception as e:
        logger.error(f"Error exception downloading file content for item ID: {item_id}", extra={'json_payload': str(e)})
        return None

def copy_sharepoint_file_to_gcs(
    FILE_PATH, # f"{INPUT_FOLDER}/{datetime.now().strftime('%Y%m')}"
    FILE_NAME,
    GCS_FILE_PATH, # f"{INPUT_FOLDER}/{INPUT_FILE_NAME}"
    fs
):
    access_token = get_access_token("fraud")
    site_id = get_site_id("fraud", access_token)
    item_url = f"https://graph.microsoft.com/v1.0/sites/{site_id}/drive/root:/{FRAUD_SITE_BASE_ROOT}/{FILE_PATH}/{FILE_NAME}:/content"

    item_resp = requests.get(
        item_url, headers={"Authorization": f"Bearer {access_token}"}
    )
    item_resp.raise_for_status()
    item_bytes = item_resp.content
    logger.info(f"Download {FILE_NAME} from SharePoint")

    # Upsert input_bk_file_name file content for backup
    INPUT_BACKUP_FILE_NAME = f"{FILE_NAME.split('.')[0]}_bk_{datetime.now().strftime('%m%d')}.{FILE_NAME.split('.')[-1]}"
    BACKUP_FILE_PATH = f"{BACKUP_FOLDER}/{datetime.now().strftime('%Y%m')}"
    upload_url = f"https://graph.microsoft.com/v1.0/sites/{site_id}/drive/root:/{FRAUD_SITE_BASE_ROOT}/{BACKUP_FILE_PATH}/{INPUT_BACKUP_FILE_NAME}:/content"
    upload_resp = requests.put(
        upload_url,
        data=item_bytes,
        headers={"Authorization": f"Bearer {access_token}"},
    )
    upload_resp.raise_for_status()
    logger.info(f"Upsert Backup file {INPUT_BACKUP_FILE_NAME} to SharePoint")

    # Save INPUT_FILE_NAME to GCS
    GCS_URI = f"gs://{GCS_BUCKET_NAME}/{GCS_FILE_PATH}"

    # Check if file exists
    if not fs.exists(GCS_URI):
        with fs.open(GCS_URI, "wb") as f:
            f.write(item_bytes)
    else:
        pass

    return GCS_FILE_PATH, GCS_URI
