"""Tax-invoice reconcile business tasks for the OCR-pipeline v2 (``tasks/ocr_tax_invoice_pipeline/``).

Consumes the typed ``OCRResult`` from ``OCRRetrieveTask`` and returns it unchanged so the
trailing ``OCRFinalizeTask`` stamps terminal status only after reconciliation succeeds. The
post pipeline runs in this order (the engine executes top-level YAML keys in insertion order;
the order is pinned by ``tests/test_tasks/tax_invoice_reconcile/test_pipeline_config.py``):

    ReconcilePrecheckTask  → FIRST; halts if Master-Buyer / Master-Vendor / Z45 sources are
                             missing. Returns ``None`` — it runs before the ``OCRResult`` exists
                             and must never be moved after ``OCRRetrieveTask``.
    OCRRetrieveTask        → collects predictions into an ``OCRResult`` (no status change)
    ReconcileTask          → reconciles OCRResult.final_df vs Master Buyer + Master Vendor + Z45;
                             returns the ``OCRResult`` unchanged
    OCRFinalizeTask        → ALWAYS last; stamps SUCCESS/FAILED in the pre-processing log

Any task placed between ``OCRRetrieveTask`` and ``OCRFinalizeTask`` MUST return
``self.pre_result``; returning ``None`` wipes the ``OCRResult`` from the chain, finalize
no-ops, and every file is stranded in-flight. See ``DEVELOPER_GUIDE.md`` § 2.

``TaxInvoiceFactCheckTask`` is a peer business task in this package: it scores ``OCRResult.final_df``
against a human-labelled ground truth and emits ``AI-Operation Fact Check log`` JSON lines (its own
pre/post pipeline configs, reusing the generic OCR submit/retrieve/finalize tasks unchanged).

Task classes are registered as an import side effect via ``tasks/__init__.py``.
"""

from tasks.tax_invoice_reconcile.fact_check_task import TaxInvoiceFactCheckTask
from tasks.tax_invoice_reconcile.precheck_task import ReconcilePrecheckTask
from tasks.tax_invoice_reconcile.reconcile_task import ReconcileTask
from tasks.tax_invoice_reconcile.reject_task import TaxInvoiceRejectTask

__all__ = ["ReconcilePrecheckTask", "ReconcileTask", "TaxInvoiceFactCheckTask", "TaxInvoiceRejectTask"]
