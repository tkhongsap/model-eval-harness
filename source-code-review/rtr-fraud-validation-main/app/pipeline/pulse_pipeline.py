from __future__ import annotations

import asyncio
import base64
from datetime import datetime
import io
import math

import chardet
from openai.types import Batch
import pandas as pd
import polars as pl
import requests

from app.core.models import PipelineConfig, ShopRecord, ShopResult
from app.processors.email_composer import EmailComposer
from app.processors.report_builder import ReportBuilder
from app.processors.pulse_processor import PulseProcessor
from app.services.email_service import EmailService
from app.services.gcs_service import GCSService
from app.services.s3_service import S3Service
from app.services.secret_service import SecretService
from app.services.sharepoint_service import SharePointService
from app.share_log import get_logger
from app.utils.common import get_value_by_path, load_yaml_string, read_file, resolve_env

logger = get_logger(__name__)

# Number of appended schema columns (must match config/app/rtr_main.yaml)
_APPENDED_SCHEMA_LEN = 22


class PulsePipeline:
    """Pulse-validation pipeline doing the sentiment of text string"""

    def __init__(
        self,
        config: PipelineConfig,
        pulse_sp: SharePointService,
        control_sp: SharePointService,
        s3: S3Service,
        gcs: GCSService,
        pulse_processor: PulseProcessor,
        report_builder: ReportBuilder,
        email_composer: EmailComposer,
        email_service: EmailService,
    ) -> None:
        self._cfg = config
        self._pulse_sp = pulse_sp
        self._control_sp = control_sp
        self._s3 = s3
        self._gcs = gcs
        self._pulse_proc = pulse_processor
        self._report = report_builder
        self._composer = email_composer
        self._email = email_service

        # Runtime state
        self._input_sharepoint_path: str = ""
        self._output_sharepoint_path: str = ""

    async def run(self) -> None:
        """Execute the complete pipeline end-to-end."""
        try:
            await self._run_pipeline()
        except Exception as exc:
            logger.error({"status": "Error", "message": str(exc)})
            raise
        finally:
            print("================== Pulse Pipeline Run Sucessfully ====================")
    
    async def _run_pipeline(self) -> None:
        resolve_config = load_yaml_string(resolve_env(read_file("config/app/pulse_main.yaml")))
        input_schema: list[str] = get_value_by_path(resolve_config, "Input.sharepoint.input_schema")
        output_schema: list[str] = get_value_by_path(resolve_config, "Output.sharepoint.output_schema")
        
        system_prompt_config = load_yaml_string(resolve_env(read_file("config/system_prompt/pulse.yaml")))
        system_prompt: str = get_value_by_path(system_prompt_config, "system_prompt")
        # read file form sharepoint
        df = self._ingest(
            resolve_config,
            input_schema,
        )

        # send data to gemini, pararelly
        await self._pulse_proc.run(df, system_prompt)
        

        # export output in csv or xlsx format for AI agent at sharepoint(processor)
        self._pulse_proc.export_to_sharepoint(self._cfg.today, self._pulse_sp, resolve_config, output_schema)


    def _ingest(
        self, 
        resolve_config: dict, 
        input_schema: list[str], 
    ) -> pl.DataFrame:
        """Download input & lookup from SharePoint to GCS, read input CSV."""
        today = self._cfg.today
        input_file_path = (
            get_value_by_path(resolve_config, "Input.sharepoint.input_file_path")
            .replace("yyyymmdd", today.strftime("%Y%m%d"))
            .replace("yyyymm", today.strftime("%Y%m"))
            .strip("/")  # guard against accidental trailing slash
        )
        input_file_name = input_file_path.split("/")[-1]        # → "pulse.xlsx"
        input_folder_path = "/".join(input_file_path.split("/")[:-1])  # → "input/202606/20260604"

        content_bytes = self._pulse_sp.read_bytes(
            input_schema,
            input_folder_path,
            input_file_name
        )

        file_stream = io.BytesIO(content_bytes)
        df = pl.read_excel(file_stream)
        df.columns = input_schema

        return df