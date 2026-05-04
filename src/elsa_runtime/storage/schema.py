"""
Schema Registry — defines the structure of every LanceDB table.

Rules:
1. Every metadata field that any consumer might filter on MUST be listed here.
2. Adding a new field: add it here with required=False and a sensible default.
3. Changing a field type: NEVER. Create a new field + deprecate old one.
4. Removing a field: mark deprecated=True here. Do NOT remove the column from LanceDB.
5. The VectorStore Protocol `where` clause can only reference fields marked filterable=True.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class FieldDef:
    """Definition of a single column in a table."""
    type: str                    # "str" | "int" | "float" | "bool"
    filterable: bool = False     # Can this field appear in `where` clauses?
    required: bool = False       # Must be provided on add()?
    default: Any = None          # Default value if not provided (None = nullable)
    deprecated: bool = False     # Soft-deleted field, still in DB but not used


@dataclass(frozen=True)
class TableSchema:
    """Complete schema for one table."""
    fields: dict[str, FieldDef]
    description: str = ""

    def filterable_fields(self) -> set[str]:
        return {k for k, v in self.fields.items() if v.filterable and not v.deprecated}

    def required_fields(self) -> set[str]:
        return {k for k, v in self.fields.items() if v.required and not v.deprecated}

    def all_field_names(self) -> set[str]:
        return {k for k, v in self.fields.items() if not v.deprecated}


# ============================================================
# Table Definitions
# ============================================================

SCHEMAS: dict[str, TableSchema] = {
    "papers": TableSchema(
        description="Research papers analyzed by Rei.",
        fields={
            "arxiv_id":          FieldDef(type="str",   filterable=True,  required=True),
            "tier":              FieldDef(type="str",   filterable=True,  required=False, default="C"),
            "core_contribution": FieldDef(type="str",   filterable=False, required=False),
            "venue":             FieldDef(type="str",   filterable=True,  required=False),
            "year":              FieldDef(type="int",   filterable=True,  required=False),
            "authors":           FieldDef(type="str",   filterable=False, required=False),
            "domain":            FieldDef(type="str",   filterable=True,  required=False),
        },
    ),

    "insights": TableSchema(
        description="Distilled insights from agent work (05c).",
        fields={
            "agent":             FieldDef(type="str",   filterable=True,  required=True),
            "domain":            FieldDef(type="str",   filterable=True,  required=True),
            "lifecycle":         FieldDef(type="str",   filterable=True,  required=True, default="active"),
            "scope":             FieldDef(type="str",   filterable=True,  required=True),
            "confidence":        FieldDef(type="float", filterable=True,  required=False, default=0.5),
            "task_type":         FieldDef(type="str",   filterable=True,  required=False),
            "derived_from_task": FieldDef(type="str",   filterable=False, required=False),
        },
    ),

    "tasks": TableSchema(
        description="Completed task records.",
        fields={
            "agent":    FieldDef(type="str",  filterable=True,  required=True),
            "verified": FieldDef(type="bool", filterable=True,  required=False, default=False),
            "body":     FieldDef(type="str",  filterable=False, required=False),
        },
    ),

    "errors": TableSchema(
        description="Error records for learning.",
        fields={
            "agent":      FieldDef(type="str",  filterable=True,  required=True),
            "error_type": FieldDef(type="str",  filterable=True,  required=False),
            "resolved":   FieldDef(type="bool", filterable=True,  required=False, default=False),
        },
    ),

    "knowledge": TableSchema(
        description="User-provided knowledge and facts.",
        fields={
            "category": FieldDef(type="str",  filterable=True,  required=False),
            "source":   FieldDef(type="str",  filterable=True,  required=False),
            "verified": FieldDef(type="bool", filterable=True,  required=False, default=False),
        },
    ),

    "conversations": TableSchema(
        description="Conversation summaries.",
        fields={
            "agent": FieldDef(type="str", filterable=True, required=True),
            "type":  FieldDef(type="str", filterable=True, required=False),
        },
    ),

    "solutions": TableSchema(
        description="Successful solution templates.",
        fields={
            "agent":     FieldDef(type="str", filterable=True, required=True),
            "task_type": FieldDef(type="str", filterable=True, required=False),
        },
    ),

    "procedures": TableSchema(
        description="Multi-step operational procedures (05-MEMORY L4d).",
        fields={
            "agent":      FieldDef(type="str", filterable=True,  required=True),
            "task_type":  FieldDef(type="str", filterable=True,  required=False),
            "times_used": FieldDef(type="int", filterable=True,  required=False, default=0),
        },
    ),

    "project_digests": TableSchema(
        description="Per-project knowledge digests.",
        fields={
            "project_id": FieldDef(type="str", filterable=True, required=True),
        },
    ),

    "theory_notes": TableSchema(
        description="Theory-level analysis notes (Rei).",
        fields={
            "domain": FieldDef(type="str", filterable=True, required=False),
        },
    ),

    "tool_docs": TableSchema(
        description="Tool documentation and usage notes.",
        fields={
            "tool_name": FieldDef(type="str", filterable=True, required=True),
        },
    ),
}


def get_schema(table_name: str) -> TableSchema:
    """Get schema for a table. Raises KeyError if table is not registered."""
    if table_name not in SCHEMAS:
        raise KeyError(
            f"Unknown table '{table_name}'. Registered tables: {sorted(SCHEMAS.keys())}"
        )
    return SCHEMAS[table_name]


def get_all_table_names() -> list[str]:
    return sorted(SCHEMAS.keys())
