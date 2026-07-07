"""Request/response models for saved mapping templates."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SaveTemplateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    entity_type: str
    mapping: dict[str, str | None] = Field(default_factory=dict)
    constants: dict[str, str] = Field(default_factory=dict)
    # the file's columns at save time — used to auto-match this template to future files
    source_columns: list[str] = Field(default_factory=list)


class TemplateResponse(BaseModel):
    id: str
    name: str
    entity_type: str
    mapping: dict[str, str] = Field(default_factory=dict)
    constants: dict[str, str] = Field(default_factory=dict)
    source_columns: list[str] = Field(default_factory=list)
    builtin: bool = False
    created_at: str | None = None
