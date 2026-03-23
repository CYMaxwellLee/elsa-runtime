"""Tests for the Schema Registry."""
import pytest

from elsa_runtime.storage.schema import (
    SCHEMAS,
    FieldDef,
    TableSchema,
    get_all_table_names,
    get_schema,
)


class TestSchemaRegistry:
    def test_all_tables_have_schemas(self):
        """All 11 tables should be registered."""
        names = get_all_table_names()
        assert len(names) == 11
        expected = {
            "conversations", "errors", "insights", "knowledge",
            "papers", "procedures", "project_digests", "solutions",
            "tasks", "theory_notes", "tool_docs",
        }
        assert set(names) == expected

    def test_filterable_fields_not_empty_for_key_tables(self):
        """papers and insights must have filterable fields."""
        papers = get_schema("papers")
        assert len(papers.filterable_fields()) >= 3
        assert "tier" in papers.filterable_fields()
        assert "domain" in papers.filterable_fields()

        insights = get_schema("insights")
        assert len(insights.filterable_fields()) >= 3
        assert "lifecycle" in insights.filterable_fields()
        assert "agent" in insights.filterable_fields()

    def test_required_fields_validation(self):
        papers = get_schema("papers")
        assert "arxiv_id" in papers.required_fields()

        insights = get_schema("insights")
        required = insights.required_fields()
        assert "agent" in required
        assert "domain" in required
        assert "lifecycle" in required

    def test_get_schema_unknown_table_raises(self):
        with pytest.raises(KeyError, match="Unknown table 'nonexistent'"):
            get_schema("nonexistent")

    def test_field_types_are_valid(self):
        """All field types must be one of str/int/float/bool."""
        valid_types = {"str", "int", "float", "bool"}
        for name, schema in SCHEMAS.items():
            for field_name, field_def in schema.fields.items():
                assert field_def.type in valid_types, \
                    f"Invalid type '{field_def.type}' for {name}.{field_name}"

    def test_all_field_names_excludes_deprecated(self):
        schema = TableSchema(
            fields={
                "active": FieldDef(type="str"),
                "old": FieldDef(type="str", deprecated=True),
            }
        )
        assert schema.all_field_names() == {"active"}
        assert schema.filterable_fields() == set()
