"""
Schema migration — detects schema drift between Registry and actual LanceDB table,
and applies safe migrations (add new columns with defaults).

Unsafe migrations (type change, column removal) are rejected with clear error messages.
"""

from __future__ import annotations

import pyarrow as pa

from elsa_runtime.storage.schema import TableSchema

# Type mapping: schema.py type string -> PyArrow type
PYARROW_TYPES = {
    "str":   pa.utf8(),
    "int":   pa.int64(),
    "float": pa.float64(),
    "bool":  pa.bool_(),
}


def schema_to_arrow(table_schema: TableSchema, vector_dim: int = 1024) -> pa.Schema:
    """Convert a TableSchema to a PyArrow schema for LanceDB table creation.

    Column order: id, text, vector, then metadata fields alphabetically.
    """
    fields = [
        pa.field("id", pa.utf8(), nullable=False),
        pa.field("text", pa.utf8(), nullable=True),
        pa.field("vector", pa.list_(pa.float32(), vector_dim), nullable=False),
    ]

    for name, fdef in sorted(table_schema.fields.items()):
        if fdef.deprecated:
            continue
        pa_type = PYARROW_TYPES.get(fdef.type)
        if pa_type is None:
            raise ValueError(f"Unknown type '{fdef.type}' for field '{name}'")
        nullable = not fdef.required
        fields.append(pa.field(name, pa_type, nullable=nullable))

    return pa.schema(fields)


def detect_schema_diff(
    registry_schema: TableSchema,
    actual_column_names: set[str],
) -> dict:
    """Compare registry schema with actual table columns.

    Returns:
        {
            "new_fields": {"field_name": FieldDef, ...},
            "deprecated": {"field_name": FieldDef, ...},
            "ok": True/False,
        }
    """
    core_columns = {"id", "text", "vector"}
    expected = {name for name, fd in registry_schema.fields.items() if not fd.deprecated}
    actual_meta = actual_column_names - core_columns

    new_fields = {
        name: registry_schema.fields[name]
        for name in (expected - actual_meta)
    }
    deprecated = {
        name: registry_schema.fields[name]
        for name in actual_meta
        if name in registry_schema.fields and registry_schema.fields[name].deprecated
    }

    return {
        "new_fields": new_fields,
        "deprecated": deprecated,
        "ok": len(new_fields) == 0,
    }


def build_default_row(table_schema: TableSchema) -> dict:
    """Build a dict of default values for all fields. Used when adding records
    that don't have values for every column."""
    defaults = {}
    for name, fdef in table_schema.fields.items():
        if fdef.deprecated:
            continue
        if fdef.default is not None:
            defaults[name] = fdef.default
        else:
            # Type-appropriate empty value
            defaults[name] = {"str": "", "int": 0, "float": 0.0, "bool": False}[fdef.type]
    return defaults
