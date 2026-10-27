"""
tests/test_semantic_loader.py
──────────────────────────────
Unit tests for the YAML semantic loader and table whitelist filtering.
"""

import os
import pytest

SEMANTIC_DIR = os.path.join(
    os.path.dirname(__file__), "..", "semantic"
)


@pytest.fixture
def loader():
    from app.semantic.loader import SemanticLoader
    return SemanticLoader(SEMANTIC_DIR)


class TestLoaderRobustness:
    def test_loads_without_crash(self, loader):
        tables = loader.load_all_tables()
        metrics = loader.load_all_metrics()
        joins = loader.load_approved_joins()
        few_shots = loader.load_few_shots()
        synonyms = loader.load_synonyms()

        assert isinstance(tables, list)
        assert isinstance(metrics, list)
        assert isinstance(joins, list)
        assert isinstance(few_shots, list)
        assert isinstance(synonyms, dict)

    def test_whitelist_filtering(self, monkeypatch):
        from app.semantic.loader import SemanticLoader
        loader_instance = SemanticLoader(SEMANTIC_DIR)

        # Mock get_included_tables to return only 'selected_table'
        monkeypatch.setattr(loader_instance, "get_included_tables", lambda: {"selected_table"})
        # Mock _load_yaml_dir to return dummy tables
        monkeypatch.setattr(
            loader_instance,
            "_load_yaml_dir",
            lambda subdir: [
                {"table": "Selected_Table", "columns": []},
                {"table": "Ignored_Table", "columns": []},
                {"table": "Other_Table", "columns": []},
            ],
        )

        filtered_tables = loader_instance.load_all_tables()
        assert len(filtered_tables) == 1
        assert filtered_tables[0]["table_name"].lower() == "selected_table"

    def test_whitelist_empty_loads_all(self, monkeypatch):
        from app.semantic.loader import SemanticLoader
        loader_instance = SemanticLoader(SEMANTIC_DIR)

        # Whitelist is None (no filter configured)
        monkeypatch.setattr(loader_instance, "get_included_tables", lambda: None)
        monkeypatch.setattr(
            loader_instance,
            "_load_yaml_dir",
            lambda subdir: [
                {"table": "TableA", "columns": []},
                {"table": "TableB", "columns": []},
            ],
        )

        all_tables = loader_instance.load_all_tables()
        assert len(all_tables) == 2
