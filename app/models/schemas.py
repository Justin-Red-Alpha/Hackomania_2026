from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Optional

from pydantic import BaseModel


class InputType(str, Enum):
    url = "url"
    text = "text"
    pdf = "pdf"
    docx = "docx"
    html = "html"
    md = "md"
    rtf = "rtf"
    image = "image"


class ContentMetadata(BaseModel):
    input_type: InputType
    url: Optional[str] = None
    s3_url: Optional[str] = None
    title: Optional[str] = None
    publisher: Optional[str] = None
    date: Optional[date] = None
    author: Optional[str] = None
    section: Optional[str] = None
    is_opinion: bool = False
    original_language: str = "en"


class IngestionResult(BaseModel):
    content: ContentMetadata
    original_text: str
    text: str
    token_count: int
