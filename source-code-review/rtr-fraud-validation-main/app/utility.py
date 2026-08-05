from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import math
import mimetypes
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from json.decoder import JSONDecodeError

import boto3
import cv2
import google
import numpy as np
from azure.identity.aio import ClientSecretCredential  # Async version for Cloud Run
from botocore.exceptions import ClientError
from dotenv import load_dotenv
from google import genai
from google.api_core.exceptions import (
    Aborted,
    BadGateway,
    InternalServerError,
    ResourceExhausted,
    ServiceUnavailable,
    TooManyRequests,
)  # Import relevant exceptions
from google.cloud import secretmanager
from google.genai import types
from msgraph import GraphServiceClient
from msgraph.generated.models.body_type import BodyType
from msgraph.generated.models.email_address import EmailAddress
from msgraph.generated.models.file_attachment import FileAttachment
from msgraph.generated.models.item_body import ItemBody
from msgraph.generated.models.message import Message
from msgraph.generated.models.recipient import Recipient
from msgraph.generated.users.item.send_mail.send_mail_post_request_body import (
    SendMailPostRequestBody,
)
from PIL import Image
from PIL.ExifTags import TAGS
from skimage.metrics import structural_similarity as ssim
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_fixed,
)

from .share_log import logger


def get_secret_value(secret_id: str):
    """
    Get information about the given secret. This only returns metadata about
    the secret container, not any secret material.
    """
    try:
        secret_value = os.environ.get(secret_id)
    except:
        credentials, project_id = google.auth.default()
        client = secretmanager.SecretManagerServiceClient()
        name = f"projects/{project_id}/secrets/{secret_id}/versions/latest"
        response = client.access_secret_version(request={"name": name})
        secret_value = response.payload.data.decode("UTF-8")
    return secret_value

# local load env
load_dotenv()

BATCH_SIZE = int(get_secret_value("BATCH_SIZE"))
executor = ThreadPoolExecutor(max_workers=BATCH_SIZE)


# Define retryable exceptions
RETRYABLE_EXCEPTIONS = (
    ResourceExhausted,  # Often indicates quota issues, might clear up
    InternalServerError,  # Transient server errors
    Aborted,  # Operation aborted, often retryable
    TooManyRequests,  # Rate limiting, `tenacity`'s wait strategy helps
    BadGateway,  # Transient gateway issues
    ServiceUnavailable,
    asyncio.TimeoutError,
    RuntimeError,
    # ValueError,  # gemini - test with input
    KeyError,
    TypeError,  # ResourceExhausted
)

def get_session_to_s3() -> boto3.client | boto3.resource | None:

    aws_access_key_id = get_secret_value("S3_AWS_ACCESS_KEY")
    aws_secret_access_key = get_secret_value("S3_AWS_SECRET_KEY")

    s3_client = boto3.client(
        "s3",
        aws_access_key_id=aws_access_key_id,
        aws_secret_access_key=aws_secret_access_key,
    )

    s3_resource = boto3.resource(
        "s3",
        aws_access_key_id=aws_access_key_id,
        aws_secret_access_key=aws_secret_access_key,
    )

    return s3_client, s3_resource

def get_file_from_s3(s3_client, s3_bucket_name, key):
    response = s3_client.get_object(Bucket=s3_bucket_name, Key=key)
    content_byte = response["Body"].read()
    return content_byte



