"""
app/agent/nodes/validator.py
──────────────────────────────
Node 5 — SQL Validator

Wraps the sql_safety utility and integrates it into the LangGraph state.
This node is the hard security gate that runs after every SQL generation
or correction attempt.

Reads:   state.generated_sql, state.retrieved_tables
Writes:  state.validation_result, state.pipeline_stage
"""

import logging

from app.agent.state import QueryAgentState, ValidationResult as StateValidationResult
from app.utils.sql_safety import validate_sql

logger = logging.getLogger(__name__)


def validator_node(state: QueryAgentState) -> QueryAgentState:
    """
    LangGraph Node 5 — SQL Validator.

    Validates the generated SQL using sqlglot AST analysis.
    If validation fails, the graph routes back to sql_generator (Node 4).
    """
    raw_sql = state.get("generated_sql", "")

    logger.info("[Node 5] Validating SQL (attempt %d)", state.get("sql_attempt_count", 1))

    if not raw_sql:
        result: StateValidationResult = {
            "passed": False,
            "errors": ["No SQL was generated to validate."],
            "sanitized_sql": None,
        }
        return {**state, "validation_result": result, "pipeline_stage": "validation_failed"}

    # Extract allowed table names from retrieved context
    allowed_tables = {
        t["table_name"].lower()
        for t in state.get("retrieved_tables", [])
    }

    # Run AST validation
    validation = validate_sql(
        raw_sql=raw_sql,
        allowed_tables=allowed_tables if allowed_tables else None,
    )

    result: StateValidationResult = {
        "passed": validation.passed,
        "errors": validation.errors,
        "sanitized_sql": validation.sanitized_sql,
    }

    if validation.passed:
        logger.info("[Node 5] Validation PASSED. Sanitized SQL ready.")
        if validation.warnings:
            logger.warning("[Node 5] Warnings: %s", "; ".join(validation.warnings))
    else:
        logger.warning(
            "[Node 5] Validation FAILED. Errors: %s",
            "; ".join(validation.errors)
        )

    return {
        **state,
        "validation_result": result,
        "pipeline_stage": "validated" if validation.passed else "validation_failed",
        # Update generated_sql to the sanitized version (with TOP N injected)
        "generated_sql": validation.sanitized_sql or raw_sql,
    }
