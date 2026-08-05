"""RTR Fraud Validation — pipeline entry point.

This module is the Docker / Cloud Run entry point (``python -m app.main``).
Its only responsibility is to wire together the services and run the pipeline.
All business logic lives in ``app/pipeline/fraud_pipeline.py``.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime

import google.auth
from google import genai

from app.core.models import PipelineConfig
from app.pipeline.fraud_pipeline import FraudValidationPipeline
from app.pipeline.pulse_pipeline import PulsePipeline

from app.processors.email_composer import EmailComposer
from app.processors.image_processor import ImageProcessor
from app.processors.report_builder import ReportBuilder
from app.processors.shop_processor import ShopProcessor
from app.processors.pulse_processor import PulseProcessor

from app.services.email_service import EmailService
from app.services.gcs_service import GCSService
from app.services.gemini_service import GeminiService
from app.services.s3_service import S3Service
from app.services.secret_service import SecretService
from app.services.sharepoint_service import SharePointService

from dotenv import load_dotenv
from app.share_log import get_logger
from app.utils.common import get_value_by_path, load_yaml_string, read_file, resolve_env

logger = get_logger(__name__)

load_dotenv(override=True)

async def main() -> None:
    secrets = SecretService()

    _, project_id = google.auth.default()

    # --- Config ---
    resolve_config = load_yaml_string(resolve_env(read_file("config/app/rtr_main.yaml")))

    config = PipelineConfig(
        batch_size=int(secrets.get("BATCH_SIZE")),
        s3_bucket=secrets.get("S3_BUCKET_NAME"),
        gcs_bucket=secrets.get("GCS_BUCKET_NAME"),
        today=datetime.now(),
        # today=datetime(year=2026, month=7, day=1),
        project_id=project_id,
        project_name=secrets.get("PROJECT_NAME"),
        # Fraud site
        fraud_site_base_root=secrets.get("FRAUD_SITE_BASE_ROOT"),
        input_folder=secrets.get("INPUT_FOLDER"),
        output_folder=secrets.get("OUTPUT_FOLDER"),
        backup_folder=secrets.get("BACKUP_FOLDER"),
        archive_folder=secrets.get("ARCHIVE_FOLDER"),
        # Control site
        control_site_base_root=secrets.get("CONTROL_SITE_BASE_ROOT"),
        control_site_prompts_root=secrets.get("CONTROL_SITE_PROMPTS_ROOT"),
        control_site_transaction_log_path=secrets.get("CONTROL_SITE_TRANSACTION_LOG_PATH"),
        control_site_performance_log_path=secrets.get("CONTROL_SITE_PERFORMANCE_LOG_PATH"),
        control_site_cost_path=secrets.get_optional("CONTROL_SITE_COST_PATH", ""),
        # Email
        recipient_emails=[
            e.strip()
            for e in secrets.get("RECIPIENT_EMAIL").split(",")
            if e.strip()
        ],
        team_emails=[
            e.strip()
            for e in secrets.get("TEAM_EMAIL").split(",")
            if e.strip()
        ],
        cc_emails=[
            e.strip()
            for e in secrets.get("CC_EMAIL").split(",")
            if e.strip()
        ],
        #decrypt key
        rsa_private_key=secrets.get("RSA_PRIVATE_KEY"),
    )

    # --- Services ---
    fraud_sp = SharePointService(
        client_id=secrets.get("FRAUD_SITE_CLIENT_ID"),
        client_secret=secrets.get("FRAUD_SITE_CLIENT_SECRET"),
        tenant_id=secrets.get("FRAUD_SITE_TENANT_ID"),
        site_domain=secrets.get("FRAUD_SITE_SITE_DOMAIN"),
        site_path=secrets.get("FRAUD_SITE_SITE_PATH"),
        base_root=secrets.get("FRAUD_SITE_BASE_ROOT"),
    )
    # pulse_sp = SharePointService(
    #     client_id=secrets.get("PULSE_SITE_CLIENT_ID"),
    #     client_secret=secrets.get("PULSE_SITE_CLIENT_SECRET"),
    #     tenant_id=secrets.get("PULSE_SITE_TENANT_ID"),
    #     site_domain=secrets.get("PULSE_SITE_SITE_DOMAIN"),
    #     site_path=secrets.get("PULSE_SITE_SITE_PATH"),
    #     base_root=secrets.get("PULSE_SITE_BASE_ROOT"),
    # ) 
    control_sp = SharePointService(
        client_id=secrets.get("CONTROL_SITE_CLIENT_ID"),
        client_secret=secrets.get("CONTROL_SITE_CLIENT_SECRET"),
        tenant_id=secrets.get("CONTROL_SITE_TENANT_ID"),
        site_domain=secrets.get("CONTROL_SITE_SITE_DOMAIN"),
        site_path=secrets.get("CONTROL_SITE_SITE_PATH"),
        base_root=secrets.get("CONTROL_SITE_BASE_ROOT"),
    )
    s3 = S3Service(
        aws_access_key=secrets.get("S3_AWS_ACCESS_KEY"),
        aws_secret_key=secrets.get("S3_AWS_SECRET_KEY"),
        bucket_name=secrets.get("S3_BUCKET_NAME"),
    )
    gcs = GCSService(project_id=project_id, bucket_name=secrets.get("GCS_BUCKET_NAME"))
    email_svc = EmailService(
        tenant_id=secrets.get("FRAUD_SITE_TENANT_ID"),
        client_id=secrets.get("FRAUD_SITE_CLIENT_ID"),
        client_secret=secrets.get("FRAUD_SITE_CLIENT_SECRET"),
        sender_email=secrets.get("SENDER_EMAIL"),
    )

    # --- AI client ---
    asia_gemini_client = genai.Client(
        vertexai=True,
        project=project_id,
        location="asia-southeast1",
    )

    global_gemini_client = genai.Client(
        vertexai=True,
        project=project_id,
        location="global",
    )

    # --- Processors ---
    image_proc = ImageProcessor()
    gemini_svc = GeminiService(asia_gemini_client)
    shop_proc = ShopProcessor(s3=s3, gemini=gemini_svc, image_processor=image_proc)
    pulse_proc = PulseProcessor(config=config, gemini=gemini_svc)
    schema1: list[str] = get_value_by_path(resolve_config, "Output.sharepoint.output_user_schema_sheet1")
    schema2: list[str] = get_value_by_path(resolve_config, "Output.sharepoint.output_user_schema_sheet2")
    report = ReportBuilder(schema1, schema2)
    composer = EmailComposer.from_image_dir("app/image")

    # --- Pipeline ---
    fraud_pipeline = FraudValidationPipeline(
        config=config,
        fraud_sp=fraud_sp,
        control_sp=control_sp,
        s3=s3,
        gcs=gcs,
        shop_processor=shop_proc,
        report_builder=report,
        email_composer=composer,
        email_service=email_svc,
    )

    await fraud_pipeline.run()

    # --- pulse_pipeline ---
    # pulse_pipeline = PulsePipeline(
    #     config=config,
    #     pulse_sp=pulse_sp,
    #     control_sp=control_sp,
    #     s3=s3,
    #     gcs=gcs,
    #     pulse_processor=pulse_proc,
    #     report_builder=report,
    #     email_composer=composer,
    #     email_service=email_svc,
    # )

    # await pulse_pipeline.run()


if __name__ == "__main__":
    asyncio.run(main())
