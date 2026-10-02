import uuid
from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class ContentBlock(BaseModel):
    block_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    block_type: Literal["text", "table", "footnote"]
    text: str
    raw_table: Optional[List[List[Optional[str]]]] = None
    position: int = 0


class ParsedSection(BaseModel):
    section_id: str
    title: str
    content_blocks: List[ContentBlock] = []
    order: int = 0

    def full_text(self) -> str:
        return "\n\n".join(b.text for b in self.content_blocks)


class ParsedDocument(BaseModel):
    doc_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    source_path: str
    company: str
    ticker: str
    filing_type: str = "10-K"
    fiscal_year: int
    filing_date: Optional[str] = None
    accession_number: Optional[str] = None
    sections: List[ParsedSection] = []
    parsed_at: str = Field(default_factory=lambda: datetime.now().isoformat())

    def section_by_id(self, section_id: str) -> Optional[ParsedSection]:
        return next((s for s in self.sections if s.section_id == section_id), None)


class Chunk(BaseModel):
    chunk_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    parent_id: str           # ParsedSection.section_id
    doc_id: str              # ParsedDocument.doc_id
    text: str
    company: str
    ticker: str
    filing_type: str
    fiscal_year: int
    section_name: str
    chunk_type: Literal["text", "table", "footnote"]
    token_count: int
    position: int = 0


class RetrievedChunk(BaseModel):
    chunk: Chunk
    score: float
    parent_text: str         # full section text — passed to LLM as context


class QueryResult(BaseModel):
    query: str
    answer: str
    citations: List[dict]
    chunks_used: List[RetrievedChunk]
    query_type: str

    # D9 status taxonomy, added in P1-05 so a dependency failure is
    # distinguishable from an answer or a clarification. v1 had no status
    # field, so an LLM outage looked exactly like "please name a company"
    # (Phase 0 F1). Phase 3 replaces this with answering/outcome.Outcome;
    # the default keeps every existing v1 call site valid.
    status: Literal[
        "answered", "answered_text", "abstained", "clarification_needed", "error"
    ] = "answered_text"
    # One of the spec section 6.5 error_code values when status == "error".
    error_code: Optional[str] = None
