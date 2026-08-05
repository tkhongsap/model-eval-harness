import os, requests, time
from datetime import datetime
from zoneinfo import ZoneInfo
from msal import ConfidentialClientApplication
from src.log import Logger

logger = Logger(__name__, env=os.environ.get("LOG_ENVIRONMENT", "prod"))

class SharePointModule:
    def __init__(self, **kwargs):
        self.scope = ["https://graph.microsoft.com/.default"]
        self.timezone = ZoneInfo("Asia/Bangkok")
        self.client_id = kwargs.get("client_id")
        self.client_secret = kwargs.get("client_secret")
        self.tenant_id = kwargs.get("tenant_id")
        self.access_token = self.__get_access_token(
            client_id=self.client_id,
            client_secret=self.client_secret,
            tenant_id=self.tenant_id,
        )
        self.site_domain = kwargs.get("site_domain")
        self.site_path = kwargs.get("site_path")
        logger.info(f"SharePointModule initialized for site: {self.site_domain}{self.site_path}")

    def __get_access_token(self, client_id: str, client_secret: str, tenant_id: str) -> str:
        """
        Obtain an access token for SharePoint using MSAL.
        Parameters:
            client_id (str): The client ID of SharePoint
            client_secret (str): The client secret of SharePoint
            tenant_id (str): The tenant ID of SharePoint
        Returns:
            str: Access token for SharePoint
        """
        try:
            sharepoint_app = ConfidentialClientApplication(
                client_id,
                authority=f"https://login.microsoftonline.com/{tenant_id}",
                client_credential=client_secret,
            )
            access_result = sharepoint_app.acquire_token_for_client(scopes=self.scope)
            
            if "access_token" in access_result:
                return access_result["access_token"]
            else:
                error_description = access_result.get("error_description", access_result.get("error"))
                logger.error(f"Failed to acquire token: {error_description}")
                raise Exception(f"Failed to acquire token: {error_description}")
        except Exception as e:
            logger.error(f"Error obtaining access token: {e}")
            raise Exception(f"Error obtaining access token: {e}")
    
    def __access_token_retry(self, response: requests.Response) -> bool:
        """
        Retry obtaining access token if the response indicates an expired token.
        Parameters:
            response (requests.Response): The HTTP response to check
        Returns:
            bool: True if the token was refreshed, False otherwise
        """
        if response.status_code == 401:
            logger.info("Access token expired. Refreshing token...")
            self.access_token = self.__get_access_token(self.client_id, self.client_secret, self.tenant_id)
            return True
        return False
    def get_site_id(self) -> str:
        """
        Retrieve the SharePoint site ID.
        Parameters:
            None
        Returns:
            str: The site ID
        """
        try:
            headers = {
                "Authorization": f"Bearer {self.access_token}"
            }
            site_url = f"https://graph.microsoft.com/v1.0/sites/{self.site_domain}:{self.site_path}"
            response = requests.get(site_url, headers=headers)
            if self.__access_token_retry(response):
                headers["Authorization"] = f"Bearer {self.access_token}"
                response = requests.get(site_url, headers=headers)
            response.raise_for_status()
            site_id = response.json().get("id")
            if not site_id:
                raise Exception("Site ID not found in response")
            return site_id
        except requests.exceptions.HTTPError as http_err:
            logger.error(f"HTTP error occurred: {http_err} - Response: {http_err.response.text}")
            raise Exception(f"Error retrieving site ID from SharePoint: {http_err}")
        except Exception as e:
            logger.error(f"Error retrieving site ID from SharePoint: {e}")
            raise Exception(f"Error retrieving site ID from SharePoint: {e}")
    
    def list_files(self, folder_path: str) -> list:
        """
        List files in a SharePoint folder.
        Parameters:
            folder_path (str): The path of the folder to list files from
        Returns:
            list: The list of files
        """
        try:
            headers = {
                "Authorization": f"Bearer {self.access_token}"
            }
            site_id = self.get_site_id()
            if not folder_path.startswith('/'):
                folder_path = f"/{folder_path}"
            
            list_url = f"https://graph.microsoft.com/v1.0/sites/{site_id}/drive/root:{folder_path}:/children"
            logger.info(f"Listing files from folder: {list_url}")
            
            all_files = []
            
            while list_url:
                response = requests.get(list_url, headers=headers)
                if self.__access_token_retry(response):
                    headers["Authorization"] = f"Bearer {self.access_token}"
                    response = requests.get(list_url, headers=headers)
                response.raise_for_status()
                
                data = response.json()
                all_files.extend(data.get("value", []))
                
                list_url = data.get("@odata.nextLink")
                if list_url:
                    logger.info(f"Fetching next page of files...")
            
            return all_files

        except requests.exceptions.HTTPError as http_err:
            logger.error(f"HTTP error occurred: {http_err} - Response: {http_err.response.text}")
            raise Exception(f"Error listing files from SharePoint: {http_err}")
        except Exception as e:
            logger.error(f"Error listing files from SharePoint: {e}")
            raise Exception(f"Error listing files from SharePoint: {e}")

    def get_item_by_path(self, item_path: str) -> requests.Response:
        """
        Retrieve an item from SharePoint by specified path.
        Parameters:
            item_path (str): The path of the item to retrieve
        Returns:
            requests.Response: The response containing the item data
        """
        try:
            headers = {
                "Authorization": f"Bearer {self.access_token}"
            }
            site_id = self.get_site_id()
            if not item_path.startswith('/'):
                item_path = f"/{item_path}"
            
            item_url = f"https://graph.microsoft.com/v1.0/sites/{site_id}/drive/root:{item_path}"
            logger.info(f"Retrieving item metadata: {item_url}")
            response = requests.get(item_url, headers=headers)
            if self.__access_token_retry(response):
                headers["Authorization"] = f"Bearer {self.access_token}"
                response = requests.get(item_url, headers=headers)
            response.raise_for_status()
            
            download_url = response.json().get("@microsoft.graph.downloadUrl")
            if not download_url:
                raise Exception("Download URL not found in response")
            
            download_res = requests.get(download_url)
            if self.__access_token_retry(download_res):
                logger.warning("Download URL request returned 401. The URL might be expired.")
                download_res = requests.get(download_url)
            download_res.raise_for_status()
            return download_res

        except requests.exceptions.HTTPError as http_err:
            logger.error(f"HTTP error occurred: {http_err} - Response: {http_err.response.text}")
            raise Exception(f"Error retrieving item from SharePoint: {http_err}")
        except Exception as e:
            logger.error(f"Error retrieving item from SharePoint: {e}")
            raise Exception(f"Error retrieving item from SharePoint: {e}")
        
    def check_item_exists(self, item_path: str) -> bool:
        """
        Check if an item exists in SharePoint by specified path.
        Parameters:
            item_path (str): The path of the item to check
        Returns:
            bool: True if the item exists, False otherwise
        """
        try:
            headers = {
                "Authorization": f"Bearer {self.access_token}"
            }
            site_id = self.get_site_id()
            if not item_path.startswith('/'):
                item_path = f"/{item_path}"
            
            item_url = f"https://graph.microsoft.com/v1.0/sites/{site_id}/drive/root:{item_path}"
            logger.info(f"Checking if item exists: {item_url}")
            response = requests.get(item_url, headers=headers)
            if self.__access_token_retry(response):
                headers["Authorization"] = f"Bearer {self.access_token}"
                response = requests.get(item_url, headers=headers)
            if response.status_code == 200:
                return True
            elif response.status_code == 404:
                return False
            else:
                response.raise_for_status()
        except requests.exceptions.HTTPError as http_err:
            logger.error(f"HTTP error occurred: {http_err} - Response: {http_err.response.text}")
            raise Exception(f"Error checking item existence in SharePoint: {http_err}")
        except Exception as e:
            logger.error(f"Error checking item existence in SharePoint: {e}")
            raise Exception(f"Error checking item existence in SharePoint: {e}")
    
    def upload_file(self, upload_path: str, content: bytes) -> requests.Response:
        """
        Upload a file to SharePoint at the specified path.
        Parameters:
            upload_path (str): The path where the file will be uploaded
            content (bytes): The binary data of the file to upload
        Returns:
            requests.Response: The response from the upload operation
        """
        try:
            headers = {
                "Authorization": f"Bearer {self.access_token}",
            }
            site_id = self.get_site_id()
            if not upload_path.startswith('/'):
                upload_path = f"/{upload_path}"
            
            upload_url = f"https://graph.microsoft.com/v1.0/sites/{site_id}/drive/root:{upload_path}:/content"
            logger.info(f"Uploading file to: {upload_url}")
            
            max_retries = 3
            retry_delay = 10
            response = None

            for attempt in range(max_retries):
                response = requests.put(upload_url, headers=headers, data=content)
                if self.__access_token_retry(response):
                    headers["Authorization"] = f"Bearer {self.access_token}"
                    response = requests.put(upload_url, headers=headers, data=content)
                
                if response.status_code in [200, 201]:
                    return response
                
                if response.status_code in [423, 409]:
                    logger.warning(f"File upload failed with status {response.status_code}. Attempting to archive existing file and retry.")
                    
                    item_url = f"https://graph.microsoft.com/v1.0/sites/{site_id}/drive/root:{upload_path}"
                    item_response = requests.get(item_url, headers=headers)
                    if self.__access_token_retry(item_response):
                        headers["Authorization"] = f"Bearer {self.access_token}"
                        item_response = requests.get(item_url, headers=headers)
                    
                    if item_response.status_code == 200:
                        item_data = item_response.json()
                        item_id = item_data.get("id")
                        item_name = item_data.get("name")
                        
                        # Logic to move/rename the locked file
                        timestamp = datetime.now(tz=self.timezone).strftime("%Y%m%d%H%M%S")
                        archive_name = f"archive_{timestamp}_{item_name}"
                        
                        # We need to move it to the same parent folder.
                        # item_data['parentReference']['id'] gives the parent folder ID.
                        parent_folder_id = item_data.get("parentReference", {}).get("id")
                        drive_id = item_data.get("parentReference", {}).get("driveId")

                        if parent_folder_id and drive_id:
                            move_url = f"https://graph.microsoft.com/v1.0/drives/{drive_id}/items/{item_id}"
                            move_payload = {
                                "parentReference": {
                                    "id": parent_folder_id
                                },
                                "name": archive_name
                            }
                            move_headers = {
                                "Authorization": f"Bearer {self.access_token}",
                                "Content-Type": "application/json",
                                "Prefer": "bypass-shared-lock"
                            }
                            
                            rename_response = requests.patch(move_url, headers=move_headers, json=move_payload)
                            if self.__access_token_retry(rename_response):
                                move_headers["Authorization"] = f"Bearer {self.access_token}"
                                rename_response = requests.patch(move_url, headers=move_headers, json=move_payload)
                            
                            if rename_response.status_code == 200:
                                logger.info(f"Successfully archived existing file to {archive_name}")
                                # Retry upload immediately after archiving
                                response = requests.put(upload_url, headers=headers, data=content)
                                if self.__access_token_retry(response):
                                    headers["Authorization"] = f"Bearer {self.access_token}"
                                    response = requests.put(upload_url, headers=headers, data=content)
                                if response.status_code in [200, 201]:
                                    return response
                            else:
                                logger.error(f"Failed to archive existing file. Status: {rename_response.status_code}, Response: {rename_response.text}")
                        else:
                             logger.error("Failed to get parent folder ID or Drive ID for archiving.")
                    else:
                         logger.error(f"Failed to retrieve existing file metadata for archiving. Status: {item_response.status_code}")

                if response.status_code == 423:
                    if attempt < max_retries - 1:
                        logger.warning(f"Resource locked. Retrying in {retry_delay} seconds... (Attempt {attempt + 1}/{max_retries})")
                        time.sleep(retry_delay)
                        continue

                response.raise_for_status()
                return response

            if response:
                response.raise_for_status()
            return response
        except requests.exceptions.HTTPError as http_err:
            logger.error(f"HTTP error occurred: {http_err} - Response: {http_err.response.text}")
            raise Exception(f"Error uploading file to SharePoint: {http_err}")
        except Exception as e:
            logger.error(f"Error uploading file to SharePoint: {e}")
            raise Exception(f"Error uploading file to SharePoint: {e}")