def save_gemini_response(response, output_dir="outputs"):
    # Convert response to dict safely
    if hasattr(response, "to_dict"):
        response_dict = response.to_dict()
    else:
        response_dict = json.loads(
            json.dumps(response, default=str, indent=2)
        )

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = f"{output_dir}/gemini_response_{timestamp}.json"

    # Ensure directory exists
    import os
    os.makedirs(output_dir, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(response_dict, f, ensure_ascii=False, indent=2)

    return output_path

@retry(
    wait=wait_fixed(20),
    stop=stop_after_attempt(3),
    retry=retry_if_exception_type(RETRYABLE_EXCEPTIONS),
)
async def fraud_validation_task(gemini_client, image_part: list, one_prompt: str):
    """
    fraud validation task

    Args:
        image_part (List[str]): list of image string-byte.
        one_prompt (str) : string prompt.

    Returns:
        dict parsed from Gemini output
    """
    GEMINI_MODEL_VERSION = "gemini-2.5-flash"

    parts = [types.Part.from_bytes(data=base64.b64decode(img), mime_type="image/jpeg") for img in image_part]
    parts.append(types.Part.from_text(text="Process These Images"))

    contents = [
        types.Content(
            role="user",
            parts=parts
        )
    ]

    generate_content_config = types.GenerateContentConfig(
            temperature=0,
            # seed=0,
            # max_output_tokens=65535,
            # temperature = 1,
            # top_p = 0.95,
            seed = 0,
            max_output_tokens = 65535,

            safety_settings=[
                types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH", threshold="OFF"),
                types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="OFF"),
                types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="OFF"),
                types.SafetySetting(category="HARM_CATEGORY_HARASSMENT", threshold="OFF"),
            ],
            system_instruction=[types.Part.from_text(text=one_prompt)],
            thinking_config=types.ThinkingConfig(thinking_budget=0),

        )

    try:
        # run sync Gemini call in a background thread
        response = gemini_client.models.generate_content(
            model=GEMINI_MODEL_VERSION,
            contents=contents,
            config=generate_content_config
        )
        # file_path = save_gemini_response(response)
        # print(f"Saved Gemini response to: {file_path}")

        response_text = None

        if response.candidates and response.candidates[0].content.parts:
            for part in response.candidates[0].content.parts:
                if part.text:
                    response_text = part.text
                    break

        text_input_tokens = 0
        image_input_tokens = 0
        for detail in response.usage_metadata.prompt_tokens_details:
            if detail.modality == 'TEXT':
                text_input_tokens = detail.token_count
            elif detail.modality == 'IMAGE':
                image_input_tokens = detail.token_count

        text_cache_tokens = 0
        image_cache_tokens = 0
        for detail in response.usage_metadata.cache_tokens_details:
            if detail.modality == 'TEXT':
                text_cache_tokens = detail.token_count
            elif detail.modality == 'IMAGE':
                image_cache_tokens = detail.token_count


        output_tokens = response.usage_metadata.candidates_token_count
        meta_data = {
            "text_cache_tokens": text_cache_tokens,
            "image_cache_tokens": image_cache_tokens,
            "text_input_tokens": text_input_tokens,
            "image_input_tokens" : image_input_tokens,
            "output_tokens": output_tokens
            }

        if not response_text:
            logger.warning(response, severity=logging.WARNING)
            logger.warning({"task cannot get text response"}, severity=logging.WARNING)
            raise ValueError("Gemini API returned an empty response for fraud_validation_task.")

        clean_json = response_text.replace("`", "").replace("json", "").strip()
        return json.loads(clean_json), meta_data


    except JSONDecodeError as e:
        raise ValueError(
            f"JSON decoding error in fraud_validation_task: {e}. Raw response: '{response_text}'"
        ) from e
    except Exception as e:
        logger.warning(contents={f"Unexpected error in fraud_validation_task: {e}"}, severity=logging.WARNING)
        raise ValueError(f"Unexpected error in fraud_validation_task: {e}") from e


