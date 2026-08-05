from io import BytesIO
from pathlib import Path

import pandas as pd
import yaml
from pypdf import PdfReader, PdfWriter


def read_xlsx(file_path: str | Path, sheet_name: str | int = 0) -> pd.DataFrame:
    """
    Reads an Excel file and returns it as a pandas DataFrame.
    Args:
        file_path (str|Path): The path to the file.
        sheet_name (str|int): The sheet name or index to read, default is 0.
    """
    if isinstance(file_path, str):
        file_path = Path(file_path)

    return pd.read_excel(file_path, sheet_name=sheet_name)


def read_file(file_path: str | Path, encoding: str = "utf-8") -> str:
    """
    Read a file from the given path and return its contents as a string.
    Args:
        file_path (str|Path): The path to the file.
        encoding (str): The file encoding, default is 'utf-8'.
    Returns:
        str: The contents of the file as a string.
    """
    if isinstance(file_path, str):
        file_path = Path(file_path)
    with open(file_path, encoding=encoding) as file:
        return file.read()


def load_yaml(file_path: str | Path, encoding: str = "utf-8") -> dict:
    """
    Load a YAML file from path and return its contents as a dictionary.
    Args:
        file_path (str|Path): The path to the YAML file.
        encoding (str): The file encoding, default is 'utf-8'.
    Returns:
        dict: The contents of the YAML file as a dictionary.
    """
    if isinstance(file_path, str):
        file_path = Path(file_path)
    with open(file_path, encoding=encoding) as file:
        return yaml.safe_load(file)


def load_yaml_str(yaml_string: str) -> dict:
    """
    Load a YAML string and return its contents as a dictionary.
    Args:
        yaml_string (str): The YAML formatted string.
    Returns:
        dict: The contents of the YAML string as a dictionary.
    """
    return yaml.safe_load(yaml_string)


def _serialize_pdf_writer(writer: PdfWriter) -> bytes:
    """Serialize a pypdf PdfWriter to bytes (pypdf 6.x has no write_to_bytes)."""
    buf = BytesIO()
    writer.write(buf)
    return buf.getvalue()


def pdf_spliter(
    file_name: str,
    file_content: bytes,
    max_pages_per_chunk: int = 10,
    chunk_suffix_format: str = "_{n}",
) -> list:
    """
    Split a PDF file into chunked sub-files.

    Parameters:
        file_name (str): The name of the source PDF file.
        file_content (bytes): The raw PDF bytes.
        max_pages_per_chunk (int): Max pages per chunk, default 10.
        chunk_suffix_format (str): Format for the chunk suffix appended to the
            stem. Must contain ``{n}`` (1-indexed chunk start page). Default
            ``"_{n}"`` yields names like ``report_1.pdf``, ``report_11.pdf``.

    Returns:
        list[dict]: One dict per chunk with keys:
            - file_name (str): chunked file name (stem + suffix + ".pdf")
            - file_content (bytes): the chunk's PDF bytes
            - parent_stem (str): the source file's stem (no suffix, no extension).
              Use this to group chunks back to their parent without parsing the
              chunk file name.
            - chunk_no (int): 1-indexed chunk number.
            - page_start (int): 1-indexed inclusive first page of this chunk
              within the parent.
            - page_end (int): 1-indexed inclusive last page of this chunk.
            - parent_total_pages (int): total page count of the parent PDF.

        The four trailing keys are additive — pre-existing callers (telesale /
        QA pipelines) that only consume the first three keys continue to work
        unchanged.
    """
    parent_stem = Path(file_name).stem
    reader = PdfReader(BytesIO(file_content))
    total_pages = len(reader.pages)
    page_streams = []

    for chunk_no, i in enumerate(range(0, total_pages, max_pages_per_chunk), start=1):
        writer = PdfWriter()
        end_idx = min(i + max_pages_per_chunk, total_pages)
        for page in reader.pages[i:end_idx]:
            writer.add_page(page)

        page_streams.append(
            {
                "file_name": f"{parent_stem}{chunk_suffix_format.format(n=i + 1)}.pdf",
                "file_content": _serialize_pdf_writer(writer),
                "parent_stem": parent_stem,
                "chunk_no": chunk_no,
                "page_start": i + 1,
                "page_end": end_idx,
                "parent_total_pages": total_pages,
            }
        )

    return page_streams
