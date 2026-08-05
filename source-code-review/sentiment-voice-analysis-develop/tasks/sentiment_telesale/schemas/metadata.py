class Metadata:
    """
    Metadata class to define the expected schemas.
    This class serves as a centralized location for defining the expected schemas.
    """

    # Define the expected schema for transaction logs and performance logs as class attributes.
    TRANSACTION_LOG_SCHEMA = [
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
        "total_cost_usd",
        "load_dt",
        "log_id",
        "log_type",
        "action",
        "status",
        "error_message",
        "created_dt",
        "updated_dt",
        "duration_seconds",
    ]

    # Define the expected schema for performance logs as a class attribute.
    PERFORMANCE_LOG_SCHEMA = [
        "data_date",
        "run_date",
        "load_dt",
        "gcp_project_id",
        "gcp_project_name",
        "total_transaction",
        "total_completed",
        "total_failed",
        "success_rate",
        "total_runtime",
        "log_id",
        "log_type",
        "action",
        "status",
        "error_message",
        "created_dt",
        "updated_dt",
        "duration_seconds",
    ]

    # Define the expected schema for batch processing logs as a class attribute.
    BATCH_PROCESSING_LOG_SCHEMA = [
        "data_date",
        "gcp_project_id",
        "gcp_project_name",
        "gcs_bucket_name",
        "source_path",
        "filename",
        "prediction_payload_path",
        "log_id",
        "log_type",
        "batch_job_id",
        "batch_job_display_name",
        "model_name",
        "action",
        "status",
        "error_message",
        "created_dt",
        "updated_dt",
        "duration_seconds",
    ]

    # GT column name → (dimension, dot-path into raw_prediction dict)
    DEFAULT_GT_SHEET_NAME = "Evaluation"
    GT_FIELD_MAPPING = {
        "op_call_opening_proper_identification": (
            "call_opening",
            "operations_and_professionalism.call_opening.proper_identification",
        ),
        "op_call_opening_call_origin_disclosure": (
            "call_opening",
            "operations_and_professionalism.call_opening.call_origin_disclosure",
        ),
        "op_call_opening_call_consent_before_engagement": (
            "call_opening",
            "operations_and_professionalism.call_opening.call_consent_before_engagement",
        ),
        "op_customer_identity_verification_customer_verification": (
            "customer_identity_verification",
            "operations_and_professionalism.customer_identity_verification.customer_verification",
        ),
        "op_customer_identity_verification_invalid_verification": (
            "customer_identity_verification",
            "operations_and_professionalism.customer_identity_verification.invalid_verification",
        ),
        "op_customer_identity_verification_missing_verification": (
            "customer_identity_verification",
            "operations_and_professionalism.customer_identity_verification.missing_verification",
        ),
        "op_language_and_tone_behavioral_violation": (
            "language_and_tone",
            "operations_and_professionalism.language_and_tone.behavioral_violation",
        ),
        "op_language_and_tone_clarity": (
            "language_and_tone",
            "operations_and_professionalism.language_and_tone.clarity",
        ),
        "op_language_and_tone_delivery_pace": (
            "language_and_tone",
            "operations_and_professionalism.language_and_tone.delivery_pace",
        ),
        "op_active_listening_no_interruption": (
            "active_listening",
            "operations_and_professionalism.active_listening.no_interruption",
        ),
        "op_active_listening_correct_understanding": (
            "active_listening",
            "operations_and_professionalism.active_listening.correct_understanding",
        ),
        "op_active_listening_acknowledgement_paraphrasing": (
            "active_listening",
            "operations_and_professionalism.active_listening.acknowledgement_paraphrasing",
        ),
        "op_call_closing_confirm_resolution": (
            "call_closing",
            "operations_and_professionalism.call_closing.confirm_resolution",
        ),
        "op_call_closing_courteous_ending": (
            "call_closing",
            "operations_and_professionalism.call_closing.courteous_ending",
        ),
        "op_call_closing_smooth_closing": (
            "call_closing",
            "operations_and_professionalism.call_closing.smooth_closing",
        ),
        "se_customer_needs_analysis_usage_based_analysis": (
            "customer_needs_analysis",
            "sales_effectiveness.customer_needs_analysis.usage_based_analysis",
        ),
        "se_customer_needs_analysis_benefit_highlight": (
            "customer_needs_analysis",
            "sales_effectiveness.customer_needs_analysis.benefit_highlight",
        ),
        "se_offer_presentation_quality_clarity_of_explanation": (
            "offer_presentation_quality",
            "sales_effectiveness.offer_presentation_quality.clarity_of_explanation",
        ),
        "se_offer_presentation_quality_customer_benefit_highlight": (
            "offer_presentation_quality",
            "sales_effectiveness.offer_presentation_quality.customer_benefit_highlight",
        ),
        "se_effective_objection_handling_failure_to_listen": (
            "effective_objection_handling",
            "sales_effectiveness.effective_objection_handling.failure_to_listen",
        ),
        "se_effective_objection_handling_confrontational_tone": (
            "effective_objection_handling",
            "sales_effectiveness.effective_objection_handling.confrontational_tone",
        ),
        "se_sales_closing_attempt_value_based_closing": (
            "sales_closing_attempt",
            "sales_effectiveness.sales_closing_attempt.value_based_closing",
        ),
        "se_sales_closing_attempt_unclear_separation": (
            "sales_closing_attempt",
            "sales_effectiveness.sales_closing_attempt.unclear_separation",
        ),
        "se_sales_closing_attempt_inadequate_addon_disclosure": (
            "sales_closing_attempt",
            "sales_effectiveness.sales_closing_attempt.inadequate_addon_disclosure",
        ),
        "se_cross_sell_upsell_missed_crosssell_upsell": (
            "cross_sell_upsell",
            "sales_effectiveness.cross_sell_upsell.missed_crosssell_upsell",
        ),
        "se_cross_sell_upsell_unclear_addon_separation_crosssell": (
            "cross_sell_upsell",
            "sales_effectiveness.cross_sell_upsell.unclear_addon_separation_crosssell",
        ),
        "se_cross_sell_upsell_inadequate_addon_disclosure_crosssell": (
            "cross_sell_upsell",
            "sales_effectiveness.cross_sell_upsell.inadequate_addon_disclosure_crosssell",
        ),
        "cx_positive_customer_experience_failure_to_demonstrate_empathy": (
            "positive_customer_experience",
            "customer_experience.positive_customer_experience.failure_to_demonstrate_empathy",
        ),
        "cx_positive_customer_experience_deflecting_responsibility": (
            "positive_customer_experience",
            "customer_experience.positive_customer_experience.deflecting_responsibility",
        ),
        "cx_positive_customer_experience_escalates_customer_emotion": (
            "positive_customer_experience",
            "customer_experience.positive_customer_experience.escalates_customer_emotion",
        ),
        "cx_clarity_of_communication_overly_technical_language": (
            "clarity_of_communication",
            "customer_experience.clarity_of_communication.overly_technical_language",
        ),
        "cx_clarity_of_communication_fails_to_clarify_limitations": (
            "clarity_of_communication",
            "customer_experience.clarity_of_communication.fails_to_clarify_limitations",
        ),
        "cx_clarity_of_communication_no_adjustment_for_complexity": (
            "clarity_of_communication",
            "customer_experience.clarity_of_communication.no_adjustment_for_complexity",
        ),
        "cx_building_trust_provides_unclear_information": (
            "building_trust",
            "customer_experience.building_trust.provides_unclear_information",
        ),
        "cx_building_trust_provides_misleading_information": (
            "building_trust",
            "customer_experience.building_trust.provides_misleading_information",
        ),
        "cx_building_trust_fails_to_connect_value": (
            "building_trust",
            "customer_experience.building_trust.fails_to_connect_value",
        ),
        "cp_compliance_data_privacy_compliance": ("compliance", "compliance.compliance.data_privacy_compliance"),
        "cp_compliance_sales_integrity_compliance": ("compliance", "compliance.compliance.sales_integrity_compliance"),
        "cp_compliance_professional_conduct_compliance": (
            "compliance",
            "compliance.compliance.professional_conduct_compliance",
        ),
    }

    # Define the expected output schema for evaluation results as a class attribute.
    EVALUATION_OUTPUT_SCHEMA = [
        "created_datetime",
        "processed_datetime",
        "gcp_project_id",
        "gcp_project_name",
        "model_version",
        "ground_truth_count",
        "prediction_count",
        "dimension",
        "label",
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
        "weight",
    ]