async def compare_ssim(image_path1, image_path2, common_size=(256, 256)):
    """
    Compares two images for structural similarity, resizing them to a common size first.

    Args:
        image_path1 (str): Path to the first image.
        image_path2 (str): Path to the second image.
        common_size (tuple): A tuple (width, height) to resize both images to.

    Returns:
        tuple: A tuple containing a boolean indicating similarity (True/False)
               and a string with the SSIM score.
    """
    try:
        # Use cv2.imdecode to handle file paths with non-ASCII characters
        img_data1 = base64.b64decode(image_path1)
        img_data2 = base64.b64decode(image_path2)

        img1 = cv2.imdecode(np.frombuffer(img_data1, np.uint8), cv2.IMREAD_COLOR)
        img2 = cv2.imdecode(np.frombuffer(img_data2, np.uint8), cv2.IMREAD_COLOR)

    except Exception as e:
        return None, f"Error opening files: {e}"

    if img1 is None or img2 is None:
        return None, "Error: Could not decode one or both images."

    # Resize images to the common size
    resized_img1 = cv2.resize(img1, common_size)
    resized_img2 = cv2.resize(img2, common_size)

    # Convert images to grayscale for SSIM comparison
    gray1 = cv2.cvtColor(resized_img1, cv2.COLOR_BGR2GRAY)
    gray2 = cv2.cvtColor(resized_img2, cv2.COLOR_BGR2GRAY)

    # Compute the SSIM between the two grayscale images
    score, _ = ssim(gray1, gray2, full=True)

    # A score close to 1 indicates high similarity
    is_similar = score > 0.8 # True, False

    return 1 if is_similar else 0


def extract_metadata_from_s3_image(image_bytes):
    """Extract metadata from S3 image and force-parse EXIF UserComment JSON."""
    try:
        metadata = {}

        with Image.open(io.BytesIO(image_bytes)) as img:

            # Basic image info
            metadata["width"] = img.width
            metadata["height"] = img.height
            metadata["format"] = img.format
            metadata["mode"] = img.mode

            exif_data = img.getexif()

            if exif_data:
                # Add non-bytes EXIF fields
                for tag_id, value in exif_data.items():
                    tag_name = TAGS.get(tag_id, f"Tag_{tag_id}")
                    if not isinstance(value, bytes):
                        metadata[tag_name] = value

                # Extract EXIF IFD for UserComment (Tag 37510)
                try:
                    exif_ifd = exif_data.get_ifd(0x8769)

                    if exif_ifd and 37510 in exif_ifd:
                        user_comment = exif_ifd[37510]

                        if isinstance(user_comment, bytes):

                            # Remove EXIF UNICODE marker if present
                            comment_bytes = user_comment
                            if b"UNICODE" in comment_bytes:
                                pos = comment_bytes.find(b"UNICODE")
                                comment_bytes = comment_bytes[pos + 8:]

                            # ---- TRY MULTIPLE ENCODINGS ----
                            decoded = None
                            successful_encoding = None

                            # Try UTF-8 first, then fall back to UTF-16 variants
                            for encoding in ['utf-8', 'utf-16-le', 'utf-16-be', 'utf-16']:
                                try:
                                    test_decode = comment_bytes.decode(encoding, errors='strict')
                                    # Check if we got valid text (not empty or full of garbage)
                                    if test_decode and len(test_decode.strip()) > 0:
                                        # Additional check: see if it contains expected JSON characters
                                        if '{' in test_decode or '"' in test_decode:
                                            decoded = test_decode
                                            successful_encoding = encoding
                                            break
                                except Exception:
                                    continue

                            # If strict decoding failed, try UTF-8 with error handling as last resort
                            if not decoded:
                                decoded = comment_bytes.decode("utf-8", errors="ignore")
                                successful_encoding = "utf-8 (with errors ignored)"

                            # ---- EXTRACT AND PARSE JSON ----
                            json_match = re.search(r'\{.*?\}', decoded, flags=re.DOTALL)

                            if json_match:
                                json_str = json_match.group()

                                # Clean up any remaining null bytes or whitespace
                                json_str = json_str.replace('\x00', '').strip()

                                try:
                                    json_data = json.loads(json_str)
                                    metadata["UserComment_JSON"] = json_data
                                    metadata["Encoding_Used"] = successful_encoding

                                    # Add flattened values
                                    for k, v in json_data.items():
                                        metadata[f"UC_{k}"] = v


                                except json.JSONDecodeError as e:
                                    logger.error(f"JSON parse error: {e.msg} at position {e.pos}")
                                    logger.error(f"Encoding used: {successful_encoding}")
                                    logger.error(f"JSON string (first 200 chars): {json_str[:200]}")
                                    metadata["UserComment_Raw"] = json_str[:300]
                            else:
                                logger.warning("No JSON pattern found in UserComment")
                                metadata["UserComment_Raw"] = decoded[:300]

                except Exception as e:
                    logger.error(f"EXIF extraction error: {e}")

        # ---------- DISTANCE CALCULATION ----------

        photo_lat = float(metadata["UserComment_JSON"]["PHOTO_LATITUDE"])
        photo_lon = float(metadata["UserComment_JSON"]["PHOTO_LONGITUDE"])
        rtr_lat   = float(metadata["UserComment_JSON"]["RTR_LATITUDE"])
        rtr_lon   = float(metadata["UserComment_JSON"]["RTR_LONGITUDE"])

        R = 6371000  # meters
        phi1 = math.radians(photo_lat)
        phi2 = math.radians(rtr_lat)
        dphi = math.radians(rtr_lat - photo_lat)
        dlambda = math.radians(rtr_lon - photo_lon)

        a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        distance = R * c

        return [
            photo_lat,
            photo_lon,
            rtr_lat,
            rtr_lon,
            (
                "No Both Lat/Long"  if (photo_lat == 0 and photo_lon == 0) and (rtr_lat == 0 and rtr_lon == 0)
                else "No Photo Lat/Long" if (photo_lat == 0) and (photo_lon == 0)
                else "No Checkin Lat/Long" if (rtr_lat == 0) and (rtr_lon == 0)
                else "Match" if distance <= 300
                else "Not Match"
            )
        ]

    except Exception as e:
        logger.warning(f"Distance calculation failed: {e}")
        return ["", "", "", "", "No Both Lat/Long"]


