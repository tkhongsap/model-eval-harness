import asyncio
import polars as pl
from dataclasses import dataclass
from typing import Any
import io
from app.core.models import PipelineConfig, PulseResult, TokenUsage
from app.services.gemini_service import GeminiService
from app.share_log import get_logger
from app.utils.common import get_value_by_path
from datetime import datetime
from app.services.sharepoint_service import SharePointService

logger = get_logger(__name__)


class PulseProcessor:

    def __init__(
        self,
        config: PipelineConfig,
        gemini: GeminiService,
    ) -> None:
        self._cfg = config
        self._gemini = gemini
        self._pulse_results: list[PulseResult] = []

    async def run(self, dataframe: pl.DataFrame, system_prompt: str) -> None:
        all_records = dataframe.to_dicts()  # list[dict[str, Any]]
        total_rows = len(all_records)
        all_results = []

        for i in range(0, total_rows, self._cfg.batch_size):
            batch = all_records[i : i + self._cfg.batch_size]
            logger.info(
                f"Batch {i // self._cfg.batch_size + 1}: submitting {len(batch)} records"
            )
            
            results: tuple[list[dict[str, Any]], TokenUsage] = await asyncio.gather(
                *[
                    self._send_to_gemini(
                        i + index, system_prompt, args=r
                    )
                    for index, r in enumerate(batch)
                ]
            )

            for pair in results:
                if pair is not None: # check None
                    response_dict, usage = pair
                    all_results.append(response_dict)

        for response in all_results:
            for item in response:
                result = PulseResult.from_dict(item)
                self._pulse_results.append(result)

    async def _send_to_gemini(
        self, index: int, system_prompt: str, args: Any
    ) -> tuple[dict, TokenUsage] | None:
        try:
            # Assumes validate_text returns a tuple of (dict, TokenUsage)
            return await self._gemini.validate_text(args, system_prompt)
        except Exception as e:
            logger.error(f"Gemini call failed for '{index}' with error {e}")
            return None

    def export_to_sharepoint(self, today: datetime, pulse_sp: SharePointService, resolve_config: dict, output_schema: list[str]):
        output_file_path = (
            get_value_by_path(
                resolve_config, "Output.sharepoint.output_file_path"
            )
            .replace("yyyymmdd", today.strftime("%Y%m%d"))
            .replace("yyyymm", today.strftime("%Y%m"))
            .strip("/")
        )

        data = [
            [
                # header
                pulse_result.emp_id,
                pulse_result.comment_id,
                pulse_result.comment,
                pulse_result.intent,
                # sentiment classification
                pulse_result.cluster,
                pulse_result.category,
                pulse_result.sub_category,
                pulse_result.sentiment,
                pulse_result.confidence,
                pulse_result.remark,
                # error log
                pulse_result.error,
            ]
            for pulse_result in self._pulse_results
        ]

        # print("=" * 50)
        # if data:
        #     print(f"Sample Row (Length {len(data[0])}): {data[0]}")
        # print("=" * 50)

        # Build clean Polars DataFrame with the configured schema directly
        export_df = pl.DataFrame(data, schema=output_schema)

        # --- EXPORT TO SHAREPOINT LOGIC ---
        try:
            logger.info(f"Writing DataFrame to memory buffer for: {output_file_path}")
            
            file_buffer = io.BytesIO()
            
            # Determine format based on your file path extension
            if output_file_path.lower().endswith(".xlsx"):
                export_df.write_excel(file_buffer)
            elif output_file_path.lower().endswith(".csv"):
                export_df.write_csv(file_buffer)
            else:
                logger.warning(f"Unrecognized file extension. Defaulting buffer output to CSV format.")
                export_df.write_csv(file_buffer)
                
            file_bytes = file_buffer.getvalue()
            # with open("Pulse_sentiment_report.xlsx", "wb") as fb:
            #     fb.write(file_bytes)
            
            # 4. Invoke your SharePoint upload function using the full path and raw bytes
            # Double-check if your instance is self._sharepoint or self._sharepoint_service
            pulse_sp.upload_file(
                upload_path=f"{pulse_sp.base_root}/{output_file_path}",
                content=file_bytes
            )
            
            logger.info("SharePoint pipeline export complete.")

        except Exception as e:
            logger.error(f"Failed to process and upload data to SharePoint destination. Error: {e}")
            raise e