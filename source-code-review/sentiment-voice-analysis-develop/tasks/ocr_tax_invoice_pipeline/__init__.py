"""Generic, config-driven document-OCR batch pipeline.

Pipeline shape: ingest (SharePoint → GCS landing) → IQS quality gate → Gemini batch
submit → retrieve/validate → finalize. Three registered tasks chain via the engine's
``pre_result`` threading:

    OCRSubmitTask        → submits the batch; files land PENDING/PARTIAL in the log
    OCRRetrieveTask      → collects predictions into an ``OCRResult``; stamps NO status
    <business task(s)>   → consume ``OCRResult.final_df`` and return the result unchanged
    OCRFinalizeTask      → ALWAYS last; stamps terminal SUCCESS/FAILED only after business
                           logic succeeds (a business exception leaves files in-flight, so
                           the next run re-collects from GCS at zero extra Gemini cost).

Reuse: a new domain adopts the pipeline with YAML only — its own ``domain:`` key,
SharePoint site credentials, bucket env-vars, and log paths, naming the same three tasks.
**Zero code changes.** Nothing in this package may hard-code a domain, bucket, or site name
(those live in YAML + env vars); only the default prompt/schema file contents are domain-aware.

Task classes are registered as an import side effect via ``tasks/__init__.py``.
"""

from tasks.ocr_tax_invoice_pipeline.finalize_task import OCRFinalizeTask
from tasks.ocr_tax_invoice_pipeline.retrieve_task import OCRRetrieveTask
from tasks.ocr_tax_invoice_pipeline.submit_task import OCRSubmitTask

__all__ = ["OCRSubmitTask", "OCRRetrieveTask", "OCRFinalizeTask"]