# --- Function to generate text from an image ---
@retry(
    wait=wait_fixed(20),
    stop=stop_after_attempt(2),
    retry=retry_if_exception_type(RETRYABLE_EXCEPTIONS),
    before_sleep=before_sleep_log(logger, logging.WARNING),
)
async def process_shop(
    images_info: dict,
    one_prompt: str,
    shop_data: dict,
    s3_client: boto3.client,
    bucket_name: str,
    gemini_client: genai.Client,
) -> list | None:
    """
    Sends an image and a text prompt to the Gemini API and returns the response.

    Args:
        images_info (dict): Dictionary containing image path and other metadata.
            Expected keys: {'shop_path': ['image_path' , 'image_path', 'image_path']}
        prompt (str): The text prompt to send with the image.
        shop_data (dict): value from rtr info file. [RTR_Code,	RTR_Name, RTR_Owner, Sales_Name, PBH_Name, GA, Check_In, AOU, Location]
        s3_client (boto3.clinent): client used for access data from S3
        bucket_name (str): bucket_name of S3

    Returns:
        str: The text response from the Gemini API.
    """
    ###########################  load shop images in local run ###########################
    # await download_images(
    #     f"{shop_data['RTR_Code']}-{shop_data['RTR_Name']}", images_info, s3_client=s3_client, bucket_name=bucket_name
    # )
    # return None
    ######################################################################################

    start_time = datetime.now()

    ### 0.RETURN edge case: shop have no images
    if len(next(iter(images_info.values()))) == 0:
        list_of_value = [
            datetime.now().strftime("%Y-%m-%d %H-%M-%S"),  # Date Run,
            next(iter(images_info.keys())),  # folder name
            shop_data["RTR_Code"],  # rtr code
            shop_data["RTR_Name"],  # rtr name
            len(next(iter(images_info.values()))),  # Number of image
            "", # Photo_Name1
            "", # Photo_Name2
            "", # Photo_Name3

            "", # Photo1_Lat
            "", # Photo1_Long
            "", # RTR1_Lat
            "", # RTR1_Long
            "", # Flag300

            "", # Same_Photo
            "", # From_Other_Device
            "", # Closed_Business

            "", # un_relate
            "", # un_relate_human
            "", # un_relate_animal
            "", # un_relate_location
            "", # un_relate_object

            "inComplaint-No Photo",
            "fail",
        ]

        contents = {
            "status": "fail",
            "message": f"skipping : {shop_data['RTR_Code']}-{shop_data['RTR_Name']}, Shop does not attach image link from S3",
            "rtr_code": shop_data["RTR_Code"],
            "rtr_name": shop_data["RTR_Name"],
            "formated_result": {"one_prompt_output": {}},
        }

        return list_of_value, contents
    elif len(next(iter(images_info.values()))) >= 1 and len(next(iter(images_info.values()))) < 3:
        list_of_value = [
            datetime.now().strftime("%Y-%m-%d %H-%M-%S"),  # Date Run,
            next(iter(images_info.keys())),  # folder name
            shop_data["RTR_Code"],  # rtr code
            shop_data["RTR_Name"],  # rtr name
            len(next(iter(images_info.values()))),  # Number of image
            "", # Photo_Name1
            "", # Photo_Name2
            "", # Photo_Name3

            "", # Photo1_Lat
            "", # Photo1_Long
            "", # RTR1_Lat
            "", # RTR1_Long
            "", # Flag300

            "", # Same_Photo
            "", # From_Other_Device
            "", # Closed_Business

            "", # un_relate
            "", # un_relate_human
            "", # un_relate_animal
            "", # un_relate_location
            "", # un_relate_object

            "incompliant-Less than 3 Photos",
            "fail",
        ]

        contents = {
            "status": "fail",
            "message": f"skipping : {shop_data['RTR_Code']}-{shop_data['RTR_Name']}, Shop Photo less than 3 Photos",
            "rtr_code": shop_data["RTR_Code"],
            "rtr_name": shop_data["RTR_Name"],
            "formated_result": {"one_prompt_output": {}},
        }

        return list_of_value, contents

    ### 1.Access image & it's metadata from s3
    image_parts = []
    images_metadata = []
    for image_path in next(iter(images_info.values())):
        key = image_path.replace(f"s3://{bucket_name}/", "")
        try:
            response = s3_client.get_object(Bucket=bucket_name, Key=key)
            image_bytes = response["Body"].read()

            # Encode for API
            image_part = base64.b64encode(image_bytes).decode('utf-8')
            image_parts.append(image_part)

            # Extract metadata
            image_metadata = extract_metadata_from_s3_image(image_bytes)
            images_metadata.append(image_metadata)

        except Exception as e:
            logger.warning(f"Error occur during accessing image metadata or there no image with error : {e} of {key}")
            pass

    ### 1.1.RETURN Handler Case that Have URL Image but cannot get iamges data
    if len(image_parts) == 0:
        list_of_value = [
            datetime.now().strftime("%Y-%m-%d %H-%M-%S"),  # Date Run,
            next(iter(images_info.keys())),  # folder name
            shop_data["RTR_Code"],  # rtr code
            shop_data["RTR_Name"],  # rtr name
            len(next(iter(images_info.values()))),  # Number of image
            "", # Photo_Name1
            "", # Photo_Name2
            "", # Photo_Name3

            "", # Photo1_Lat
            "", # Photo1_Long
            "", # RTR1_Lat
            "", # RTR1_Long
            "", # Flag300

            "", # Same_Photo
            "", # From_Other_Device
            "", # Closed_Business

            "", # un_relate
            "", # un_relate_human
            "", # un_relate_animal
            "", # un_relate_location
            "", # un_relate_object

            "inComplaint-No Photo",
            "fail",
        ]

        contents = {
            "status": "fail",
            "message": f"skipping : {shop_data['RTR_Code']}-{shop_data['RTR_Name']}, Shop have no file in S3",
            "rtr_code": shop_data["RTR_Code"],
            "rtr_name": shop_data["RTR_Name"],
            "formated_result": {"one_prompt_output": {}},
        }

        return list_of_value, contents

    ### 2.Extract data
    ### 2.1.Same photo detection label
    same_photo = ""
    if len(image_parts) == 3:
        same_photo_compare_1 = 2*(await compare_ssim(image_parts[0], image_parts[1]))
        same_photo_compare_2 = 2*(await compare_ssim(image_parts[1], image_parts[2]))
        same_photo_compare_3 = 2*(await compare_ssim(image_parts[0], image_parts[2]))
        if same_photo_compare_1 + same_photo_compare_2 + same_photo_compare_3 >= 4:
            same_photo = f"3/{len(image_parts)}"
        else:
            same_photo = f"{same_photo_compare_1 + same_photo_compare_2 + same_photo_compare_3}/{len(image_parts)}"
    elif len(image_parts) == 2:
        same_photo_compare_1 = 2*(await compare_ssim(image_parts[0], image_parts[1]))
        same_photo = f"{same_photo_compare_1}/{len(image_parts)}"
    elif len(image_parts) == 1:
        same_photo = "0/1"

    ### 3.ADD data [A row] of processing
    ## ADD : 1.Run_Date, 2.Folder_Name, 3.RTR_Code, 4.RTR_Name, 5.Number_of_image
    list_of_value = [
        datetime.now().strftime("%Y-%m-%d %H-%M-%S"),  # Run_Date,
        next(iter(images_info.keys())),  # Folder_Name
        shop_data["RTR_Code"],  # RTR_Code
        shop_data["RTR_Name"],  # RTR_Name
        len(image_parts),  # Number_of_image
    ]

    ## ADD : 6.Photo_Name1, 7.Photo_Name2, 8.Photo_Name3
    list_images_info = list(next(iter(images_info.values())))
    for image_no in range(3): # Fix structure output with 3
        try:
            list_of_value.append(list_images_info[image_no])
        except:
            list_of_value.append("")

    ## ADD : 9.Photo1_Lat, 10.Photo1_Long, 11.RTR1_Lat, 12.RTR1_Long, 13.Photo1_Flag300
    for i in range(1): # Fix structure output with 21
        for j in range(5):
            try:
                list_of_value.append(images_metadata[i][j])
            except:
                list_of_value.append("")

    ## ADD : 14.Same_Photo
    list_of_value.append(same_photo)

    ## ADD : 15.From_Other_Device, 16.Closed_Business, 17.un_relate, 18.un_relate_human, 19.un_relate_animal, 20.un_relate_location, 21.un_relate_object, 22.Complaint, 23.Status
    try:
        # Call gemini for 'From_Other_Device', 'Closed_Business'
        one_prompt_output, meta_data = await fraud_validation_task(gemini_client, image_parts, one_prompt)

        if one_prompt_output is None:
            raise ValueError("fraud_validation_task returned None")

        list_of_value.append(one_prompt_output["from_other_device"]) # From_Other_Device
        list_of_value.append(one_prompt_output["shop_operate"]) # Closed_Business

        list_of_value.append(one_prompt_output["un_relate"]) # un_relate
        list_of_value.append(one_prompt_output["un_relate_category"]["un_relate_human"]) # un_relate_human
        list_of_value.append(one_prompt_output["un_relate_category"]["un_relate_animal"]) # un_relate_animal
        list_of_value.append(one_prompt_output["un_relate_category"]["un_relate_location"]) # un_relate_location
        list_of_value.append(one_prompt_output["un_relate_category"]["un_relate_object"]) # un_relate_object

        list_of_value.append(
            "inComplaint"
            if (
                # AI - post processing for Complaint
                "1/" in same_photo
                or "2/" in same_photo
                or "3/" in same_photo

                or "1/" in one_prompt_output["from_other_device"]
                or "2/" in one_prompt_output["from_other_device"]
                or "3/" in one_prompt_output["from_other_device"]

                or "1/" in one_prompt_output["shop_operate"]
                or "2/" in one_prompt_output["shop_operate"]
                or "3/" in one_prompt_output["shop_operate"]

            )
            else "Complaint"
        )

        list_of_value.append("success")


        # 4.Conduct Final data before sending back to main
        contents = {
            "status": "success",
            "start_time" : str(start_time),
            "end_time" : str(datetime.now()),
            "process_time": (datetime.now() - start_time).total_seconds(),
            "message": f"success process : {shop_data['RTR_Code']}-{shop_data['RTR_Name']}",
            "image_parts" : next(iter(images_info.values())),
            "rtr_code": shop_data["RTR_Code"],
            "rtr_name": shop_data["RTR_Name"],
            "formated_result": {
                "one_prompt_output": one_prompt_output,
            },
            "meta_data": meta_data,
        }

        return list_of_value, contents

    except RETRYABLE_EXCEPTIONS as e: # Retry with RETRYABLE_EXCEPTIONS
        contents = {
            "status": "fail",
            "start_time" : str(start_time),
            "end_time" : str(datetime.now()),
            "process_time": (datetime.now() - start_time).total_seconds(),
            "message": f"retrying : {shop_data['RTR_Code']}-{shop_data['RTR_Name']} from {next(iter(images_info.keys()))} with reason : {type(e).__name__} - {e}",
            "image_parts" : next(iter(images_info.values())),
            "rtr_code": shop_data["RTR_Code"],
            "rtr_name": shop_data["RTR_Name"],
            "formated_result": {"one_prompt_output": {}},
            "meta_data": {"input_tokens": 0, "output_tokens": 0}
        }

    except ClientError as e:  # files not exists in s3
        if e.response["Error"]["Code"] == "NoSuchKey":
            contents = {
                "status": "fail",
                "start_time" : str(start_time),
                "end_time" : str(datetime.now()),
                "process_time": (datetime.now() - start_time).total_seconds(),
                "message": f"skipping : {shop_data['RTR_Code']}-{shop_data['RTR_Name']}, Missing file in S3: {key}",
                "image_parts" : next(iter(images_info.values())),
                "rtr_code": shop_data["RTR_Code"],
                "rtr_name": shop_data["RTR_Name"],
                "formated_result": {"one_prompt_output": {}},
                "meta_data": {"input_tokens": 0, "output_tokens": 0}
            }

            ## ADD fail case where files not exists in s3
            list_of_value.extend(
                [
                    "", # From_Other_Device
                    "", # Closed_Business

                    "", # un_relate
                    "", # un_relate_human
                    "", # un_relate_animal
                    "", # un_relate_location
                    "", # un_relate_object

                    "inComplaint-No Photo",
                    "fail",
                ]
            )
        return list_of_value, contents

    except Exception as e:
        contents = {
            "status": "fail",
            "start_time" : str(start_time),
            "end_time" : str(datetime.now()),
            "process_time": (datetime.now() - start_time).total_seconds(),
            "message": f"Unhandled error processing shop : {shop_data['RTR_Code']}-{shop_data['RTR_Name']} with reason : {type(e).__name__} - {e}",
            "image_parts" : next(iter(images_info.values())),
            "rtr_code": shop_data["RTR_Code"],
            "rtr_name": shop_data["RTR_Name"],
            "formated_result": {"one_prompt_output": {}},
            "meta_data": {"input_tokens": 0, "output_tokens": 0}
        }

        ## ADD fail case with un-expected error
        list_of_value.extend(
            [
                "", # From_Other_Device
                "", # Closed_Business

                "", # un_relate
                "", # un_relate_human
                "", # un_relate_animal
                "", # un_relate_location
                "", # un_relate_object

                "inComplaint",
                "fail",
            ]
        )

        return list_of_value, contents


