"""
tests/test_validator.py
────────────────────────
Unit tests for the SQL AST validator (Node 5).
These tests run without any database connection.
"""

import pytest
from app.utils.sql_safety import validate_sql


class TestSelectAllowed:
    def test_simple_select_passes(self):
        sql = "SELECT * FROM dbo.Orders"
        result = validate_sql(sql)
        assert result.passed
        assert result.sanitized_sql is not None

    def test_select_with_join_passes(self):
        sql = """
        SELECT c.name, SUM(o.amount) AS Revenue
        FROM dbo.Orders o
        INNER JOIN dbo.Customers c ON o.customer_id = c.id
        WHERE o.status = 'completed'
        GROUP BY c.name
        """
        result = validate_sql(sql)
        assert result.passed

    def test_top_n_injected_when_missing(self):
        sql = "SELECT name FROM dbo.Customers"
        result = validate_sql(sql, max_rows=500)
        assert result.passed
        assert "TOP 500" in result.sanitized_sql.upper()

    def test_top_n_not_duplicated_when_present(self):
        sql = "SELECT TOP 100 name FROM dbo.Customers"
        result = validate_sql(sql, max_rows=1000)
        assert result.passed
        # Should not inject another TOP — original TOP 100 preserved
        assert result.sanitized_sql.upper().count("TOP") == 1


class TestDangerousStatements:
    def test_drop_table_blocked(self):
        sql = "DROP TABLE dbo.Orders"
        result = validate_sql(sql)
        assert not result.passed
        assert any("not allowed" in e or "DROP" in e for e in result.errors)

    def test_delete_blocked(self):
        sql = "DELETE FROM dbo.Orders WHERE 1=1"
        result = validate_sql(sql)
        assert not result.passed

    def test_update_blocked(self):
        sql = "UPDATE dbo.Customers SET name = 'hacked' WHERE 1=1"
        result = validate_sql(sql)
        assert not result.passed

    def test_insert_blocked(self):
        sql = "INSERT INTO dbo.Orders (amount) VALUES (999)"
        result = validate_sql(sql)
        assert not result.passed

    def test_stacked_statements_blocked(self):
        sql = "SELECT 1; DROP TABLE dbo.Orders"
        result = validate_sql(sql)
        assert not result.passed
        assert any("Multiple" in e for e in result.errors)

    def test_comment_injection_blocked(self):
        sql = "SELECT * FROM dbo.Orders -- WHERE 1=1"
        result = validate_sql(sql)
        assert not result.passed


class TestTableWhitelist:
    def test_allowed_table_passes(self):
        sql = "SELECT * FROM dbo.Orders"
        result = validate_sql(sql, allowed_tables={"orders", "customers"})
        assert result.passed

    def test_unknown_table_blocked(self):
        sql = "SELECT * FROM dbo.SensitiveData"
        result = validate_sql(sql, allowed_tables={"orders", "customers"})
        assert not result.passed
        assert any("Unknown tables" in e for e in result.errors)


class TestEdgeCases:
    def test_empty_sql_fails(self):
        result = validate_sql("")
        assert not result.passed

    def test_whitespace_only_fails(self):
        result = validate_sql("   ")
        assert not result.passed

    def test_complex_select_with_subquery(self):
        sql = """
        SELECT TOP 10
            c.name,
            (SELECT COUNT(*) FROM dbo.Orders o2 WHERE o2.customer_id = c.id) AS OrderCount
        FROM dbo.Customers c
        WHERE c.region = 'North America'
        ORDER BY OrderCount DESC
        """
        result = validate_sql(sql, allowed_tables={"customers", "orders"})
        assert result.passed
