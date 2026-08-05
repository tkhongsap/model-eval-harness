"""Domain models for the RTR fraud validation pipeline.

These dataclasses replace raw dicts and positional lists throughout the codebase,
giving every inter-component boundary a clear, typed contract.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

# ---------------------------------------------------------------------------
# Token / cost tracking
# ---------------------------------------------------------------------------

@dataclass
class TokenUsage:
    text_input_tokens: int = 0
    image_input_tokens: int = 0
    text_cache_tokens: int = 0
    image_cache_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_input_tokens(self) -> int:
        return self.text_input_tokens + self.image_input_tokens

    def to_dict(self) -> dict:
        return {
            "text_input_tokens": self.text_input_tokens,
            "image_input_tokens": self.image_input_tokens,
            "text_cache_tokens": self.text_cache_tokens,
            "image_cache_tokens": self.image_cache_tokens,
            "output_tokens": self.output_tokens,
        }


# ---------------------------------------------------------------------------
# GPS / image metadata
# ---------------------------------------------------------------------------

@dataclass
class GpsMetadata:
    photo_lat: float = 0.0
    photo_lon: float = 0.0
    rtr_lat: float = 0.0
    rtr_lon: float = 0.0
    flag: str = "No Both Lat/Long"

    def to_list(self) -> list:
        """Returns [photo_lat, photo_lon, rtr_lat, rtr_lon, flag]."""
        return [self.photo_lat, self.photo_lon, self.rtr_lat, self.rtr_lon, self.flag]


# ---------------------------------------------------------------------------
# AI detection output
# ---------------------------------------------------------------------------

@dataclass
class UnrelateCategory:
    un_relate_human: str = "0"
    un_relate_animal: str = "0"
    un_relate_location: str = "0"
    un_relate_object: str = "0"


@dataclass
class DetectionResult:
    """Structured output from one Gemini fraud_validation call."""
    from_other_device: str = "0/0"
    shop_operate: str = "0/0"     # maps to Closed_Business in the output schema
    un_relate: str = "0"
    un_relate_category: UnrelateCategory = field(default_factory=UnrelateCategory)
    token_usage: TokenUsage = field(default_factory=TokenUsage)

    @classmethod
    def from_dict(cls, data: dict, token_usage: TokenUsage | None = None) -> DetectionResult:
        cat_raw = data.get("un_relate_category", {})
        return cls(
            from_other_device=str(data.get("from_other_device", "0/0")),
            shop_operate=str(data.get("shop_operate", "0/0")),
            un_relate=str(data.get("un_relate", "0")),
            un_relate_category=UnrelateCategory(
                un_relate_human=str(cat_raw.get("un_relate_human", "0")),
                un_relate_animal=str(cat_raw.get("un_relate_animal", "0")),
                un_relate_location=str(cat_raw.get("un_relate_location", "0")),
                un_relate_object=str(cat_raw.get("un_relate_object", "0")),
            ),
            token_usage=token_usage or TokenUsage(),
        )


# ---------------------------------------------------------------------------
# Input: one row from the CSV
# ---------------------------------------------------------------------------

@dataclass
class ShopRecord:
    """Typed wrapper for one row from the input CSV."""
    original_row_id: int
    xd_rtr_code: str = ""
    xt_rtr_code: str = ""
    xd_rtr_name: str = ""
    xt_rtr_name: str = ""
    photo_1_path: str = ""
    photo_2_path: str = ""
    photo_3_path: str = ""
    xd_latitude: str = "" 	
    xd_longitude: str = ""

    @property
    def rtr_lat(self) -> str:
        return self.xd_latitude 

    @property
    def rtr_lon(self) -> str:
        return self.xd_longitude

    @property
    def rtr_code(self) -> str:
        return self.xd_rtr_code or self.xt_rtr_code

    @property
    def rtr_name(self) -> str:
        return self.xd_rtr_name or self.xt_rtr_name

    @property
    def shop_path(self) -> str:
        """S3 folder path derived from the first photo path."""
        if self.photo_1_path:
            return "/".join(self.photo_1_path.split("/")[:4])
        return ""

    @property
    def image_paths(self) -> list[str]:
        """Valid image paths only (non-empty, correct extension)."""
        return [
            p for p in [self.photo_1_path, self.photo_2_path, self.photo_3_path]
            if p
            and isinstance(p, str)
            and p.lower().endswith((".jpg", ".jpeg", ".png"))
        ]

    @classmethod
    def from_row(cls, row: dict) -> ShopRecord:
        return cls(
            original_row_id=int(row.get("original_row_id", 0)),
            xd_rtr_code=str(row.get("xd_rtr_code") or ""),
            xt_rtr_code=str(row.get("xt_rtr_code") or ""),
            xd_rtr_name=str(row.get("xd_rtr_name") or ""),
            xt_rtr_name=str(row.get("xt_rtr_name") or ""),
            photo_1_path=str(row.get("photo_1_path") or ""),
            photo_2_path=str(row.get("photo_2_path") or ""),
            photo_3_path=str(row.get("photo_3_path") or ""),
            xd_latitude=str(row.get("xd_latitude") or ""),
            xd_longitude=str(row.get("xd_longitude") or ""),
        )


# ---------------------------------------------------------------------------
# Output: per-shop processing result
# ---------------------------------------------------------------------------

class ProcessStatus:
    SUCCESS = "success"
    FAIL = "fail"
    ERROR = "error"


@dataclass
class ShopResult:
    """Complete result for one shop.

    ``to_output_list()`` maps exactly to the 22-column ``appended_schema``
    defined in config/app/rtr_main.yaml, in the same order.
    The ``status`` field is written to a separate column in the DataFrame.
    """
    # --- row identity (used to update the correct DataFrame row) ---
    original_row_id: int = 0

    # --- appended_schema columns (order matters for to_output_list) ---
    run_date: str = ""
    folder_name: str = ""
    rtr_code: str = ""
    rtr_name: str = ""
    number_of_images: int = 0
    photo_name_1: str = ""
    photo_name_2: str = ""
    photo_name_3: str = ""
    photo1_lat: str = ""
    photo1_long: str = ""
    rtr1_lat: str = ""
    rtr1_long: str = ""
    photo1_flag300: str = ""
    same_photo: str = ""
    from_other_device: str = ""
    closed_business: str = ""
    un_relate: str = ""
    un_relate_human: str = ""
    un_relate_animal: str = ""
    un_relate_location: str = ""
    un_relate_object: str = ""
    complaint_status: str = ""

    # --- not in appended_schema; written separately ---
    status: str = ProcessStatus.FAIL

    # --- internal / logging only ---
    start_time: datetime | None = None
    end_time: datetime | None = None
    error_message: str = ""
    token_usage: TokenUsage = field(default_factory=TokenUsage)
    image_parts: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def to_output_list(self) -> list:
        """Returns the 22-element list matching appended_schema column order."""
        return [
            self.run_date,
            self.folder_name,
            self.rtr_code,
            self.rtr_name,
            self.number_of_images,
            self.photo_name_1,
            self.photo_name_2,
            self.photo_name_3,
            self.photo1_lat,
            self.photo1_long,
            self.rtr1_lat,
            self.rtr1_long,
            self.photo1_flag300,
            self.same_photo,
            self.from_other_device,
            self.closed_business,
            self.un_relate,
            self.un_relate_human,
            self.un_relate_animal,
            self.un_relate_location,
            self.un_relate_object,
            self.complaint_status,
        ]

    def to_log_dict(self) -> dict:
        return {
            "status": self.status,
            "start_time": str(self.start_time) if self.start_time else "",
            "end_time": str(self.end_time) if self.end_time else "",
            "process_time": (
                (self.end_time - self.start_time).total_seconds()
                if self.start_time and self.end_time
                else 0
            ),
            "message": self.error_message or f"success process : {self.rtr_code}-{self.rtr_name}",
            "rtr_code": self.rtr_code,
            "rtr_name": self.rtr_name,
            "image_parts": self.image_parts,
            "meta_data": self.token_usage.to_dict(),
        }

    # ------------------------------------------------------------------
    # Factory methods — replace 4 duplicated list_of_value builders
    # ------------------------------------------------------------------

    @classmethod
    def _base(cls, record: ShopRecord) -> ShopResult:
        return cls(
            original_row_id=record.original_row_id,
            run_date=datetime.now().strftime("%Y-%m-%d %H-%M-%S"),
            folder_name=record.shop_path,
            rtr_code=record.rtr_code,
            rtr_name=record.rtr_name,
            number_of_images=len(record.image_paths),
            start_time=datetime.now(),
        )

    @classmethod
    def no_photo(cls, record: ShopRecord, photo1_lat, photo1_lon, rtr1_lat, rtr1_lon, flag) -> ShopResult:
        r = cls._base(record)
        r.photo1_lat = photo1_lat
        r.photo1_long = photo1_lon
        r.rtr1_lat = rtr1_lat
        r.rtr1_long = rtr1_lon
        r.photo1_flag300 = flag
        r.complaint_status = "inComplaint-No Photo"
        r.status = ProcessStatus.FAIL
        r.error_message = f"skipping: {record.rtr_code}-{record.rtr_name}, no image links from S3"
        return r

    @classmethod
    def insufficient_photos(cls, record: ShopRecord, photo1_lat, photo1_lon, rtr1_lat, rtr1_lon, flag) -> ShopResult:
        r = cls._base(record)
        r.photo1_lat = photo1_lat
        r.photo1_long = photo1_lon
        r.rtr1_lat = rtr1_lat
        r.rtr1_long = rtr1_lon
        r.photo1_flag300 = flag
        r.complaint_status = "incompliant-Less than 3 Photos"
        r.status = ProcessStatus.FAIL
        r.error_message = f"skipping: {record.rtr_code}-{record.rtr_name}, fewer than 3 photos"
        return r

    @classmethod
    def s3_error(cls, record: ShopRecord, message: str = "") -> ShopResult:
        r = cls._base(record)
        r.complaint_status = "inComplaint-No Photo"
        r.status = ProcessStatus.FAIL
        r.error_message = message or f"skipping: {record.rtr_code}-{record.rtr_name}, no files in S3"
        return r

    @classmethod
    def unhandled_error(cls, record: ShopRecord, message: str = "") -> ShopResult:
        r = cls._base(record)
        r.complaint_status = "inComplaint"
        r.status = ProcessStatus.ERROR
        r.error_message = message or f"unhandled error: {record.rtr_code}-{record.rtr_name}"
        return r


# ---------------------------------------------------------------------------
# Pipeline-level configuration
# ---------------------------------------------------------------------------

@dataclass
class PipelineConfig:
    """All runtime configuration needed to run FraudValidationPipeline."""
    batch_size: int
    s3_bucket: str
    gcs_bucket: str
    today: datetime
    project_id: str
    project_name: str

    # SharePoint - Fraud site
    fraud_site_base_root: str
    input_folder: str
    output_folder: str
    backup_folder: str
    archive_folder: str

    # SharePoint - Control site
    control_site_base_root: str
    control_site_prompts_root: str
    control_site_transaction_log_path: str
    control_site_performance_log_path: str
    control_site_cost_path: str

    # Email recipients
    recipient_emails: list = field(default_factory=list)
    team_emails: list = field(default_factory=list)
    cc_emails: list = field(default_factory=list)

    # Derived paths (set after construction)
    input_file_path: str = ""
    lookup_file_path: str = ""
    output_user_file_path: str = ""
    output_system_log_file_path: str = ""

    # rsa private key
    rsa_private_key: str = ""


@dataclass
class PulseResult:
    intent: str = ""
    cluster: str = ""
    category: str = ""
    sub_category: str = ""
    sentiment: str = ""
    confidence: str = ""
    remark: str = ""
    comment: str = ""
    emp_id: str = ""
    comment_id: str = ""
    error: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "PulseResult":
        try:
            return cls(
                intent=data["intent"],
                cluster=data["cluster"],
                category=data["category"],
                sub_category=data["sub_category"],
                sentiment=data["sentiment"],
                confidence=data["confidence"],
                remark=data["remark"],
                comment=data["comment"],
                emp_id=data["emp_id"],
                comment_id=data["comment_id"],
            )
        except (KeyError, ValueError) as e:
            return cls(
                intent=data.get("intent", ""),
                cluster=data.get("cluster", ""),
                category=data.get("category", ""),
                sub_category=data.get("sub_category", ""),
                sentiment=data.get("sentiment", ""),
                confidence=data.get("confidence", ""),
                remark=data.get("remark", ""),
                comment=data.get("comment", ""),
                emp_id=data.get("emp_id", ""),
                comment_id=data.get("comment_id", ""),
                error=str(e),
            )