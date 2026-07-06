from __future__ import annotations

from typing import Any
import pandas as pd


class MappingTemplate:
    def __init__(self, name: str, mapping: dict[str, str], constants: dict[str, str]):
        self.name = name
        self.mapping = mapping
        self.constants = constants


SHOPIFY_TEMPLATE = MappingTemplate(
    name="shopify",
    mapping={
        "voucher_number": "Name",
        "date": "Created at",
        "ledger_name": "Lineitem name",
        "amount": "Lineitem price",
        "party_ledger": "Billing Name",
        "narration": "Name", # Order ID
    },
    constants={
        "voucher_type": "Sales",
        "dr_cr": "Cr",
        "opening_balance_drcr": "Cr",
    }
)


TEMPLATES: dict[str, MappingTemplate] = {
    "shopify": SHOPIFY_TEMPLATE,
}


def apply_template(template_name: str) -> tuple[dict[str, str], dict[str, str]]:
    template = TEMPLATES.get(template_name.lower())
    if not template:
        raise ValueError(f"Unknown template: {template_name}")
    return template.mapping, template.constants
