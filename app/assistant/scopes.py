"""
app/assistant/scopes.py
────────────────────────
THE place to add, remove or re-permission "Ask AI" sections.

Each section limits which tables the AI may use for a question. That keeps the
prompt small (fewer tokens), makes the SQL more accurate, and is enforced by the
SQL validator — a question asked in "Appointments" can never read other tables.

  tables     semantic-layer table names (semantic/tables/*.yaml) the AI may use
  roles      which staff roles may ask in this section ("admin", "staff")
  icon       lucide-react icon name shown in the UI
  starters   example questions shown on an empty chat
"""

from dataclasses import dataclass, field
from typing import List, Tuple


@dataclass(frozen=True)
class Scope:
    key: str
    label: str
    description: str
    tables: Tuple[str, ...]
    roles: Tuple[str, ...] = ("admin",)
    icon: str = "MessageSquare"
    starters: Tuple[str, ...] = ()
    compare_starters: Tuple[str, ...] = ()

    @property
    def all_tables(self) -> Tuple[str, ...]:
        return self.tables + ("BranchMaster",)


SCOPES: List[Scope] = [
    Scope(
        key="appointments",
        label="Appointments",
        description="Visits, visit status, follow-ups and doctor workload",
        tables=("Appointment", "Patient", "Employee"),
        roles=("admin", "staff"),
        icon="CalendarCheck",
        starters=(
            "How many appointments did we have each month this year?",
            "Which doctors saw the most patients last month?",
            "What share of appointments were follow-up visits last quarter?",
        ),
        # appointments
        compare_starters=(
            "Appointments by branch this month",
            "Monthly appointments by branch this year",
        ),
        
    ),
    Scope(
        key="services",
        label="Services Prescribed",
        description="Lab tests, procedures and other services, with quantity and cost",
        tables=("ServicePrescription", "Appointment", "Patient", "Employee"),
        icon="Stethoscope",
        starters=(
            "What are the top 10 services by revenue this year?",
            "How has monthly service revenue changed over the last 6 months?",
            "Which facility performed the most lab tests last month?",
        ),
        # services
        compare_starters=("Service revenue by branch this month",),
    ),
    Scope(
        key="medicines",
        label="Medicines",
        description="Medicines prescribed, by type, manufacturer and status",
        tables=("MedicinePrescription", "Appointment", "Patient"),
        icon="Pill",
        starters=(
            "What are the 10 most prescribed medicines this month?",
            "How many medicine lines were prescribed per week last month?",
            "Which manufacturers' medicines are prescribed most often?",
        ),
        # medicines
        compare_starters=("Medicine prescriptions by branch this month",),
    ),
    Scope(
        key="patients",
        label="Patients",
        description="Registrations, demographics, nationality and visit frequency",
        tables=("Patient", "Appointment"),
        roles=("admin", "staff"),
        icon="Users",
        starters=(
            "How many new patients registered each month this year?",
            "What is the gender split of patients seen last month?",
            "Which nationalities visit us most often?",
        ),
        # patients
        compare_starters=("New patients by branch this year",),
    ),
    Scope(
        key="doctors",
        label="Doctors & Departments",
        description="Doctor and department activity and workload",
        tables=("Employee", "Appointment"),
        icon="Building2",
        starters=(
            "How many appointments did each department handle last month?",
            "Which doctors had the most cancelled appointments this year?",
        ),
        # doctors
        compare_starters=("Appointments by department this month",),
    ),
    Scope(
        key="complaints",
        label="Complaints",
        description="Patient complaints recorded during visits",
        tables=("Complaints", "Appointment", "Patient"),
        icon="ClipboardList",
        starters=(
            "What are the most common patient complaints this month?",
            "How have complaints changed month by month this year?",
        ),
        
    ),
]

_BY_KEY = {s.key: s for s in SCOPES}


def get_scope(key: str) -> Scope:
    return _BY_KEY[key]


def _allowed(role: str, scope: Scope) -> bool:
    return role == "superadmin" or role in scope.roles


def scopes_for(role: str) -> List[dict]:
    """Sections a role may use, in display order (what the UI receives)."""
    return [
        {"key": s.key, "label": s.label, "description": s.description,
         "icon": s.icon, "starters": list(s.starters),
         "compare_starters": list(s.compare_starters) if role == "superadmin" else []}
        for s in SCOPES if _allowed(role, s)
    ]


def can_use(role: str, key: str) -> bool:
    return key in _BY_KEY and _allowed(role, _BY_KEY[key])