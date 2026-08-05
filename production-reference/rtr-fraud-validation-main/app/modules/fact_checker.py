import argparse
import asyncio
import base64
import io
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import google
import numpy as np
import pandas as pd
from dotenv import load_dotenv
from google import genai
from google.genai import types
from google.genai.types import HttpOptions
from openpyxl import load_workbook

from app.modules.sharepoint import SharePointModule
from app.share_log import logger

# from src.output_schema import OutputValidation
from app.utils.common import (
    get_value_by_path,
    load_yaml_string,
    percentage_format,
    read_file,
    resolve_env,
)

BKK_TZ = ZoneInfo("Asia/Bangkok")
load_dotenv(override=True)

GROUND_TRUTH_COLUMNS = [
    "RTR_Code",
    "RTR_Name",
    "Number_of_image",
    "Photo_Name1",
    "Photo_Name2",
    "Photo_Name3",
    "Same_Photo",
    "From_Other_Device",
    "Closed_Business",
    "un_relate",
    "un_relate_human",
    "un_relate_animal",
    "un_relate_location",
    "un_relate_object",
]


TARGET_COLUMNS = [
    "Same_Photo",
    "From_Other_Device",
    "Closed_Business",
    "un_relate",
    "un_relate_human",
    "un_relate_animal",
    "un_relate_location",
    "un_relate_object",
]