async def send_outlook_graph_api(
    bcc_emails: list, subject: str, body_content: str, is_html: bool = False, attachments: dict = None, inline_images: dict = None
) -> None:
    """
    Sends an email via Microsoft Graph API using Client Credentials Flow.
    Designed for use in server-side applications like Google Cloud Run.
    """

    try:
        # --- Configuration (Store securely, e.g., in environment variables or Secret Manager) ---

        FRAUD_SITE_TENANT_ID = get_secret_value("FRAUD_SITE_TENANT_ID")
        FRAUD_SITE_CLIENT_ID = get_secret_value("FRAUD_SITE_CLIENT_ID")
        FRAUD_SITE_CLIENT_SECRET = get_secret_value("FRAUD_SITE_CLIENT_SECRET")
        SENDER_EMAIL = get_secret_value("SENDER_EMAIL")

        # --------------------------------------------------------------------------------------

        credential = ClientSecretCredential(
            tenant_id=FRAUD_SITE_TENANT_ID, client_id=FRAUD_SITE_CLIENT_ID, client_secret=FRAUD_SITE_CLIENT_SECRET
        )

        # Initialize the GraphServiceClient
        graph_client = GraphServiceClient(credentials=credential)

        # Create the message object
        message = Message()
        message.subject = subject
        message.body = ItemBody()
        message.body.content_type = BodyType.Html if is_html else BodyType.Text
        message.body.content = body_content
        # message.to_recipients = [Recipient(email_address=EmailAddress(address=email)) for email in to_email]
        # add BCC if needed
        message.bcc_recipients = [
            Recipient(email_address=EmailAddress(address=email)) for email in bcc_emails
        ]


        if attachments:
            message.attachments = []
            for filename, file_b64 in attachments.items():

                file_attachment = FileAttachment()
                file_attachment.odata_type = "#microsoft.graph.fileAttachment"
                file_attachment.name = filename
                mime_type, _ = mimetypes.guess_type(filename)
                file_attachment.content_type = mime_type or "application/octet-stream"

                # file_b64 should be the base64 string of the file's binary content
                file_attachment.content_bytes = base64.b64decode(file_b64) # ! Important : Use raw byte
                message.attachments.append(file_attachment)

        if inline_images:
            if message.attachments is None:
                message.attachments = []
            for cid, file_b64 in inline_images.items():
                file_attachment = FileAttachment()
                file_attachment.odata_type = "#microsoft.graph.fileAttachment"
                file_attachment.name = cid
                mime_type, _ = mimetypes.guess_type(cid)
                file_attachment.content_type = mime_type or "image/png"
                file_attachment.content_bytes = base64.b64decode(file_b64)
                file_attachment.content_id = cid        # <-- CID reference
                file_attachment.is_inline = True        # <-- Mark as inline
                message.attachments.append(file_attachment)

        # Construct the request body for sendMail
        request_body = SendMailPostRequestBody(
            message=message, save_to_sent_items=True  # Save a copy in Sent Items
        )

        await graph_client.users.by_user_id(SENDER_EMAIL).send_mail.post(request_body)

        contents = {
            "status": "success",
            "message": f"Email sent successfully to {bcc_emails} from {SENDER_EMAIL} via Microsoft Graph API.",
        }
        logger.info(contents)

    except Exception as e:
        contents = {
            "status": "General Error",
            "message": f"An unexpected error occurred: {e}",
        }
        logger.error(contents)
        raise

# Rare use function

async def download_images(RTR_Name: str, images_info: dict, s3_client, bucket_name):
    local_folder = "./app/images"
    # next(iter(images_info.keys())),  # folder name
    # {'shop_path': ['image_path' , 'image_path', 'image_path']}
    try:
        for image_path in next(iter(images_info.values())):
            key = image_path.replace(f"s3://{bucket_name}/", "")

            try:
                response = s3_client.get_object(Bucket=bucket_name, Key=key)
                image_bytes = response["Body"].read()

                # Build output path
                output_path = os.path.join(local_folder, RTR_Name, key.split("/")[3])

                # Ensure *full* directory exists
                os.makedirs(os.path.dirname(output_path), exist_ok=True)

                # Save image
                with open(output_path, "wb") as out_file:
                    out_file.write(image_bytes)

                logger.info(f"Image saved successfully to {output_path}")

            except Exception as e:
                logger.error(f"Unhandled error processing image: {type(e).__name__} - {e}: with file name {key}")
                raise

    except Exception:
        pass
