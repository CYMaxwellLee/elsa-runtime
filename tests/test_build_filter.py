"""Tests for _build_filter SQL generation."""
import pytest

from elsa_runtime.storage.lancedb_store import _build_filter


class TestEqualityFilter:
    def test_eq_filter_string(self):
        sql = _build_filter({"tier": "A"}, "papers")
        assert sql == "tier = 'A'"

    def test_eq_filter_int(self):
        sql = _build_filter({"year": 2024}, "papers")
        assert sql == "year = 2024"

    def test_eq_filter_bool(self):
        sql = _build_filter({"verified": True}, "tasks")
        assert sql == "verified = true"

    def test_eq_filter_bool_false(self):
        sql = _build_filter({"verified": False}, "tasks")
        assert sql == "verified = false"


class TestOperatorFilter:
    def test_in_filter(self):
        sql = _build_filter({"tier": {"$in": ["A", "B"]}}, "papers")
        assert sql == "tier IN ('A', 'B')"

    def test_gt_filter(self):
        sql = _build_filter({"year": {"$gt": 2023}}, "papers")
        assert sql == "year > 2023"

    def test_lt_filter(self):
        sql = _build_filter({"year": {"$lt": 2025}}, "papers")
        assert sql == "year < 2025"

    def test_gte_filter(self):
        sql = _build_filter({"confidence": {"$gte": 0.8}}, "insights")
        assert sql == "confidence >= 0.8"

    def test_ne_filter_string(self):
        sql = _build_filter({"tier": {"$ne": "C"}}, "papers")
        assert sql == "tier != 'C'"

    def test_ne_filter_int(self):
        sql = _build_filter({"year": {"$ne": 2020}}, "papers")
        assert sql == "year != 2020"

    def test_list_implicit_in(self):
        """A list value without $in should be treated as implicit $in."""
        sql = _build_filter({"lifecycle": ["active", "dormant"]}, "insights")
        assert sql == "lifecycle IN ('active', 'dormant')"


class TestCombinedFilter:
    def test_combined_filter(self):
        sql = _build_filter({"tier": "A", "domain": "robotics"}, "papers")
        assert "tier = 'A'" in sql
        assert "domain = 'robotics'" in sql
        assert " AND " in sql


class TestValidation:
    def test_invalid_field_raises_valueerror(self):
        with pytest.raises(ValueError, match="Cannot filter on 'nonexistent'"):
            _build_filter({"nonexistent": "x"}, "papers")

    def test_non_filterable_field_raises_valueerror(self):
        with pytest.raises(ValueError, match="Cannot filter on 'core_contribution'"):
            _build_filter({"core_contribution": "x"}, "papers")

    def test_unknown_operator_raises(self):
        with pytest.raises(ValueError, match="Unknown operator"):
            _build_filter({"tier": {"$regex": ".*"}}, "papers")

    def test_empty_where_returns_empty(self):
        assert _build_filter({}, "papers") == ""
        assert _build_filter(None, "papers") == ""