_, project_id = google.auth.default()
class FactCheckerModule:
    def __init__(self, config_path: str):
        resolve_config = load_yaml_string(resolve_env(read_file(config_path)))
        self.config_validation(resolve_config)
        self.configs = resolve_config
        print(self.configs)

        sharepoint_mapping = {}
        for key, value in self.__get_sharepoint_env_variables().items():
            sharepoint_mapping[key] = SharePointModule(
                client_id=value["CLIENT_ID"],
                client_secret=value["CLIENT_SECRET"],
                tenant_id=value["TENANT_ID"],
                site_domain=value["SITE_DOMAIN"],
                site_path=value["SITE_PATH"],
            )

        self.module_objects = {**sharepoint_mapping}

    def config_validation(self, configs: dict[str, Any]) -> None:
        """
        Validate the configuration dictionary against the expected structure.
        Parameters:
            configs (Dict[str, Any]): The configuration dictionary.
        Returns:
            None
        Raises:
            ValueError: If the configuration is invalid.
        """
        required_keys = ["Project_id", "Project_name", "GroundTruth", "Prediction", "Report"]
        for key in required_keys:
            if key not in configs:
                raise ValueError(f"Missing required key in configuration: {key}")

        # Validate GroundTruth
        gt_input = get_value_by_path(configs, "GroundTruth.input")
        if not gt_input:
             raise ValueError("Missing 'GroundTruth.input' configuration.")
        self._validate_io_config(gt_input, "GroundTruth.input")

        # Validate Prediction
        pred_gemini = get_value_by_path(configs, "Prediction.gemini_stream")
        if not pred_gemini:
             raise ValueError("Missing 'Prediction.gemini_stream' configuration.")

        # Validate gemini_batch components
        self._validate_io_config(get_value_by_path(configs, "Prediction.gemini_stream.model_config.input"), "Prediction.gemini_stream.model_config.input")
        self._validate_io_config(get_value_by_path(configs, "Prediction.gemini_stream.input"), "Prediction.gemini_stream.input")
        self._validate_io_config(get_value_by_path(configs, "Prediction.gemini_stream.prompt.system_prompt_path.input"), "Prediction.gemini_stream.prompt.system_prompt_path.input")

        # Validate Report
        self._validate_io_config(get_value_by_path(configs, "Report.output"), "Report.output")

        if "schema" not in configs["Report"]:
             raise ValueError("Missing 'Report.schema' configuration.")
        if "metric_thresholds" not in configs["Report"]:
             raise ValueError("Missing 'Report.metric_thresholds' configuration.")

    def _validate_io_config(self, config_section: dict[str, Any], path: str) -> None:
        """
        Validate an Input/Output configuration section.
        Parameters:
            config_section (Dict[str, Any]): The configuration section to validate.
            path (str): The configuration path for error messages.
        Returns:
            None
        """
        if not config_section:
            raise ValueError(f"Missing configuration section: {path}")

        type_ = config_section.get("type")
        if not type_:
            raise ValueError(f"Missing 'type' in {path}")

        if type_ == "sharepoint":
            required = ["site_name", "path"]
            for field in required:
                if field not in config_section:
                     raise ValueError(f"Missing '{field}' in {path} (type: sharepoint)")
        elif type_ == "gcs":
             required = ["gcp_project_id", "bucket_name", "path"]
             for field in required:
                if field not in config_section:
                     raise ValueError(f"Missing '{field}' in {path} (type: gcs)")
        elif type_ == "source_code":
             required = ["path"]
             for field in required:
                if field not in config_section:
                     raise ValueError(f"Missing '{field}' in {path} (type: source_code)")
        else:
             raise ValueError(f"Unknown type '{type_}' in {path}")

    def __get_sharepoint_env_variables(self) -> dict[str, dict[str, str]]:
        env_mapping = {
            "SentimentAnalysisfromVoiceFile":{
                "CLIENT_ID": "CONTROL_SITE_CLIENT_ID",
                "CLIENT_SECRET": "CONTROL_SITE_CLIENT_SECRET",
                "TENANT_ID": "CONTROL_SITE_TENANT_ID",
                "SITE_DOMAIN": "CONTROL_SITE_SITE_DOMAIN",
                "SITE_PATH": "CONTROL_SITE_SITE_PATH",
            },
            "AIandAutomationTeamControlManagement":{
                "CLIENT_ID": "CONTROL_SITE_CLIENT_ID",
                "CLIENT_SECRET": "CONTROL_SITE_CLIENT_SECRET",
                "TENANT_ID": "CONTROL_SITE_TENANT_ID",
                "SITE_DOMAIN": "CONTROL_SITE_SITE_DOMAIN",
                "SITE_PATH": "CONTROL_SITE_SITE_PATH",
            }
        }
        env_variables = {}

        def recursive_extract_site_names(config_section: dict[Any,Any], parent_key: str = "") -> list[tuple[str, str]]:
            """
            Recursively extract site names and their config paths.
            Parameter:
                config_section (Dict[Any,Any]): The configuration section to search.
                parent_key (str): The accumulated key path.
            Returns:
                List[Tuple[str, str]]: A list of (site_name, config_path).
            """
            site_name_list = []
            if isinstance(config_section, dict):
                # Check if current dict has both site_name and type='sharepoint'
                if "site_name" in config_section and config_section.get("type") == "sharepoint":
                    site_name_list.append((config_section.get("site_name"), parent_key))

                # Recursively check all values
                for key, value in config_section.items():
                    if isinstance(value, (dict, list)):
                        current_key = f"{parent_key}.{key}" if parent_key else key
                        site_name_list.extend(recursive_extract_site_names(value, current_key))

            elif isinstance(config_section, list):
                for index, item in enumerate(config_section):
                    current_key = f"{parent_key}.{index}" if parent_key else str(index)
                    site_name_list.extend(recursive_extract_site_names(item, current_key))

            return site_name_list

        extracted_sites = recursive_extract_site_names(config_section=self.configs)

        for site_name, config_path in extracted_sites:
            if site_name in env_mapping:
                env_variables[config_path] = {
                    "CLIENT_ID": os.getenv(env_mapping[site_name]["CLIENT_ID"]),
                    "CLIENT_SECRET": os.getenv(env_mapping[site_name]["CLIENT_SECRET"]),
                    "TENANT_ID": os.getenv(env_mapping[site_name]["TENANT_ID"]),
                    "SITE_DOMAIN": os.getenv(env_mapping[site_name]["SITE_DOMAIN"]),
                    "SITE_PATH": os.getenv(env_mapping[site_name]["SITE_PATH"]),
                }
            else:
                logger.warning(f"No environment variable mapping found for site: {site_name}")
        return env_variables


    def ratio_to_binary(self, value) -> int:
        """
        Convert ratio string to binary flag.
        Rule:
        - '0/x' -> 0
        - anything else -> 1
        - NaN / None / '' -> 0
        """
        if pd.isna(value):
            return 0

        value = str(value).strip()

        if value == "":
            return 0

        # split 'a/b'
        try:
            numerator = int(value.split("/")[0])
            return 1 if numerator > 0 else 0
        except Exception:
            return 0


    def ratio_to_YN(self, value) -> str:
        if pd.isna(value):
            return "Y"
        try:
            a, _ = str(value).split("/")
            return "Y" if int(a) == 0 else "N"
        except Exception:
            return "Y"

    def match_to_binary(self, gt: str, pred: str) -> int:
        return 1 if gt == pred else 0



    def confusion_metrics(self, y_true: pd.Series, y_pred: pd.Series) -> dict:
        """
        Calculate confusion matrix and metrics.
        """
        TP = int(((y_true == 1) & (y_pred == 1)).sum())
        FP = int(((y_true == 0) & (y_pred == 1)).sum())
        FN = int(((y_true == 1) & (y_pred == 0)).sum())
        TN = int(((y_true == 0) & (y_pred == 0)).sum())

        accuracy = (TP + TN) / max(TP + TN + FP + FN, 1)
        precision = TP / max(TP + FP, 1)
        recall = TP / max(TP + FN, 1)
        f1_score = (
            2 * precision * recall / max(precision + recall, 1e-9)
        )
        weight = TP + FN

        return {
            "TP": TP,
            "FP": FP,
            "FN": FN,
            "TN": TN,
            "accuracy": round(accuracy, 4),
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1_score": round(f1_score, 4),
            "weight": weight
        }

    def evaluation_status_check(self, confusion_key: str, value: str|float) -> dict[str, str]:
        thresholds = get_value_by_path(self.configs, f"Report.metric_thresholds.{confusion_key}")
        if isinstance(value, str):
            value = float(value)

        if value >= float(thresholds['acceptable']) and value < float(thresholds['good']):
            return 'acceptable'
        elif value >= float(thresholds['good']) and value < float(thresholds['excellent']):
            return 'good'
        elif value >= float(thresholds['excellent']):
            return 'excellent'
        else:

            return 'unacceptable'

    def evaluate_predictions(
        self,
        ground_truth_df: pd.DataFrame,
        prediction_df: pd.DataFrame
    ) -> pd.DataFrame:

        results = []

        gt_df = ground_truth_df.sort_values("RTR_Code").reset_index(drop=True)
        pred_df = prediction_df.sort_values("RTR_Code").reset_index(drop=True)

        for col in TARGET_COLUMNS:
            if col not in gt_df.columns or col not in pred_df.columns:
                continue

            gt_flag = gt_df[col].apply(self.ratio_to_YN).reset_index(drop=True)
            pred_flag = pred_df[col].apply(self.ratio_to_YN).reset_index(drop=True)

            # align by position, not index labels
            min_len = min(len(gt_flag), len(pred_flag))
            gt_flag = gt_flag.iloc[:min_len].values
            pred_flag = pred_flag.iloc[:min_len].values

            # expected = always match (1)
            y_true = np.ones(min_len, dtype=int)

            # prediction = match or not
            y_pred = (gt_flag == pred_flag).astype(int)


            metrics = self.confusion_metrics(y_true, y_pred)
            metrics["metric"] = col

            results.append(metrics)

        return pd.DataFrame(results).set_index("metric")



    def prepare_ground_truth(self) -> pd.DataFrame:
        try:
            logger.info("Starting prepare_ground_truth...")
            root = "GroundTruth.input"
            item_path = get_value_by_path(self.configs, f"{root}.path")
            sheet_name = get_value_by_path(self.configs, f"{root}.sheet_name")
            response = self.module_objects[root].get_item_by_path(
                item_path=item_path
            )
            with io.BytesIO(response.content) as file_stream:
                workbook = load_workbook(filename=file_stream, data_only=True)
                if sheet_name:
                    ws = workbook[sheet_name]
                else:
                    ws = workbook.active
                data = list(ws.values)
            headers_1 = data[0]
            combined_headers = []
            for h1 in headers_1:
                combined_headers.append(h1)

            gt_df = pd.DataFrame(data[1:], columns=combined_headers)
            logger.info(f"Ground truth data prepared successfully. Final records: {len(gt_df)}")
            return gt_df
        except Exception as e:
            logger.error(f"Error preparing ground truth data: {e}")
            raise Exception(f"Error preparing ground truth data: {e}")


    def fraud_validation_task(
        self,
        gemini_client,
        meta_data: dict,
        image_part: list,
        one_prompt: str
    ):
        GEMINI_MODEL_VERSION = "gemini-2.5-flash"

        parts = [
            types.Part.from_bytes(
                data=base64.b64decode(img),
                mime_type="image/jpeg"
            )
            for img in image_part
        ]

        parts.append(types.Part.from_text(text="Process these images together"))

        contents = [
            types.Content(
                role="user",
                parts=parts
            )
        ]

        generate_content_config = types.GenerateContentConfig(
            temperature=0,
            seed=0,
            max_output_tokens=65535,
            safety_settings=[
                types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH", threshold="OFF"),
                types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="OFF"),
                types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="OFF"),
                types.SafetySetting(category="HARM_CATEGORY_HARASSMENT", threshold="OFF"),
            ],
            system_instruction=[
                types.Part.from_text(text=f"""{one_prompt}""")
            ],
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        )

        response = gemini_client.models.generate_content(
            model=GEMINI_MODEL_VERSION,
            contents=contents,
            config=generate_content_config
        )

        response_text = None
        if response.candidates and response.candidates[0].content.parts:
            for part in response.candidates[0].content.parts:
                if part.text:
                    response_text = part.text
                    break

        if not response_text:
            raise ValueError("Gemini returned empty text output")

        clean_json = response_text.replace("```", "").replace("json", "").strip()
        result = meta_data
        result["response"] = json.loads(clean_json)

        text_input_tokens = 0
        image_input_tokens = 0

        prompt_details = response.usage_metadata.prompt_tokens_details or []
        for detail in prompt_details:
            if detail.modality == 'TEXT':
                text_input_tokens = detail.token_count
            elif detail.modality == 'IMAGE':
                image_input_tokens = detail.token_count

        text_cache_tokens = 0
        image_cache_tokens = 0
        cache_details = response.usage_metadata.cache_tokens_details or []
        for detail in cache_details:
            if detail.modality == 'TEXT':
                text_cache_tokens = detail.token_count
            elif detail.modality == 'IMAGE':
                image_cache_tokens = detail.token_count

        output_tokens = response.usage_metadata.candidates_token_count
        rtr_meta_data = {
            "create_time": response.create_time,
            "text_cache_tokens": text_cache_tokens,
            "image_cache_tokens": image_cache_tokens,
            "text_input_tokens": text_input_tokens,
            "image_input_tokens" : image_input_tokens,
            "output_tokens": output_tokens
            }

        return result, rtr_meta_data

    def run_coroutine_in_thread(coroutine_function, *args, **kwargs):
        """A synchronous wrapper to run an async function in a new event loop."""
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            result = loop.run_until_complete(coroutine_function(*args, **kwargs))
            return result
        finally:
            loop.close()

    def prepare_predictions(self) -> tuple[pd.DataFrame, list]:
        """
        1. Read image folders from SharePoint
        2. Call Gemini 2.5 Flash per RTR
        3. Return prediction dataframe with same schema as ground truth
        """
        logger.info("Starting prepare_predictions...")

        root = "Prediction.gemini_stream"
        sp_root = f"{root}.input"
        prompt_root = f"{root}.prompt.system_prompt_path.input"

        sp_path = get_value_by_path(self.configs, f"{sp_root}.path")
        site_key = sp_root
        prompt_path = get_value_by_path(self.configs, f"{prompt_root}.path")

        project_root = Path(__file__).resolve().parents[2]
        resolved_prompt_path = project_root / prompt_path.lstrip("/\\")
        system_prompt = load_yaml_string(read_file(str(resolved_prompt_path)))

        # Gemini client
        client = genai.Client(
            http_options=HttpOptions(api_version="v1"),
            location='asia-southeast1'
        )


        predictions = []
        raw_outputs = []

        # List RTR folders
        futures = []
        future_to_rtr = {}
        folders = self.module_objects[site_key].list_folders(sp_path)
        with ThreadPoolExecutor(max_workers=10) as executor:
            for folder in folders:
                try:
                    folder_path = f"{sp_path}/{folder['name']}"
                    images = self.module_objects[site_key].list_files(folder_path)

                    image_files = [
                        img for img in images
                        if img["name"].lower().endswith((".jpg", ".jpeg", ".png"))
                    ][:3]

                    if not image_files:
                        logger.warning(f"No images found in {folder_path}")
                        continue

                    parts = folder["name"].split("-", 1)
                    rtr_code = parts[0]
                    rtr_name = parts[1] if len(parts) > 1 else None

                    image_parts = []

                    for img in image_files:
                        img_resp = self.module_objects[site_key].get_item_by_path(
                            f"{folder_path}/{img['name']}"
                        )

                        image_parts.append(
                            base64.b64encode(img_resp.content).decode("utf-8")
                        )

                    meta_data = {
                        "RTR_Code": rtr_code,
                        "RTR_Name": rtr_name,
                        "Number_of_image": len(image_files),
                        "Photo_Name1": image_files[0]["name"] if len(image_files) > 0 else None,
                        "Photo_Name2": image_files[1]["name"] if len(image_files) > 1 else None,
                        "Photo_Name3": image_files[2]["name"] if len(image_files) > 2 else None,
                    }
                    future = executor.submit(
                        # self.run_coroutine_in_thread,
                        self.fraud_validation_task,
                        gemini_client=client,
                        meta_data=meta_data,
                        image_part=image_parts,
                        one_prompt=system_prompt
                    )

                    futures.append(future)
                    future_to_rtr[future] = rtr_code

                except Exception as e:
                    logger.error(f"Failed preparing folder {folder['name']}: {e}")

            text_input_price_usd = 0.3 / 1_000_000
            image_input_price_usd = 0.3 / 1_000_000
            text_cache_price_usd = 0.03 / 1_000_000
            image_cache_price_usd = 0.03 / 1_000_000
            output_price_usd = 2.5 / 1_000_000

            for future in as_completed(futures):
                try:
                    row, rtr_meta_data = future.result()
                    print(row)
                    print("-"*50)
                    print(rtr_meta_data)
                    print("="*50)

                    create_time = rtr_meta_data.get("create_time")

                    # Normalize create_time → Asia/Bangkok
                    if isinstance(create_time, datetime):
                        if create_time.tzinfo is None:
                            # assume UTC if tzinfo missing
                            create_time = create_time.replace(tzinfo=ZoneInfo("UTC"))
                        create_time_bkk = create_time.astimezone(BKK_TZ)
                    else:
                        create_time_bkk = datetime.now(BKK_TZ)

                    now_bkk = datetime.now(BKK_TZ)

                    duration_secs = (now_bkk - create_time_bkk).total_seconds()

                    result = row["response"]

                    for col in TARGET_COLUMNS:
                        if col not in row:
                            row[col] = result.get(col, "0/3")

                    predictions.append(row)
                    try:
                        raw_output = {
                            "data_date": now_bkk.strftime('%Y%m%d'),
                            "start_time": create_time_bkk.strftime('%Y%m%d %H:%M:%S'),
                            "end_time": now_bkk.strftime('%Y%m%d %H:%M:%S'),
                            "total_time_mins": duration_secs / 60,
                            "type": "AI-Fact-Checker",
                            "gcp_project_id": project_id,
                            "gcp_project_name": project_id,
                            "user_id": "daisyrpa",
                            "source": "SharePoint Control Management",
                            "storage_path": f"Control Management/Check in app/RTR Fraud Validation/fact_check/prediction/input/images_fact_check/{row.get('RTR_Code','')}-{row.get('RTR_Name','')}",
                            "folder": "Control Management/Check in app/RTR Fraud Validation/fact_check/prediction/input/images_fact_check",
                            "filename": f"{row.get('Photo_Name1','')}|{row.get('Photo_Name2','')}|{row.get('Photo_Name3','')}",
                            "file_metadata_min": "-",
                            "status_pass_failed_retry": "success",
                            "error_log_if": "",
                            "latency_ms": duration_secs * 1000,
                            "token_usage_input": rtr_meta_data.get("text_input_tokens", 0) + rtr_meta_data.get("image_input_tokens", 0),
                            "token_usage_output": rtr_meta_data.get("output_tokens", 0),
                            "total_cost_usd": (
                                (rtr_meta_data.get("text_input_tokens", 0) * text_input_price_usd)
                                + (rtr_meta_data.get("image_input_tokens", 0) * image_input_price_usd)
                                + (rtr_meta_data.get("text_cache_tokens", 0) * text_cache_price_usd)
                                + (rtr_meta_data.get("image_cache_tokens", 0) * image_cache_price_usd)
                                + (rtr_meta_data.get("output_tokens", 0) * output_price_usd)
                            ),
                        }
                    except Exception:
                        raw_output = {
                            "data_date": datetime.now().strftime('%Y%m%d'),
                            "start_time": "-",
                            "end_time": "-",
                            "total_time_mins": "-",
                            "type": "AI-Fact-Checker",
                            "gcp_project_id": project_id,
                            "gcp_project_name": project_id,
                            "user_id": "daisyrpa",
                            "source": "SharePoint Control Management",
                            "storage_path": f"Control Management/Check in app/RTR Fraud Validation/fact_check/prediction/input/images_fact_check/{result.get('RTR_Code','')}-{result.get('RTR_Name','')}",
                            "folder": "Control Management/Check in app/RTR Fraud Validation/fact_check/prediction/input/images_fact_check",
                            "filename": f"{result.get('Photo_Name1','')}|{result.get('Photo_Name2','')}|{result.get('Photo_Name3','')}",
                            "file_metadata_min": "-",
                            "status_pass_failed_retry": "fail",
                            "error_log_if": "",
                            "latency_ms": 0,
                            "token_usage_input": 0,
                            "token_usage_output": 0,
                            "total_cost_usd": 0
                        }

                    raw_outputs.append(raw_output)
                    logger.info(f"Processed RTR {future_to_rtr[future]}")
                except Exception as e:
                    logger.error(f"Task failed for RTR {future_to_rtr[future]}: {e}")

        prediction_df = pd.DataFrame(predictions)
        prediction_df = prediction_df.reindex(columns=GROUND_TRUTH_COLUMNS)

        logger.info(f"Prediction data prepared. prediction_df Records: {len(prediction_df)}")
        logger.info(f"Raw outputs prepared. raw_outputs Records: {len(raw_outputs)}")

        return prediction_df, raw_outputs

    def format_excel_report(self, df: pd.DataFrame) -> bytes:
        """
        Format the DataFrame into an Excel file with specific formatting.
        Parameters:
            df (pd.DataFrame): The DataFrame to be formatted.
        Returns:
            bytes: The formatted Excel file in bytes.
        """
        try:
            with io.BytesIO() as output:
                with pd.ExcelWriter(output, engine='openpyxl') as writer:
                    df.to_excel(writer, index=False, sheet_name='Report')
                    worksheet = writer.sheets['Report']

                    # Add auto-filter
                    worksheet.auto_filter.ref = worksheet.dimensions

                    # Freeze top row
                    worksheet.freeze_panes = 'A2'

                    # Adjust column width
                    for column in worksheet.columns:
                        max_length = 0
                        column_letter = column[0].column_letter
                        for cell in column:
                            try:
                                if len(str(cell.value)) > max_length:
                                    max_length = len(str(cell.value))
                            except:
                                pass

                        # Set a reasonable max width to avoid extremely wide columns
                        adjusted_width = min(max_length + 2, 50)
                        worksheet.column_dimensions[column_letter].width = adjusted_width

                data = output.getvalue()
            return data
        except Exception as e:
            logger.error(f"Error formatting Excel report: {e}")
            raise Exception(f"Error formatting Excel report: {e}")

    def merge_upload_report(self, final_df: pd.DataFrame):
        """
        Merge the final report with existing SharePoint report and upload the updated report.
        Parameters:
            final_df (pd.DataFrame): The final report DataFrame to be merged and uploaded.
        Returns:
            None
        """
        try:
            logger.info("Starting merge_upload_report...")
            root = "Report.output"
            output_path = get_value_by_path(self.configs, f"{root}.path")

            if self.module_objects[root].check_item_exists(
                item_path=output_path
            ):
                logger.info("Report file already exists in SharePoint. fetching existing report.")
                response = self.module_objects[root].get_item_by_path(
                    item_path=output_path
                )
                with io.BytesIO(response.content) as file_stream:
                    existing_report_df = pd.read_excel(file_stream)

                report_schema = get_value_by_path(self.configs, "Report.schema")
                if report_schema:
                    schema_map = {col.lower(): col for col in report_schema}
                    new_cols = []
                    for col in existing_report_df.columns:
                        col_str = str(col).strip()
                        new_cols.append(schema_map.get(col_str.lower(), col_str))
                    existing_report_df.columns = new_cols
                    existing_report_df = existing_report_df.loc[:, ~existing_report_df.columns.duplicated(keep='last')]
                else:
                    existing_report_df.columns = [str(col).lower().strip() for col in existing_report_df.columns]

                merged_df = pd.concat([existing_report_df, final_df], ignore_index=True)
            else:
                logger.info("Report file does not exist in SharePoint. Creating new report.")
                merged_df = final_df

            report_schema = get_value_by_path(self.configs, "Report.schema")
            if report_schema:
                for col in report_schema:
                    if col not in merged_df.columns:
                        merged_df[col] = None
                merged_df = merged_df[report_schema]
            elif self.configs.get("report_columns"):
                merged_df = merged_df[self.configs.get("report_columns")]

            merged_df = merged_df.sort_values(by=["created_datetime", "processed_datetime", "dimension", "label"], ascending=[False, False, True, True])
            excel_bytes = self.format_excel_report(merged_df)
            self.module_objects[root].upload_file(
                upload_path=output_path,
                content=excel_bytes,
            )
            logger.info("Final report prepared and uploaded to SharePoint successfully.")
        except Exception as e:
            logger.error(f"Error in merge_upload_report: {e}")
            raise Exception(f"Error in merge_upload_report: {e}")

    def merge_transaction_log(self, raw_outputs: list):
        log_columns = [
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
        transaction_df = pd.DataFrame(raw_outputs, columns=log_columns)
        try:
            logger.info("Starting merge_transaction_log...")
            root = "Report.output"
            output_path = get_value_by_path(self.configs, f"{root}.transaction_path")
            output_path += f"/{datetime.now().strftime('%Y')}/transaction_log_{datetime.now().strftime('%Y%m')}.csv"
            if self.module_objects[root].check_item_exists(
                item_path=output_path
            ):
                logger.info("Report file already exists in SharePoint. fetching existing report.")
                response = self.module_objects[root].get_item_by_path(
                    item_path=output_path
                )
                with io.BytesIO(response.content) as file_stream:
                    try:
                        existing_report_df = pd.read_csv(file_stream)
                        transaction_log_final_df = pd.concat([existing_report_df, transaction_df], ignore_index=True)
                    except Exception:
                        logger.info(f"Creating new file: {output_path}")
                        transaction_log_final_df = transaction_df

                transaction_csv_buffer = io.BytesIO()
                transaction_log_final_df.to_csv(transaction_csv_buffer, index=False, encoding="utf-8-sig")
                transaction_csv_buffer.seek(0)
                self.module_objects[root].upload_file(
                    upload_path=output_path,
                    content=transaction_csv_buffer,
                )
        except Exception as e:
            logger.error(f"fail to upload transaction log: {e}")



    def execute(self):

        ground_truth_df = self.prepare_ground_truth()
        # print("========================================== Ground Truth Data:")
        # print(ground_truth_df.head())
        # print(ground_truth_df.iloc[[0]].T)

        ai_df, ai_raw = self.prepare_predictions()
        # print("========================================== AI Prediction Data:")
        # print(ai_df.head())
        # print(ai_df.iloc[[0]].T)

        metrics_df = self.evaluate_predictions(
            ground_truth_df=ground_truth_df,
            prediction_df=ai_df
        )

        metrics_df["ground_truth_count"] = len(ground_truth_df)
        metrics_df["prediction_count"] = len(ai_df)
        metrics_df["accuracy"] = metrics_df["accuracy"].apply(percentage_format)
        metrics_df["precision"] = metrics_df["precision"].apply(percentage_format)
        metrics_df["recall"] = metrics_df["recall"].apply(percentage_format)
        metrics_df["f1_score"] = metrics_df["f1_score"].apply(percentage_format)
        metrics_df["accuracy_status"] = metrics_df["accuracy"].apply(lambda v: self.evaluation_status_check("accuracy", v))
        metrics_df["precision_status"] = metrics_df["precision"].apply(lambda v: self.evaluation_status_check("precision", v))
        metrics_df["recall_status"] = metrics_df["recall"].apply(lambda v: self.evaluation_status_check("recall", v))
        metrics_df["f1_score_status"] = metrics_df["f1_score"].apply(lambda v: self.evaluation_status_check("f1_score", v))

        # Add metadata columns
        metrics_df["created_datetime"] = datetime.now(ZoneInfo("Asia/Bangkok")).strftime("%Y-%m-%d %H:%M:%S")
        metrics_df["processed_datetime"] = datetime.now(ZoneInfo("Asia/Bangkok")).strftime("%Y-%m-%d %H:%M:%S")
        metrics_df["gcp_project_id"] = project_id
        metrics_df["gcp_project_name"] = project_id
        metrics_df["model_version"] = "gemini-2.5-flash"
        metrics_df["system_prompt"] = "config/system_prompt/rtr.yml"

        metadata_cols = [
            "created_datetime",
            "processed_datetime",
            "gcp_project_id",
            "gcp_project_name",
            "model_version",
            "system_prompt",
            "ground_truth_count",
            "prediction_count",
            "dimension",
            "accuracy",
            "accuracy_status",
            "precision",
            "precision_status",
            "recall",
            "recall_status",
            "f1_score",
            "f1_score_status",
            "TP",
            "FP",
            "FN",
            "TN",
            "weight"
        ]

        # Create dimension column FIRST
        metrics_df = metrics_df.reset_index()
        metrics_df = metrics_df.rename(columns={"metric": "dimension"})

        # NOW slice
        metrics_df = metrics_df[metadata_cols]
        self.merge_upload_report(final_df=metrics_df)
        self.merge_transaction_log(raw_outputs=ai_raw)


if __name__ == "__main__":
    logger.info("Starting Fact Checker Module...")
    parser = argparse.ArgumentParser(description="Fact Checker Module Execution")
    parser.add_argument("--config_path", help="Path to the configuration file", required=True)
    args = parser.parse_args()

    fact_checker = FactCheckerModule(config_path=args.config_path)


    fact_checker.execute()
