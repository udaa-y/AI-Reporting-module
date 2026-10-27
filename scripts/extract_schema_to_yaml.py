"""
scripts/extract_schema_to_yaml.py
───────────────────────────────────
Selective Auto-Schema Extractor

Connects to your Microsoft SQL Server database, inspects table
and column metadata, and generates semantic YAML files in semantic/tables/<table_name>.yaml.

Provision for restricted/selective learning:
  1. Specify via CLI:
     python scripts/extract_schema_to_yaml.py --tables Employees,Departments
  2. Specify via configuration:
     List tables in `semantic/included_tables.yaml` or in `.env` as `INCLUDED_TABLES=...`
     The script will automatically detect and extract ONLY those tables!
"""

import argparse
import os
import sys
import yaml

# Add root directory to python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.db.sqlserver import get_table_columns, get_table_names
from app.config import get_settings
from app.semantic.loader import SemanticLoader


def get_whitelisted_tables(cli_tables: str | None = None) -> set[str] | None:
    """Determine the list of tables to extract based on CLI or config."""
    if cli_tables:
        return {t.strip().lower() for t in cli_tables.split(",") if t.strip()}

    # Check semantic loader config (included_tables.yaml or .env INCLUDED_TABLES)
    settings = get_settings()
    loader = SemanticLoader(settings.semantic_yaml_dir)
    return loader.get_included_tables()


def export_schema_to_yaml(schema: str = "dbo", table_filter: str | None = None):
    settings = get_settings()
    out_dir = os.path.abspath(os.path.join(settings.semantic_yaml_dir, "tables"))
    os.makedirs(out_dir, exist_ok=True)

    target_whitelist = get_whitelisted_tables(table_filter)

    print(f"Connecting to SQL Server: {settings.sqlserver_server} / {settings.sqlserver_database} (Schema: {schema})...")
    try:
        all_tables = get_table_names(schema=schema)
    except Exception as exc:
        print(f"Error connecting or fetching table list: {exc}")
        return

    if not all_tables:
        print(f"No tables found in schema '{schema}'.")
        return

    # Filter tables if an inclusion list is provided
    if target_whitelist:
        filtered = [t for t in all_tables if t.lower() in target_whitelist]
        missing = target_whitelist - {t.lower() for t in all_tables}
        if missing:
            print(f"Note: Table(s) specified in inclusion list not found in DB: {missing}")
        all_tables = filtered
        print(f"Found {len(all_tables)} matching table(s) from inclusion list to extract: {all_tables}")
    else:
        print(f"No inclusion filter specified. Found {len(all_tables)} total table(s) in schema '{schema}'.")

    if not all_tables:
        print("No matching tables to extract.")
        return

    print("Generating YAML definitions for selected tables...")

    for table in all_tables:
        try:
            cols = get_table_columns(table, schema=schema)
            formatted_cols = []
            for c in cols:
                formatted_cols.append({
                    "name": c.get("COLUMN_NAME"),
                    "type": str(c.get("DATA_TYPE")),
                    "IS_NULLABLE": c.get("IS_NULLABLE", "YES"),
                    "description": c.get("column_description") or f"Column {c.get('COLUMN_NAME')}",
                })

            table_yaml = {
                "version": "v1",
                "table": table,
                "schema_name": schema,
                "description": f"Table {table} in {schema} schema.",
                "columns": formatted_cols,
                "business_notes": "Add domain-specific notes and filter rules here.",
            }

            out_file = os.path.join(out_dir, f"{table.lower()}.yaml")
            with open(out_file, "w", encoding="utf-8") as f:
                yaml.dump(table_yaml, f, sort_keys=False, default_flow_style=False)

            print(f"  [OK] Written: semantic/tables/{table.lower()}.yaml ({len(cols)} columns)")

        except Exception as exc:
            print(f"  [ERROR] Failed for table {table}: {exc}")

    print("\nSelective extraction complete! Only the specified tables are loaded into your semantic layer.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Extract specific SQL Server table schemas to semantic YAML")
    parser.add_argument("--schema", default="dbo", help="Database schema name (default: dbo)")
    parser.add_argument(
        "--tables",
        default=None,
        help="Comma-separated table names to extract (e.g. --tables TableA,TableB). If omitted, reads from semantic/included_tables.yaml or .env",
    )
    args = parser.parse_args()

    export_schema_to_yaml(schema=args.schema, table_filter=args.tables)
