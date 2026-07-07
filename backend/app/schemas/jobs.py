"""API request/response models for the job lifecycle."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class CreateJobRequest(BaseModel):
    entity_type: str


class JobResponse(BaseModel):
    id: str
    entity_type: str
    status: str
    filename: str | None = None
    company: str | None = None
    rows: int | None = None
    columns: int | None = None
    notes: list[str] = Field(default_factory=list)
    created_at: str
    updated_at: str


class MappingRequest(BaseModel):
    template: str | None = None
    mapping: dict[str, str | None] = Field(default_factory=dict)
    constants: dict[str, str] = Field(default_factory=dict)


class ValidateRequest(BaseModel):
    known_groups: list[str] | None = None
    known_units: list[str] | None = None
    known_states: list[str] | None = None
    known_stock_groups: list[str] | None = None


class ResolveRequest(BaseModel):
    existing: list[dict[str, Any]] = Field(default_factory=list)


class GenerateRequest(BaseModel):
    company: str | None = None
    cutover_date: str | None = None
    # Consolidate B2C sales (no buyer GSTIN) into one summary voucher per day (GSTR-1 B2C-Others).
    b2c_summary: bool = False
    # Treat each row as a marketplace settlement -> multi-leg journal (Bank/Commission/Fees/TCS).
    settlement_mode: bool = False
