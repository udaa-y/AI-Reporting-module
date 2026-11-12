"""
app/dashboard/dimensions.py
────────────────────────────
THE place to add or remove appointment chart parameters.

A Dimension is anything you can FILTER by and/or BREAK DOWN (stack) the chart by.
The frontend builds its filter dropdowns and "Break down by" menu from this
registry (GET /dashboard/appointments/meta), so adding an entry here is all it
takes for a new filter to appear in the UI.

  value_sql   expression stored/filtered on (e.g. an id)
  label_sql   expression shown to users (e.g. a name); defaults to value_sql
  joins       keys from JOINS that the expressions need
  filter      show as a filter dropdown
  breakdown   offer in the "Break down by" menu

All SQL here is fixed text written by developers. User selections are only ever
passed as bind parameters, never concatenated into SQL.
"""

from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

# Base table alias is `a` (dbo.Appointment). Add a join once, reuse it anywhere.
JOINS: Dict[str, str] = {
    "employee": "LEFT JOIN dbo.Employee e ON e.Id = a.DoctorId",
    "patient": "LEFT JOIN dbo.Patient p ON p.Id = a.PatientId",
    "branch": "LEFT JOIN dbo.BranchMaster br ON br.BranchId = a.BranchId",
}


@dataclass(frozen=True)
class Dimension:
    label: str
    value_sql: str
    label_sql: Optional[str] = None
    joins: Tuple[str, ...] = ()
    filter: bool = True
    breakdown: bool = True
    max_options: int = 500              # cap for very large lists (e.g. doctors)
    all_branches_only: bool = False     # only offered while viewing "All branches"

    @property
    def label_expr(self) -> str:
        return self.label_sql or self.value_sql


DIMENSIONS: Dict[str, Dimension] = {

        # Compare branches side by side (breakdown only; the top-bar selector filters).
    "branch": Dimension(
        label="Branch",
        value_sql="a.BranchId",
        label_sql="br.BranchName",
        joins=("branch",),
        filter=False,
        all_branches_only=True,
    ),

    "department": Dimension(
        label="Department",
        value_sql="e.Department",
        joins=("employee",),
    ),
    "doctor": Dimension(
        label="Doctor",
        value_sql="a.DoctorId",
        label_sql="e.Name",
        joins=("employee",),
    ),
    "status": Dimension(
        label="Appointment Status",
        value_sql="a.VisitStatus",
    ),
    # Example of adding a parameter — one entry, nothing else to change:
    # "patient_type": Dimension(label="Patient Type", value_sql="a.PatientType"),
}

# Rows shown when drilling down to individual appointments: (key, header, SQL).
LIST_COLUMNS = [
    ("id", "Appointment #", "a.Id"),
    ("visit_date", "Visit Date", "a.VisitDate"),
    ("branch", "Branch", "br.BranchName"),
    ("patient", "Patient", "p.Name"),
    ("fileno", "File No.", "p.FileNo"),
    ("doctor", "Doctor", "e.Name"),
    ("department", "Department", "e.Department"),
    ("status", "Status", "a.VisitStatus"),
    ("patient_type", "Patient Type", "a.PatientType"),
    ("follow_up", "Follow-up", "CASE WHEN a.ParentAppointment IS NULL THEN 'No' ELSE 'Yes' END"),
]
LIST_JOINS = ("employee", "patient", "branch")

# A NULL value in a dimension (e.g. no status recorded) travels as this token.
NULL_TOKEN = "__null__"
NULL_LABEL = "Not specified"
