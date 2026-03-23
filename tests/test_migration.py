"""Tests for schema migration utilities."""
import pyarrow as pa
import pytest

from elsa_runtime.storage.schema import FieldDef, TableSchema, get_schema
from elsa_runtime.storage.migration import (
    PYARROW_TYPES,
    build_default_row,
    detect_schema_diff,
    schema_to_arrow,
)


class TestSchemaToArrow:
    def test_correct_types(self):
        schema = get_schema("papers")
        arrow = schema_to_arrow(schema, vector_dim=8)
        # Core fields
        assert arrow.field("id").type == pa.utf8()
        assert arrow.field("text").type == pa.utf8()
        assert arrow.field("vector").type == pa.list_(pa.float32(), 8)
        # Metadata fields
        assert arrow.field("tier").type == pa.utf8()
        assert arrow.field("year").type == pa.int64()
        assert arrow.field("domain").type == pa.utf8()

    def test_includes_all_fields(self):
        schema = get_schema("papers")
        arrow = schema_to_arrow(schema, vector_dim=8)
        names = set(arrow.names)
        # Core + all non-deprecated fields
        expected_meta = schema.all_field_names()
        assert expected_meta.issubset(names)
        assert {"id", "text", "vector"}.issubset(names)

    def test_nullable_matches_required(self):
        schema = get_schema("papers")
        arrow = schema_to_arrow(schema, vector_dim=8)
        # arxiv_id is required -> not nullable
        assert arrow.field("arxiv_id").nullable is False
        # tier is not required -> nullable
        assert arrow.field("tier").nullable is True

    def test_deprecated_fields_excluded(self):
        schema = TableSchema(fields={
            "active": FieldDef(type="str"),
            "old": FieldDef(type="str", deprecated=True),
        })
        arrow = schema_to_arrow(schema, vector_dim=8)
        names = set(arrow.names)
        assert "active" in names
        assert "old" not in names


class TestDetectSchemaDiff:
    def test_no_drift(self):
        schema = get_schema("tasks")
        actual = {"id", "text", "vector", "agent", "verified", "body"}
        diff = detect_schema_diff(schema, actual)
        assert diff["ok"] is True
        assert diff["new_fields"] == {}

    def test_new_field_detected(self):
        schema = get_schema("tasks")
        # Missing "body" column
        actual = {"id", "text", "vector", "agent", "verified"}
        diff = detect_schema_diff(schema, actual)
        assert diff["ok"] is False
        assert "body" in diff["new_fields"]

    def test_deprecated_field_detected(self):
        schema = TableSchema(fields={
            "active": FieldDef(type="str"),
            "old": FieldDef(type="str", deprecated=True),
        })
        actual = {"id", "text", "vector", "active", "old"}
        diff = detect_schema_diff(schema, actual)
        assert "old" in diff["deprecated"]


class TestBuildDefaultRow:
    def test_defaults_for_tasks(self):
        schema = get_schema("tasks")
        defaults = build_default_row(schema)
        assert defaults["agent"] == ""  # str, no default -> ""
        assert defaults["verified"] is False  # bool, default=False
        assert defaults["body"] == ""  # str, no default -> ""

    def test_defaults_for_insights(self):
        schema = get_schema("insights")
        defaults = build_default_row(schema)
        assert defaults["lifecycle"] == "active"  # has explicit default
        assert defaults["confidence"] == 0.5  # has explicit default
        assert defaults["agent"] == ""  # str required, no default

    def test_deprecated_excluded(self):
        schema = TableSchema(fields={
            "active": FieldDef(type="str", default="yes"),
            "old": FieldDef(type="str", default="no", deprecated=True),
        })
        defaults = build_default_row(schema)
        assert "active" in defaults
        assert "old" not in defaults
