import os, io, asyncio, json, time, re, argparse
import pandas as pd
from zoneinfo import ZoneInfo
from datetime import datetime, timedelta
from typing import Any, Dict, List, Tuple
from google import genai
from google.genai.types import CreateBatchJobConfig, JobState, HttpOptions
from openpyxl import load_workbook
from pathlib import Path
from dotenv import load_dotenv
import yaml
from src.log import logger
from src.modules.sharepoint import SharePointModule
from src.modules.gcs import GCSModule
from src.main import get_analysis_schema
from src.modules.gemini_batch import GeminiBatchModule
from src.modules.audit_log.audit_log import AuditLogModule
from src.modules.audit_log.transaction import TransactionPayload
from src.utils.common import (
    load_yaml_string,
    load_yaml,
    pydantic_resolve_refs,
    recursive_dict_value_by_key,
    export_pandas_df_to_markdown, # For debugging purpose
    percentage_format,
    resolve_date,
    resolve_env,
    read_file,
    get_value_by_path,
)

load_dotenv(override=True)

class FactCheckerModule:
    def __init__(self, config_path: str):
        """
        Initialize FactCheckerModule with configurations.
        Parameters:
            config_path (str): Path to the YAML configuration file.
        Returns:
            None
        """
        resolve_config = load_yaml_string(resolve_env(read_file(config_path)))
        self.config_validation(resolve_config)
        self.configs = resolve_config
        self.timezone = ZoneInfo("Asia/Bangkok")
        logger.info("FactChecker configuration loaded successfully.")
        logger.info("Configurations:")
        for key, value in self.configs.items():
            logger.info(f"{key}: {value}")

        sharepoint_mapping = {}
        for key, value in self.__get_sharepoint_env_variables().items():
            sharepoint_mapping[key] = SharePointModule(
                client_id=value["CLIENT_ID"],
                client_secret=value["CLIENT_SECRET"],
                tenant_id=value["TENANT_ID"],
                site_domain=value["SITE_DOMAIN"],
                site_path=value["SITE_PATH"],
            )
        
        gcs_bucket_mapping = {}
        for item in self.recursive_extract_bucket_names(self.configs):
            gcs_bucket_mapping[item[0]] = GCSModule(
                project_id=item[1],
                bucket_name=item[2],
            )    
        self.module_objects = {**sharepoint_mapping, **gcs_bucket_mapping}
        
    def config_validation(self, configs: Dict[str, Any]) -> None:
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
        pred_gemini = get_value_by_path(configs, "Prediction.gemini_batch")
        if not pred_gemini:
             raise ValueError("Missing 'Prediction.gemini_batch' configuration.")
        
        # Validate gemini_batch components
        self._validate_io_config(get_value_by_path(configs, "Prediction.gemini_batch.model_config.input"), "Prediction.gemini_batch.model_config.input")
        self._validate_io_config(get_value_by_path(configs, "Prediction.gemini_batch.input"), "Prediction.gemini_batch.input")
        self._validate_io_config(get_value_by_path(configs, "Prediction.gemini_batch.processing"), "Prediction.gemini_batch.processing")
        self._validate_io_config(get_value_by_path(configs, "Prediction.gemini_batch.output"), "Prediction.gemini_batch.output")
        self._validate_io_config(get_value_by_path(configs, "Prediction.gemini_batch.prompt.system_prompt_path.input"), "Prediction.gemini_batch.prompt.system_prompt_path.input")
        self._validate_io_config(get_value_by_path(configs, "Prediction.gemini_batch.prompt.user_prompt_path.input"), "Prediction.gemini_batch.prompt.user_prompt_path.input")
        
        self._validate_io_config(get_value_by_path(configs, "Prediction.output_prompt_log"), "Prediction.output_prompt_log")

        # Validate Report
        self._validate_io_config(get_value_by_path(configs, "Report.output"), "Report.output")
        
        if "schema" not in configs["Report"]:
             raise ValueError("Missing 'Report.schema' configuration.")
        if "metric_thresholds" not in configs["Report"]:
             raise ValueError("Missing 'Report.metric_thresholds' configuration.")

    def _validate_io_config(self, config_section: Dict[str, Any], path: str) -> None:
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

    def __get_sharepoint_env_variables(self) -> Dict[str, Dict[str, str]]:
        """
        Retrieve environment variables for SharePoint site configurations.
        Parameters:
            None
        Returns:
            Dict[str, Dict[str, str]]: A dictionary containing environment variables for each SharePoint site.
        """
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

        def recursive_extract_site_names(config_section: Dict[Any,Any], parent_key: str = "") -> List[Tuple[str, str]]:
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
    
    def recursive_extract_bucket_names(self, config_section: Dict[Any,Any], parent_key: str = "") -> List[Tuple[str, str, str]]:
        """
        Recursively extract bucket names and their config paths.
        Parameter:
            config_section (Dict[Any,Any]): The configuration section to search.
            parent_key (str): The accumulated key path.
        Returns:
            List[Tuple[str, str, str]]: A list of (project_id, bucket_name, config_path).
        """
        bucket_list = []
        if isinstance(config_section, dict):
            # Check if current dict has both bucket_name and type='gcs'
            if "bucket_name" in config_section and config_section.get("type") == "gcs":
                bucket_list.append((
                    parent_key,
                    config_section.get("gcp_project_id"), 
                    config_section.get("bucket_name")
                ))
            
            # Recursively check all values
            for key, value in config_section.items():
                if isinstance(value, (dict, list)):
                    current_key = f"{parent_key}.{key}" if parent_key else key
                    bucket_list.extend(self.recursive_extract_bucket_names(value, current_key))
        
        elif isinstance(config_section, list):
            for index, item in enumerate(config_section):
                current_key = f"{parent_key}.{index}" if parent_key else str(index)
                bucket_list.extend(self.recursive_extract_bucket_names(item, current_key))
        
        return bucket_list

    def upload_voice_to_gcs(self, process_date: datetime|str) -> None:
        """
        Upload voice files from SharePoint to GCS.
        Parameters:
            process_date (datetime|str): The datetime or date string (YYYY-MM-DD HH:MM:SS) representing the process date.
        Returns:
            None
        """
        try:
            if isinstance(process_date, datetime):
                process_date = process_date.strftime("%Y-%m-%d %H:%M:%S")
            logger.info(f"Starting upload_voice_to_gcs for date: {process_date}")
            # Retrieve SharePoint configurations
            input_root = "Prediction.gemini_batch.input"
            folder_path = get_value_by_path(self.configs, f"{input_root}.path")
            
            # Retrieve batch GCS configurations
            processing_root = "Prediction.gemini_batch.processing"
            batch_path = get_value_by_path(self.configs, f"{processing_root}.path")
            batch_path = resolve_date(batch_path[1:], process_date) if batch_path.startswith("/") else resolve_date(batch_path, process_date)
        
            files = self.module_objects[input_root].list_files(
                folder_path=folder_path
            )
            filtered_files = []
            for file in files:
                file_name = file.get("name", None)
                file_id = file.get("id", None)
                file_path = file.get("parentReference", {}).get("path", None)
                file_created_datetime = file.get("createdDateTime", None)
                file_extension = Path(file_name).suffix.lower() if file_name else None
                if file_extension and file_extension not in [".wav"]:
                    logger.info(f"Skipping file {file_name} with unsupported extension {file_extension}")
                    continue
                filtered_files.append({
                    "file_name": file_name,
                    "file_extension": file_extension,
                    "file_id": file_id,
                    "file_path": file_path,
                    "file_created_datetime": file_created_datetime,
                })
            logger.info(f"Total files retrieved from SharePoint: {len(filtered_files)}")
            stream_list = []
            for item in filtered_files:
                stream_list.append({
                    "download": item["file_path"].replace("/drive/root:", "") + "/" + item["file_name"],
                    "upload": batch_path + "/" + item["file_name"],
                    "mime_type": "audio/wav",
                })
            logger.info(f"Uploading {len(stream_list)} files to GCS...")
            asyncio.run(
                self.module_objects[processing_root].upload_sharepoint_to_gcs(
                    sharepoint_object=self.module_objects[input_root],
                    stream_list=stream_list
                )
            )
            logger.info("Voice files uploaded successfully.")
        except Exception as e:
            logger.error(f"Error uploading voice to GCS: {e}")
            raise Exception(f"Error uploading voice to GCS: {e}")
    
    def execute_gemini_batch(self, process_date: datetime|str) -> None:
        """
        Execute Gemini batch job for processing voice files.
        Parameters:
            process_date (datetime|str): The datetime or date string (YYYYMMDD) representing the process date.
        Returns:
            None
        """
        try:
            if isinstance(process_date, datetime):
                process_date = process_date.strftime("%Y-%m-%d %H:%M:%S")
            logger.info(f"Starting execute_gemini_batch for date: {process_date}")
            mapping_file_type = {
                ".txt": "text/plain",
                ".wav": "audio/wav",
                ".jsonl": "application/jsonl",
            }
            input_root = "Prediction.gemini_batch.processing"
            input_batch_path = get_value_by_path(self.configs, f"{input_root}.path")
            input_batch_path = resolve_date(input_batch_path[1:], process_date) if input_batch_path.startswith("/") else resolve_date(input_batch_path, process_date)
            output_root = "Prediction.gemini_batch.output"
            output_batch_path = get_value_by_path(self.configs, f"{output_root}.path")
            output_batch_path = resolve_date(output_batch_path[1:], process_date) if output_batch_path.startswith("/") else resolve_date(output_batch_path, process_date)

            user_prompt_root = "Prediction.gemini_batch.prompt.user_prompt_path.input"
            user_prompt_path = get_value_by_path(self.configs, f"{user_prompt_root}.path")

            system_prompt_root = "Prediction.gemini_batch.prompt.system_prompt_path.input"
            system_prompt_path = get_value_by_path(self.configs, f"{system_prompt_root}.path")
            if system_prompt_path.startswith("/"):
                system_prompt_path = system_prompt_path[1:]
            system_prompt = load_yaml(system_prompt_path).get("system_prompt", "")

            response = self.module_objects[user_prompt_root].get_item_by_path(
                item_path=user_prompt_path
            )
            user_prompt = response.content.decode("utf-8").strip()
            prompt_sys_user = system_prompt.replace("{user_prompt}", user_prompt)
            list_input_files = self.module_objects[input_root].list_files(
                prefix=input_batch_path
            )
            list_input_files = [file for file in list_input_files if Path(file).suffix.lower() in [".wav"]]
            list_input_files = [f"gs://{get_value_by_path(self.configs, f'{input_root}.bucket_name')}/{file}" for file in list_input_files if not file.endswith("/")]
            logger.info(f"Total input files for Gemini batch: {len(list_input_files)}")
            gemini_config_root = "Prediction.gemini_batch.model_config.input"
            gemini_config_path = get_value_by_path(self.configs, f"{gemini_config_root}.path")
            if gemini_config_path.startswith("/"):
                gemini_config_path = gemini_config_path[1:]
            gemini_config = load_yaml(gemini_config_path)

            # response_schema = OutputValidation.model_json_schema()
            # response_schema = pydantic_resolve_refs(response_schema)
            # gemini_config["generation_config"].update({"responseSchema": response_schema})
            jsonl = []
            function_declaration = get_analysis_schema()
            for input_file in list_input_files:             
                json_record = {
                    "request": {
                        "contents": [
                            {
                                "role": "user",
                                "parts": [
                                    {"text": prompt_sys_user},
                                    {
                                        "fileData": {
                                            "fileUri": input_file,
                                            "mimeType": mapping_file_type.get(Path(input_file).suffix.lower())
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
                        }
                    }
                }
                json_record["request"].update({"generationConfig": gemini_config["generation_config"]})
                jsonl.append(json_record)
                logger.info(f"Prepared JSONL record for input file: {input_file}")
            jsonl_input_path = input_batch_path + "/gemini_input.jsonl"
            jsonl_content = "\n".join(json.dumps(rec, ensure_ascii=False) for rec in jsonl)
            self.module_objects[input_root].update_content_to_gcs(
                content=jsonl_content.encode("utf-8"),
                mime_type=mapping_file_type[".jsonl"],
                destination_path=jsonl_input_path
            )

            src_gemini = f"gs://{get_value_by_path(self.configs, f'{input_root}.bucket_name')}/{jsonl_input_path}"
            des_gemini = f"gs://{get_value_by_path(self.configs, f'{output_root}.bucket_name')}/{output_batch_path}/"

            genai_module = GeminiBatchModule(
                genai_location=gemini_config.get("location")
            )
            config = CreateBatchJobConfig(dest=des_gemini)
            job = genai_module.create_batch_job(
                model_nm=gemini_config.get("model"),
                src_uri=src_gemini,
                config=config
            )
            logger.info(f"Gemini batch job created with ID: {job.name}")
            logger.info(f"Source URI: {src_gemini}")
            logger.info(f"Destination URI: {des_gemini}")
            time.sleep(5)
            # if genai_client.batches.get(name=job.name).state.name not in [
            job_status = genai_module.status_check_batch_job(job_name=job.name)
            if job_status not in [
                'JOB_STATE_FAILED',
                'JOB_STATE_CANCELLED',
                'JOB_STATE_EXPIRED',
            ]:
                logger.info("Gemini batch job completed successfully.")
            else:
                logger.error("Gemini batch job failed.")
                raise Exception("Gemini batch job failed.")
        except Exception as e:
            logger.error(f"Error executing Gemini batch job: {e}")
            raise Exception(f"Error executing Gemini batch job: {e}")

    def retrieve_gemini_batch_output(self, batch_process_date: datetime|str) -> list[dict[str, Any]]:
        """
        Retrieve the latest Gemini batch output file content from GCS.
        Parameters:
            batch_process_date (datetime|str): The datetime or date string (YYYYMMDD) representing the batch process date.
        Returns:
            list[dict[str, Any]]: A list of dictionaries representing the parsed JSONL results.
        """
        try:
            if isinstance(batch_process_date, datetime):
                batch_process_date = batch_process_date.strftime("%Y%m%d")
            logger.info(f"Starting retrieve_gemini_batch_output for date: {batch_process_date}")
            root = "Prediction.gemini_batch.output"
            batch_path = get_value_by_path(self.configs, f"{root}.path")
            batch_path = resolve_date(batch_path[1:], batch_process_date) if batch_path.startswith("/") else resolve_date(batch_path, batch_process_date)
            output_list = self.module_objects[root].list_files(
                prefix=batch_path
            )

            prediction_files = [
                f for f in output_list 
                if "prediction-model-" in f 
                and f.endswith(".jsonl") 
                and "incremental_predictions" not in f
            ]
            
            if not prediction_files:
                logger.warning("No final prediction files found. Batch job may still be running.")
                logger.info("Files found: " + str(output_list))
                raise Exception("No final prediction files found. Batch job may still be running or has not completed yet.")

            def extract_timestamp(filename):
                try:
                    # Split by '/' to get the folder name, then find the timestamp part
                    parts = filename.split('/')
                    for part in parts:
                        if part.startswith("prediction-model-"):
                            ts_str = part.replace("prediction-model-", "")
                            return datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%S.%fZ")
                    return datetime.min
                except ValueError:
                    return datetime.min

            prediction_files.sort(key=extract_timestamp, reverse=True)
            latest_file = prediction_files[0]
            logger.info(f"Latest prediction file: {latest_file}")

            json_list = GeminiBatchModule.retrieve_batch_results(
                gcs_module=self.module_objects[root],
                batch_output_path=latest_file
            )

            return json_list
        except Exception as e:
            logger.error(f"Error retrieving Gemini batch output: {e}")
            raise Exception(f"Error retrieving Gemini batch output: {e}")
        
    def prepare_ground_truth(self) -> pd.DataFrame:
        """
        Prepare ground truth DataFrame from SharePoint Excel file.
        Parameters:
            None
        Returns:
            pd.DataFrame: The prepared ground truth DataFrame.
        """
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
            headers_2 = data[1]
            combined_headers = []
            for h1, h2 in zip(headers_1, headers_2):
                if (h2 is None or h2 == "") and h1 is not None and h1 != "":
                    combined_headers.append(h1)
                else:
                    combined_headers.append(h2)
            gt_df = pd.DataFrame(data[2:], columns=combined_headers)
            logger.info(f"Initial ground truth records: {len(gt_df)}")

            # Filter out records where call_id is 0, None, or blank
            gt_df = gt_df[
                (gt_df['call_id'].notna()) & 
                (gt_df['call_id'] != 0) & 
                (gt_df['call_id'].astype(str).str.strip() != "")
            ]
            
            # Clean gt_df: phone_number 0 -> None, blanks -> None
            if 'phone_number' in gt_df.columns:
                gt_df['phone_number'] = gt_df['phone_number'].replace({0: None, '0': None})

            # Convert all blank strings to None in gt_df
            gt_df = gt_df.replace(r'^\s*$', None, regex=True)
            gt_df = gt_df.where(pd.notnull(gt_df), None)

            logger.info(f"Ground truth data prepared successfully. Final records: {len(gt_df)}") 
            return gt_df
        except Exception as e:
            logger.error(f"Error preparing ground truth data: {e}")
            raise Exception(f"Error preparing ground truth data: {e}")

    def prepare_predictions(self, batch_process_date: datetime|str, content: list[dict[str, Any]]) -> Tuple[pd.DataFrame, list]:
        """
        Prepare predictions DataFrame from Gemini batch output content.
        Parameters:
            batch_process_date (datetime|str): The datetime or date string (YYYYMMDD) representing the batch process date.
            content (list[dict[str, Any]]): The list of dictionaries representing the parsed JSONL results.
        Returns:
            pd.DataFrame: The prepared predictions DataFrame.
        """
        try:
            logger.info("Starting prepare_predictions...")
            pre_data = []
            if isinstance(batch_process_date, datetime):
                batch_process_date = batch_process_date.strftime("%Y%m%d")

            for record in content:
                file_url = recursive_dict_value_by_key(record, "fileUri")
                file_name = os.path.splitext(os.path.basename(file_url[0]))[0] if file_url else None
                call_id = file_name.split("_")[0] if file_name else None
                phone_number = file_name.split("_")[1] if file_name and len(file_name.split("_")) > 1 else None

                req = record.get("request", {})
                res = record.get("response", {})

                # Retrieve system prompt text
                req_contents = req.get("contents", [])
                system_prompt = None
                if req_contents:
                    req_parts = req_contents[0].get("parts", [])
                    if req_parts:
                        system_prompt = req_parts[0].get("text", "")

                # Retrieve response text
                res_candidates = res.get("candidates", [])
                raw_response = None
                if res_candidates:
                    content = res_candidates[0].get("content", {})
                    parts = content.get("parts", [])
                    if parts:
                        raw_response = parts[0].get("text", None)
                
                res_info = None
                res_info = {
                    "model_version": res.get("modelVersion", None),
                    "processed_time": record.get("processed_time", None),
                }

                # if not raw_response:
                #     logger.warning(f"No valid ai_result found for file: {file_name}")
                #     continue

                try:
                    ai_result = record['response']['candidates'][0]['content']['parts'][0]['functionCall']['args']

                    for product_name in ai_result["product"].keys():
                        if ai_result["product"][product_name] is None:
                            continue
                        product_result = ai_result["product"][product_name]
                        main = ""
                        if product_result.get("main", None) is not None:
                            main = product_result.get("main", {}).get("reason", "")
                        
                        secondary = ""
                        if product_result.get("secondary", None) is not None:
                            secondary = product_result.get("secondary", {}).get("reason", "")

                        third = ""
                        if product_result.get("third", None) is not None:
                            third = product_result.get("third", {}).get("reason", "")

                        pre_data.append({
                            "call_id": call_id,
                            "phone_number": phone_number,
                            'call_result': product_result.get("retention_outcome", ""),
                            'product': product_name,
                            'main': main,
                            'secondary': secondary,
                            'third': third,
                            'system_prompt': system_prompt,
                            'model_version': res_info["model_version"],
                            'processed_time': res_info["processed_time"],
                            'message': None
                        })
                except Exception as ex:
                    logger.error(f"Error processing AI result for file {file_name}: {ex}")
                    pre_data.append({
                        "call_id": call_id,
                        "phone_number": phone_number,
                        'call_result' : None,
                        'product': None,
                        'main' : None,
                        'secondary' : None,
                        'third' : None,
                        'system_prompt': system_prompt,
                        'model_version': res_info["model_version"],
                        'processed_time': res_info["processed_time"],
                        'message': f"Error: {ex}"
                    })
            pre_data_df = pd.DataFrame(pre_data)
            if pre_data_df.empty:
                logger.warning("No valid prediction records found after processing Gemini batch output.")
                return pre_data_df
            pre_data_df['processed_time'] = pd.to_datetime(pre_data_df['processed_time'], format='ISO8601', utc=True).dt.tz_convert(self.timezone).dt.strftime("%Y-%m-%d %H:%M:%S")
            valid_prompts_df = pre_data_df.dropna(subset=['system_prompt'])
            
            root = "Prediction.output_prompt_log"
            bucket_name = get_value_by_path(self.configs, f"{root}.bucket_name")
            dest_system_prompt_path = get_value_by_path(self.configs, f"{root}.path")
            if not valid_prompts_df.empty:
                system_prompt_text = valid_prompts_df.sort_values('processed_time', ascending=False).iloc[0]['system_prompt']
                max_processed_time = valid_prompts_df['processed_time'].max()
                dest_system_prompt_path = resolve_date(dest_system_prompt_path[1:], max_processed_time) if dest_system_prompt_path.startswith("/") else resolve_date(dest_system_prompt_path, max_processed_time)
            else:
                logger.warning("No valid system prompts found in predictions data. processed_time is empty or system_prompt is not found.")
                logger.warning("Uploading empty system prompt file.")
                system_prompt_text = ""
                logger.warning("Setting processed_time to current datetime.")
                logger.warning("Setting batch_processed_time of report to None.")
                max_processed_time = None
                default_time = datetime.now(tz=self.timezone).strftime("%Y%m%d %H:%M:%S")
                dest_system_prompt_path = resolve_date(dest_system_prompt_path[1:], default_time) if dest_system_prompt_path.startswith("/") else resolve_date(dest_system_prompt_path, default_time)

            self.module_objects[root].update_content_to_gcs(
                content=system_prompt_text.encode("utf-8"),
                mime_type="text/plain",
                destination_path=dest_system_prompt_path
            )
            pre_data_df['system_prompt'] = f"gs://{bucket_name}/" + dest_system_prompt_path
            pre_data_df['batch_processed_time'] = max_processed_time
            pre_data_df = pre_data_df.drop(columns=['processed_time'])
            logger.info(f"Predictions prepared successfully. Records: {len(pre_data_df)}")
            return pre_data_df
        except Exception as e:
            logger.error(f"Error preparing predictions data: {e}")
            raise Exception(f"Error preparing predictions data: {e}")

    def pre_process(self, prediction_df: pd.DataFrame, ground_truth_df: pd.DataFrame) -> pd.DataFrame:
        """
        Pre-process prediction and ground truth DataFrames for comparison.
        Parameters:
            prediction_df (pd.DataFrame): The predictions DataFrame.
            ground_truth_df (pd.DataFrame): The ground truth DataFrame.
        Returns:
            pd.DataFrame: The pre-processed prediction and ground truth DataFrames.
        """
        try:
            logger.info("Starting pre_process...")
            logger.info(f"Input shapes - Prediction: {prediction_df.shape}, Ground Truth: {ground_truth_df.shape}")
            # Align ground_truth_df columns to match prediction_df
            required_columns = prediction_df.columns.tolist()
            
            # Filter/Reorder ground_truth_df to match prediction_df schema
            # Add missing columns with None
            for col in required_columns:
                if col not in ground_truth_df.columns:
                    logger.warning(f"Column '{col}' not found in ground_truth_df. Creating with None.")
                    ground_truth_df[col] = None
            
            aligned_gt_df = ground_truth_df[required_columns]
            
            # Filter out records where call_id is 0, None, or blank
            aligned_gt_df = aligned_gt_df[
                (aligned_gt_df['call_id'].notna()) & 
                (aligned_gt_df['call_id'] != 0) & 
                (aligned_gt_df['call_id'].astype(str).str.strip() != "")
            ]
            
            # Clean aligned_gt_df: phone_number 0 -> None, blanks -> None
            if 'phone_number' in aligned_gt_df.columns:
                aligned_gt_df['phone_number'] = aligned_gt_df['phone_number'].replace({0: None, '0': None})

            # Convert all blank strings to None in aligned_gt_df
            aligned_gt_df = aligned_gt_df.replace(r'^\s*$', None, regex=True)
            aligned_gt_df = aligned_gt_df.where(pd.notnull(aligned_gt_df), None)

            # Clean prediction_df: phone_number 0 -> None, blanks -> None
            if 'phone_number' in prediction_df.columns:
                prediction_df['phone_number'] = prediction_df['phone_number'].replace({0: None, '0': None})

            # Convert all blank strings to None in prediction_df
            prediction_df = prediction_df.replace(r'^\s*$', None, regex=True)
            prediction_df = prediction_df.where(pd.notnull(prediction_df), None)

            # Normalize string columns to lowercase
            for col in prediction_df.columns:
                if prediction_df[col].dtype == 'object':
                    prediction_df[col] = prediction_df[col].str.lower().str.strip()

            for col in aligned_gt_df.columns:
                if aligned_gt_df[col].dtype == 'object':
                    aligned_gt_df[col] = aligned_gt_df[col].str.lower().str.strip()

            logger.info(f"Pre-process completed. Shapes - Prediction: {prediction_df.shape}, Ground Truth: {aligned_gt_df.shape}")
            return prediction_df, aligned_gt_df
        except Exception as e:
            logger.error(f"Error in pre_process: {e}")
            raise Exception(f"Error in pre_process: {e}")
    
    def call_result_calculation(self, merged_df: pd.DataFrame) -> pd.DataFrame:
        """
        Calculate confusion matrix and metrics summary for call_result.
        Parameters:
            merged_df (pd.DataFrame): The merged DataFrame containing ground truth and predictions.
        Returns:
            pd.DataFrame: The metrics summary DataFrame for call_result.
        """
        # Filter out records where call_result is NaN in either ground truth or prediction
        call_result_df = merged_df.dropna(subset=['call_result_gt']).copy()
        
        confusion_matrix = pd.crosstab(
            call_result_df['call_result_gt'], 
            call_result_df['call_result_pred'], 
            rownames=['actual'], 
            colnames=['prediction'], 
            dropna=False,
            margins=True,
            margins_name='total'
        )
        
        # Reorder columns and rows to match the image: Save, Churn, Unknown, Undefined
        desired_order = ['save', 'churn', 'unknown', 'undefined', 'total']
        
        # Filter desired_order to only include keys that exist in the matrix
        existing_columns = [col for col in desired_order if col in confusion_matrix.columns]
        # Append other columns (like NaN)
        other_columns = [col for col in confusion_matrix.columns if col not in existing_columns]
        confusion_matrix = confusion_matrix[existing_columns + other_columns]
        
        existing_index = [idx for idx in desired_order if idx in confusion_matrix.index]
        other_index = [idx for idx in confusion_matrix.index if idx not in existing_index]
        confusion_matrix = confusion_matrix.reindex(existing_index + other_index)

        # Calculate Metrics Summary
        # Use a matrix without margins for calculation and ensure all classes are present
        cm_calc = pd.crosstab(
            call_result_df['call_result_gt'], 
            call_result_df['call_result_pred'], 
            rownames=['actual'], 
            colnames=['prediction'], 
            dropna=False
        )
        
        target_classes = ['save', 'churn', 'unknown', 'undefined']
        # Ensure all target classes are in the calculation matrix
        all_classes = sorted(list(set(cm_calc.index) | set(cm_calc.columns) | set(target_classes)), key=lambda x: str(x))
        cm_calc = cm_calc.reindex(index=all_classes, columns=all_classes, fill_value=0)
        
        metrics_data = []
        total_samples = cm_calc.values.sum()

        for cls in target_classes:
            tp = cm_calc.loc[cls, cls]
            fp = cm_calc[cls].sum() - tp
            fn = cm_calc.loc[cls].sum() - tp
            tn = total_samples - (tp + fp + fn)
            
            fn_mask = (call_result_df['call_result_gt'] == cls) & (call_result_df['call_result_pred'] != cls)
            fn_ids = call_result_df.loc[fn_mask, 'call_id'].tolist()
            
            weight = tp + fn
            
            accuracy = (tp + tn) / total_samples if total_samples > 0 else 0
            precision = tp / (tp + fp) if (tp + fp) > 0 else 0
            recall = tp / (tp + fn) if (tp + fn) > 0 else 0
            f1 = 2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else 0
            
            metrics_data.append({
                'call_result': cls,
                'accuracy': accuracy,
                'precision': precision,
                'recall': recall,
                'f1_score': f1,
                'TP': tp,
                'FP': fp,
                'FN': fn,
                'TN': tn,
                'weight': weight
            })

        metrics_df = pd.DataFrame(metrics_data)
        
        # Calculate Weighted Average
        total_weight = metrics_df['weight'].sum()
        if total_weight > 0:
            weighted_avg = {
                'call_result': 'weighted_avg',
                'accuracy': (metrics_df['accuracy'] * metrics_df['weight']).sum() / total_weight,
                'precision': (metrics_df['precision'] * metrics_df['weight']).sum() / total_weight,
                'recall': (metrics_df['recall'] * metrics_df['weight']).sum() / total_weight,
                'f1_score': (metrics_df['f1_score'] * metrics_df['weight']).sum() / total_weight,
                'TP': metrics_df['TP'].sum(),
                'FP': metrics_df['FP'].sum(),
                'FN': metrics_df['FN'].sum(),
                'TN': metrics_df['TN'].sum(),
                'weight': total_weight
            }
            metrics_df = pd.concat([metrics_df, pd.DataFrame([weighted_avg])], ignore_index=True)

        return metrics_df

    def reason_calculation(self, merged_df: pd.DataFrame) -> pd.DataFrame:
        """
        Calculate confusion matrix and metrics summary for reasons.
        Parameters:
            merged_df (pd.DataFrame): The merged DataFrame containing ground truth and predictions.
        Returns:
            pd.DataFrame: The metrics summary DataFrame for reasons.
        """
        target_classes = [
            'network', 
            'promotion related', 
            'device promotion related', 
            'save cost', 
            'contract end', 
            'sale upsell problem', 
            'dissatisfied service', 
            'other', 
            'post to pre', 
            'customer reason',  
            'down sell not success'
        ]
        
        def get_reasons_set(row, suffix):
            reasons = set()
            for col in ['main', 'secondary', 'third']:
                val = row.get(f'{col}{suffix}')
                if pd.notna(val) and str(val).strip() != '':
                    # Split by comma if multiple reasons are in one cell
                    parts = [p.strip().lower() for p in str(val).split(',')]
                    reasons.update(parts)
            return reasons

        # Pre-calculate sets for all rows
        merged_df['gt_reasons_set'] = merged_df.apply(lambda row: get_reasons_set(row, '_gt'), axis=1)
        merged_df['pred_reasons_set'] = merged_df.apply(lambda row: get_reasons_set(row, '_pred'), axis=1)

        # Initialize detailed DataFrame
        detailed_df = pd.DataFrame()
        detailed_df['call_id'] = merged_df['call_id']
        detailed_df['Actual'] = merged_df['gt_reasons_set'].apply(lambda x: ' , '.join(sorted(x)))
        detailed_df['Predict'] = merged_df['pred_reasons_set'].apply(lambda x: ' , '.join(sorted(x)))

        metrics_data = []
        
        for i, cls in enumerate(target_classes, 1):
            cls_lower = cls.lower()
            # Vectorized boolean checks
            is_in_gt = merged_df['gt_reasons_set'].apply(lambda x: cls_lower in x)
            is_in_pred = merged_df['pred_reasons_set'].apply(lambda x: cls_lower in x)
            # Vectorized TP/FP/FN/TN calculation
            tp_col = (is_in_gt & is_in_pred).astype(int)
            fp_col = (~is_in_gt & is_in_pred).astype(int)
            fn_col = (is_in_gt & ~is_in_pred).astype(int)
            tn_col = (~is_in_gt & ~is_in_pred).astype(int)
            
            fp_ids = merged_df.loc[(~is_in_gt & is_in_pred), 'call_id']
            fn_ids = merged_df.loc[(is_in_gt & ~is_in_pred), 'call_id']
            
            # Add columns to detailed_df
            detailed_df[f'TP {i}'] = tp_col
            detailed_df[f'FP {i}'] = fp_col
            detailed_df[f'FN {i}'] = fn_col
            detailed_df[f'TN {i}'] = tn_col
            
            # Calculate aggregate metrics
            tp = tp_col.sum()
            fp = fp_col.sum()
            fn = fn_col.sum()
            tn = tn_col.sum()
            total = tp + fp + fn + tn
            
            weight = tp + fn
            
            accuracy = (tp + tn) / total if total > 0 else 0
            precision = tp / (tp + fp) if (tp + fp) > 0 else 0
            recall = tp / (tp + fn) if (tp + fn) > 0 else 0
            f1 = 2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else 0
            
            metrics_data.append({
                'id': i,
                'reason': cls,
                'accuracy': accuracy,
                'precision': precision,
                'recall': recall,
                'f1_score': f1,
                'TP': tp,
                'FP': fp,
                'FN': fn,
                'TN': tn,
                'weight': weight
            })
            
        metrics_df = pd.DataFrame(metrics_data)
        
        # Calculate Weighted Average
        total_weight = metrics_df['weight'].sum()
        if total_weight > 0:
            weighted_avg = {
                'id': 'weighted_avg',
                'reason': 'weighted_avg',
                'accuracy': (metrics_df['accuracy'] * metrics_df['weight']).sum() / total_weight,
                'precision': (metrics_df['precision'] * metrics_df['weight']).sum() / total_weight,
                'recall': (metrics_df['recall'] * metrics_df['weight']).sum() / total_weight,
                'f1_score': (metrics_df['f1_score'] * metrics_df['weight']).sum() / total_weight,
                'TP': metrics_df['TP'].sum(),
                'FP': metrics_df['FP'].sum(),
                'FN': metrics_df['FN'].sum(),
                'TN': metrics_df['TN'].sum(),
                'weight': total_weight
            }
            metrics_df = pd.concat([metrics_df, pd.DataFrame([weighted_avg])], ignore_index=True)

        return metrics_df
    
    def product_calculation(self, merged_df: pd.DataFrame) -> pd.DataFrame:
        """
        Calculate confusion matrix and metrics summary for reasons.
        Parameters:
            merged_df (pd.DataFrame): The merged DataFrame containing ground truth and predictions.
        Returns:
            pd.DataFrame: The metrics summary DataFrame for reasons.
        """
        target_classes = [
            'postpaid', 'tol', 'tvs', 'unknown'
        ]
        
        # Filter out records where ground truth reasons set is empty
        # merged_df = merged_df[merged_df['gt_reasons_set'].apply(lambda x: len(x) > 0)].copy()
        merged_df['product_gt'] = merged_df['product_gt'].apply(lambda x: x if isinstance(x, set) else set())
        merged_df['product_pred'] = merged_df['product_pred'].apply(lambda x: x if isinstance(x, set) else set())
        # Initialize detailed DataFrame
        detailed_df = pd.DataFrame()
        detailed_df['call_id'] = merged_df['call_id']
        # detailed_df['Actual'] = merged_df['product_gt'].apply(lambda x: ' , '.join(sorted(x)))
        # detailed_df['Predict'] = merged_df['product_pred'].apply(lambda x: ' , '.join(sorted(x)))

        metrics_data = []
        
        for i, cls in enumerate(target_classes, 1):
            cls_lower = cls.lower()

            is_in_gt = merged_df['product_gt'].apply(lambda x: cls_lower in x)
            is_in_pred = merged_df['product_pred'].apply(lambda x: cls_lower in x)

            tp_col = (is_in_gt & is_in_pred).astype(int)
            fp_col = (~is_in_gt & is_in_pred).astype(int)
            fn_col = (is_in_gt & ~is_in_pred).astype(int)
            tn_col = (~is_in_gt & ~is_in_pred).astype(int)
            
            fp_ids = merged_df.loc[(~is_in_gt & is_in_pred), 'call_id']
            fn_ids = merged_df.loc[(is_in_gt & ~is_in_pred), 'call_id']
            
            # Add columns to detailed_df
            detailed_df[f'TP {i}'] = tp_col
            detailed_df[f'FP {i}'] = fp_col
            detailed_df[f'FN {i}'] = fn_col
            detailed_df[f'TN {i}'] = tn_col
            
            # Calculate aggregate metrics
            tp = tp_col.sum()
            fp = fp_col.sum()
            fn = fn_col.sum()
            tn = tn_col.sum()
            total = tp + fp + fn + tn
            
            weight = tp + fn
            
            accuracy = (tp + tn) / total if total > 0 else 0
            precision = tp / (tp + fp) if (tp + fp) > 0 else 0
            recall = tp / (tp + fn) if (tp + fn) > 0 else 0
            f1 = 2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else 0
            
            metrics_data.append({
                'id': i,
                'product': cls,
                'accuracy': accuracy,
                'precision': precision,
                'recall': recall,
                'f1_score': f1,
                'TP': tp,
                'FP': fp,
                'FN': fn,
                'TN': tn,
                'weight': weight
            })
            
        metrics_df = pd.DataFrame(metrics_data)
        
        # Calculate Weighted Average
        total_weight = metrics_df['weight'].sum()
        if total_weight > 0:
            weighted_avg = {
                'id': 'weighted_avg',
                'product': 'weighted_avg',
                'accuracy': (metrics_df['accuracy'] * metrics_df['weight']).sum() / total_weight,
                'precision': (metrics_df['precision'] * metrics_df['weight']).sum() / total_weight,
                'recall': (metrics_df['recall'] * metrics_df['weight']).sum() / total_weight,
                'f1_score': (metrics_df['f1_score'] * metrics_df['weight']).sum() / total_weight,
                'TP': metrics_df['TP'].sum(),
                'FP': metrics_df['FP'].sum(),
                'FN': metrics_df['FN'].sum(),
                'TN': metrics_df['TN'].sum(),
                'weight': total_weight
            }
            metrics_df = pd.concat([metrics_df, pd.DataFrame([weighted_avg])], ignore_index=True)

        return metrics_df

    def calculate_confusion_matrix(self, prediction_df: pd.DataFrame, ground_truth_df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """
        Calculate confusion matrix and metrics summary for call_result and reasons.
        Parameters:
            prediction_df (pd.DataFrame): The predictions DataFrame.
            ground_truth_df (pd.DataFrame): The ground truth DataFrame.
        Returns:
            Tuple[pd.DataFrame, pd.DataFrame]: The metrics summary DataFrames for call_result and reasons.
        """
        try:
            logger.info("Starting calculate_confusion_matrix...")

            prediction_df, ground_truth_df = self.pre_process(prediction_df, ground_truth_df)

            # Merge on call_id to align rows
            merged_df = pd.merge(
                ground_truth_df, 
                prediction_df, 
                on=['call_id', 'phone_number','product'], 
                how='outer', 
                suffixes=('_gt', '_pred')
            )
            
            ground_truth_df_grouped = ground_truth_df.groupby(['call_id', 'phone_number'])['product'].apply(set).reset_index()
            prediction_df_grouped = prediction_df.groupby(['call_id', 'phone_number'])['product'].apply(set).reset_index()
            
            # merged_df['product_gt'] = merged_df['product_gt'].apply(lambda x: x if isinstance(x, set) else set())
            # merged_df['product_pred'] = merged_df['product_pred'].apply(lambda x: x if isinstance(x, set) else set())
            
            merged2_df = pd.merge(
                ground_truth_df_grouped, 
                prediction_df_grouped, 
                on=['call_id', 'phone_number'], 
                how='outer', 
                suffixes=('_gt', '_pred')
            )

            # Create confusion matrix for 'call_result'
            call_result_metrics = self.call_result_calculation(merged_df)        
            # Create confusion matrix for 'reason' (all)
            reason_metrics = self.reason_calculation(merged_df)
            # Create confusion matrix for 'product'
            product_metrics = self.product_calculation(merged2_df)
            
            logger.info("Confusion matrix calculation completed.")
            return call_result_metrics, reason_metrics, product_metrics
        except Exception as e:
            logger.error(f"Error in calculate_confusion_matrix: {e}")
            raise Exception(f"Error in calculate_confusion_matrix: {e}")

    def prepare_output_confusion(self, call_result_metrics: pd.DataFrame, reason_metrics: pd.DataFrame, product_metrics: pd.DataFrame) -> None:
        """
        Prepare the final confusion report DataFrame.
        Parameters:
            call_result_metrics (pd.DataFrame): The metrics summary DataFrame for call_result.
            reason_metrics (pd.DataFrame): The metrics summary DataFrame for reasons.
        Returns:
            pd.DataFrame: The final confusion report DataFrame.
        """
        try:
            logger.info("Starting prepare_output_confusion...")
            def evaluation_status_check(confusion_key: str, value: str|float) -> Dict[str, str]:
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
            
            def format_confusion_dict(df: pd.DataFrame, dimension: str) -> list:
                confusion_list = []
                for _, row in df.iterrows():
                    confusion_obj = {   
                        "dimension": dimension,
                        "details":  {
                            "label": row[dimension],
                            "metrics": {
                                "accuracy": percentage_format(row['accuracy']),
                                "precision": percentage_format(row['precision']),
                                "recall": percentage_format(row['recall']),
                                "f1_score": percentage_format(row['f1_score']),
                            },
                            "evaluation_status": {
                                "accuracy": evaluation_status_check('accuracy', percentage_format(row['accuracy'])),
                                "precision": evaluation_status_check('precision', percentage_format(row['precision'])),
                                "recall": evaluation_status_check('recall', percentage_format(row['recall'])),
                                "f1_score": evaluation_status_check('f1_score', percentage_format(row['f1_score'])),
                            },
                            "confusion": {
                                "TP": int(row['TP']),
                                "FP": int(row['FP']),
                                "FN": int(row['FN']),
                                "TN": int(row['TN']),
                                "weight": int(row['weight'])
                            }
                        }
                    }
                    confusion_list.append(confusion_obj)
                return confusion_list
            
            confusion_obj = format_confusion_dict(call_result_metrics, 'call_result') + format_confusion_dict(reason_metrics, 'reason') + format_confusion_dict(product_metrics, 'product')
            rows = []
            for entry in confusion_obj:
                # Extract nested dictionaries
                details = entry.get('details', {})
                metrics = details.get('metrics', {})
                status = details.get('evaluation_status', {})
                confusion = details.get('confusion', {})
                
                # Map to flat schema
                row = {
                    'dimension': entry.get('dimension'),
                    'label': details.get('label'),
                    'accuracy': metrics.get('accuracy'),
                    'accuracy_status': status.get('accuracy'),
                    'precision': metrics.get('precision'),
                    'precision_status': status.get('precision'),
                    'recall': metrics.get('recall'),
                    'recall_status': status.get('recall'),
                    'f1_score': metrics.get('f1_score'),
                    'f1_score_status': status.get('f1_score'),
                    'TP': confusion.get('TP'),
                    'FP': confusion.get('FP'),
                    'FN': confusion.get('FN'),
                    'TN': confusion.get('TN'),
                    'weight': confusion.get('weight')
                }
                rows.append(row)
            final_confusion_df = pd.DataFrame(rows)
            logger.info(f"Output confusion report prepared. Rows: {len(final_confusion_df)}")
            return final_confusion_df
            
        except Exception as e:
            logger.error(f"Error in prepare_output_log: {e}")
            raise Exception(f"Error in prepare_output_log: {e}")
    
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
        
    def check_gemini_batch_output(self, process_date: datetime|str) -> Dict[str, bool]:
        """
        Check for the existence of Gemini batch output files for the given date and the previous day.
        Parameters:
            process_date (datetime|str): The date to check for Gemini batch output files.
        Returns:
            Dict[str, bool]: A dictionary with date strings as keys and boolean values indicating file existence
        """
        try:
            if isinstance(process_date, str):
                process_date = datetime.strptime(process_date, "%Y-%m-%d %H:%M:%S").date()
            logger.info(f"Checking Gemini batch output for date: {process_date}")
            root = "Prediction.gemini_batch.output"
            batch_path = get_value_by_path(self.configs, f"{root}.path")
            look_back_1d = process_date - timedelta(days=1)
            check_list = [process_date.strftime("%Y%m%d"), look_back_1d.strftime("%Y%m%d")]
            check_result = {}
            for date_str in check_list:
                check_bool = False
                check_batch_path = resolve_date(batch_path[1:], date_str) if batch_path.startswith("/") else resolve_date(batch_path, date_str)
                
                files = self.module_objects[root].list_files(
                    prefix=check_batch_path
                )
                for file in files:
                    # Only check for final prediction files, exclude incremental predictions
                    if file.endswith(".jsonl") and "incremental_predictions" not in file:
                        logger.info(f"Found Gemini batch output file for date {date_str}: {file}")
                        check_bool = True
                        check_result[date_str] = check_bool
                        break
                    else:
                        check_result[date_str] = check_bool
                if not files:
                    check_result[date_str] = check_bool
            logger.info(f"Check result: {check_result}")
            return check_result
        except Exception as e:
            logger.error(f"Error in check_gemini_batch_output: {e}")
            raise Exception(f"Error in check_gemini_batch_output: {e}")

    def insert_log(self, batch_result: dict[str, Any]) -> None:
        """
        Insert audit log for the given batch result.
        Parameters:
            batch_result (dict[str, Any]): The batch result data to be logged.
        Returns:
            None
        """
        logger.info("Starting insert_log for audit logging...")
        logger.info(f"Processing {len(batch_result)} batch result records")
        audit_log_module = AuditLogModule()
        sharepoint_module = SharePointModule(
            client_id=os.environ.get("CONTROL_SITE_CLIENT_ID"),
            client_secret=os.environ.get("CONTROL_SITE_CLIENT_SECRET"),
            tenant_id=os.environ.get("CONTROL_SITE_TENANT_ID"),
            site_domain=os.environ.get("CONTROL_SITE_SITE_DOMAIN"),
            site_path=os.environ.get("CONTROL_SITE_SITE_PATH")
        )
        try:
            cost_file_path = f"{os.environ.get('CONTROL_SITE_BASE_ROOT').split('/')[0]}/{os.environ.get('CONTROL_SITE_COST_PATH')}/gemini-cost.yaml"
            cost_file_path = re.sub(r'/+', '/', cost_file_path)
            cost_res = sharepoint_module.get_item_by_path(cost_file_path)
            cost_config = load_yaml_string(cost_res.content.decode("utf-8").strip())
            model_config_path = get_value_by_path(self.configs, "Prediction.gemini_batch.model_config.input.path")
            if model_config_path.startswith("/"):
                model_config_path = model_config_path[1:]
            model_config = load_yaml(model_config_path)
            batch_cost = cost_config['batch'][model_config.get('model')]['pricing']

            text_input_item = next((item for item in batch_cost['input'] if item['type'] == 'text'), None)
            audio_input_item = next((item for item in batch_cost['input'] if item['type'] == 'audio'), None)
            audio_output_item = next((item for item in batch_cost['output'] if item['type'] == 'all'), None)
                
            text_input_price_usd = text_input_item['cost'] / text_input_item['tokens']
            audio_input_price_usd = audio_input_item['cost'] / audio_input_item['tokens']
            audio_output_price_usd = audio_output_item['cost'] / audio_output_item['tokens']
            logger.info("Cost configuration loaded successfully from SharePoint")
        except Exception as e:
            logger.warning(f"Failed to load cost configuration: {e}")
            logger.warning("Using default pricing")
            text_input_price_usd = 0.15 / 1_000_000
            audio_input_price_usd = 0.5 / 1_000_000
            audio_output_price_usd = 1.25 / 1_000_000
        
        data_date_pattern = r'\/\d{8}\/'
        payload = []
        logger.info("Building transaction payload from batch results...")
        for record in batch_result:
            file_url_list = recursive_dict_value_by_key(record, "fileUri")
            file_url = file_url_list[0] if file_url_list else None
            file_name = os.path.basename(file_url) if file_url else "Unknown"
            source_files_config = get_value_by_path(self.configs, "Prediction.gemini_batch.input", {})
            source_files_url = f"https://{os.environ.get('VERINT_SITE_SITE_DOMAIN')}/sites/{source_files_config.get('site_name')}/{source_files_config.get('path')}"
            source_files_url = re.sub(r'(?<!:)/+', '/', source_files_url)

            # Initialize tokens for each record
            audio_input_token = 0
            text_input_token = 0
            text_output_token = 0

            # Accumulate token counts (use += in case multiple details exist)
            for token_detail in get_value_by_path(record, "response.usageMetadata.promptTokensDetails", []):
                if token_detail.get("modality") == "AUDIO":
                    audio_input_token += token_detail.get("tokenCount", 0)
                elif token_detail.get("modality") == "TEXT":
                    text_input_token += token_detail.get("tokenCount", 0)

            for token_detail in get_value_by_path(record, "response.usageMetadata.candidatesTokensDetails", []):
                if token_detail.get("modality") == "TEXT":
                    text_output_token += token_detail.get("tokenCount", 0)

            input_token = text_input_token + audio_input_token
            output_token = text_output_token

            audio_price = (
                text_input_token * text_input_price_usd
                + audio_input_token * audio_input_price_usd
                + text_output_token * audio_output_price_usd
            )
            
            # Extract data_date from file_url
            data_date = ""
            if file_url:
                match = re.search(data_date_pattern, file_url)
                if match:
                    data_date = match.group(0).strip('/')
            
            # Skip record if data_date cannot be determined
            if not data_date:
                logger.warning(f"Cannot determine data_date for file: {file_name}. Skipping transaction log entry.")
                continue
                
            try:
                # Validate record structure
                # OutputValidation.model_validate_json(get_value_by_path(record, "response.candidates.0.content.parts.0.text", {}))
                
                payload.append(
                    TransactionPayload(
                        data_date = data_date,
                        start_time = get_value_by_path(record, "processed_time", None),
                        end_time = get_value_by_path(record, "response.createTime", None),
                        type = "AI Fact-Checker",
                        gcp_project_id = self.configs.get("Project_id"),
                        gcp_project_name = self.configs.get("Project_name"),
                        user_id = "daisyrpa",
                        source = source_files_config.get("type", "Unknown").capitalize(),
                        storage_path = source_files_url,
                        folder = "",
                        filename = file_name,
                        file_metadata_sec = int(get_value_by_path(record, "response.usageMetadata.billablePromptUsage.audioDurationSeconds", 0)),
                        status_pass_failed_retry = "Pass",
                        error_log_if = None,
                        token_usage_input = input_token,
                        token_usage_output = output_token,
                        total_cost_usd = audio_price
                    )
                )
            except Exception as e:
                logger.error(f"Error append payload: {e}")
                payload.append(
                    TransactionPayload(
                        data_date = data_date,
                        start_time = get_value_by_path(record, "processed_time", None),
                        end_time = get_value_by_path(record, "response.createTime", None),
                        type = "AI Fact-Checker",
                        gcp_project_id = self.configs.get("Project_id"),
                        gcp_project_name = self.configs.get("Project_name"),
                        user_id = "daisyrpa",
                        source = source_files_config.get("type", "Unknown").capitalize(),
                        storage_path = source_files_url,
                        folder = "",
                        filename = file_name,
                        file_metadata_sec = int(get_value_by_path(record, "response.usageMetadata.billablePromptUsage.audioDurationSeconds", 0)),
                        status_pass_failed_retry = "Failed",
                        error_log_if = str(e).splitlines()[0],
                        token_usage_input = input_token,
                        token_usage_output = output_token,
                        total_cost_usd = audio_price
                    )
                )
        logger.info(f"Transaction payload prepared with {len(payload)} records")
        
        if not payload:
            logger.warning("No valid transaction records to log. Skipping transaction log creation.")
            return
            
        df = audit_log_module.log_transaction(log_data=payload)
        logger.info(f"Transaction log created successfully")
        
        # Filter out empty data_date values
        data_dates = [d for d in df['data_date'].unique().tolist() if d and str(d).strip()]
        logger.info(f"Processing transaction logs for {len(data_dates)} unique dates: {data_dates}")
        for data_date in data_dates:
            existing_path = f"{os.environ.get('CONTROL_SITE_BASE_ROOT')}/{os.environ.get('CONTROL_SITE_TRANSACTION_LOG_PATH')}/{data_date[:4]}"
            log_name = f"transaction_log_{data_date[0:6]}.csv"
            transaction_path = f"{existing_path}/{log_name}"
            logger.info(f"Processing transaction log for date {data_date}: {transaction_path}")
            try:
                existing_log = sharepoint_module.get_item_by_path(transaction_path)
                existing_df = pd.read_csv(io.BytesIO(existing_log.content))
                logger.info(f"Found existing transaction log with {len(existing_df)} records")
                combined_df = pd.concat([existing_df, df[df['data_date'] == data_date]], ignore_index=True)
                logger.info(f"Merged with new records, total: {len(combined_df)}")
            except Exception as e:
                logger.warning(f"Cannot update transaction log: {e} at {data_date}")
                logger.info(f"Creating new transaction log with {len(df[df['data_date'] == data_date])} records")
                combined_df = df[df['data_date'] == data_date]
            finally:
                combined_df['data_date'] = combined_df['data_date'].astype(str)
                combined_df = combined_df.fillna("").sort_values(by=['data_date', 'start_time', 'end_time'], ascending=[True, True, True])
                csv_buffer = io.BytesIO()
                combined_df.to_csv(csv_buffer, index=False, encoding="utf-8-sig")
                csv_buffer.seek(0)
                sharepoint_module.upload_file(
                    upload_path=transaction_path,
                    content=csv_buffer.read(),
                )
                logger.info(f"Successfully uploaded transaction log to {transaction_path}")
        logger.info("Transaction log uploaded/updated successfully.")

    def execute(self):
        """
        Main execution method for the FactChecker module.
        It checks for Gemini batch output, prepares predictions and ground truth,
        calculates confusion matrices, prepares the final report, and uploads it to SharePoint.
        If no Gemini batch output is found, it initiates a new Gemini batch process.
        Parameters:
            None
        Returns:
            None
        """
        logger.info("Starting FactChecker execution cycle...")
        execution_datetime = datetime.now(tz=self.timezone)
        check_list = self.check_gemini_batch_output(
            process_date=execution_datetime
        )
        prediction_output = False
        latest_date_str = execution_datetime.strftime("%Y%m%d")
        if any(check_list.values()):
            latest_date_str = max([date for date, available in check_list.items() if available])
            prediction_output = check_list[latest_date_str]            

        if prediction_output:
            current_datetime = execution_datetime.strftime("%Y-%m-%d %H:%M:%S")
            raw_jsonl = self.retrieve_gemini_batch_output(batch_process_date=latest_date_str)
            prediction_df = self.prepare_predictions(
                batch_process_date=latest_date_str, 
                content=raw_jsonl
            )
            if prediction_df.empty:
                logger.info("No valid predictions found in Gemini batch output. Skipping report generation.")
                return
            ground_truth_df = self.prepare_ground_truth()
            if ground_truth_df.empty:
                logger.info("No ground truth data available. Skipping report generation.")
                return
            call_result_metrics, reason_metrics, product_metrics = self.calculate_confusion_matrix(
                prediction_df=prediction_df, 
                ground_truth_df=ground_truth_df
            )
            confusion_df = self.prepare_output_confusion(
                call_result_metrics=call_result_metrics,
                reason_metrics=reason_metrics,
                product_metrics=product_metrics
            )
            final_df = confusion_df.copy()
            final_df['created_datetime'] = current_datetime
            final_df['processed_datetime'] = prediction_df['batch_processed_time'].values[0] if not prediction_df.empty else None
            final_df['model_version'] = prediction_df['model_version'].values[0] if not prediction_df.empty else None
            final_df['system_prompt'] = prediction_df['system_prompt'].values[0] if not prediction_df.empty else None
            final_df['gcp_project_id'] = self.configs.get("Project_id")
            final_df['gcp_project_name'] = self.configs.get("Project_name")
            final_df['ground_truth_count'] = len(ground_truth_df)
            final_df['prediction_count'] = len(prediction_df)
            final_df = final_df[get_value_by_path(self.configs, "Report.schema")]
            self.merge_upload_report(final_df=final_df)
            logger.info("FactChecker execution cycle completed successfully.")
            # Insert audit log
            self.insert_log(batch_result=raw_jsonl)
        else:
            logger.info("No Gemini batch output found for the current or previous day. Skipping report generation.")
            logger.info("Initiating upload and execution of new Gemini batch process.")
            current_datetime = execution_datetime.strftime("%Y-%m-%d %H:%M:%S")
            self.upload_voice_to_gcs(
                process_date=current_datetime
            )
            self.execute_gemini_batch(
                process_date=current_datetime
            )

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fact Checker Module Execution")
    parser.add_argument("--config_path", help="Path to the configuration file", required=True)
    args = parser.parse_args()
    
    fact_checker = FactCheckerModule(config_path=args.config_path)
    fact_checker.execute()