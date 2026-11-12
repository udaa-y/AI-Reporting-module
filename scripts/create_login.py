"""
scripts/create_login.py
────────────────────────
Create (or reset the password of) an Ask AI / dashboard login.

  python scripts/create_login.py --username admin --name "Clinic Admin" --role admin
  python scripts/create_login.py --username reception1 --name "Front Desk" --role staff --employee-id 42

The password is asked for interactively (never pass it on the command line).
Run from the project root, with the same .env the app uses.
"""

import argparse
import datetime as dt
import getpass
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import func, insert, select, update  # noqa: E402

from app.db.app_tables import employee_login  # noqa: E402
from app.db.sqlserver import get_db_session  # noqa: E402
from app.security.auth import ROLES, hash_password  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--username", required=True)
    parser.add_argument("--name", required=True, help="Display name, e.g. 'Dr. Aisha Khan'")
    parser.add_argument("--role", required=True, choices=ROLES)
    parser.add_argument("--employee-id", type=int, default=None, help="Optional dbo.Employee.Id")
    parser.add_argument("--branch-id", type=int, default=None,
                        help="dbo.BranchMaster.BranchId (required for admin and staff)")
    args = parser.parse_args()
    if args.role != "superadmin" and args.branch_id is None:
        sys.exit("--branch-id is required for admin and staff logins.")

    password = getpass.getpass("Password: ")
    if len(password) < 8:
        sys.exit("Password must be at least 8 characters.")
    if password != getpass.getpass("Repeat password: "):
        sys.exit("Passwords do not match.")

    username = args.username.strip().lower()
    values = dict(DisplayName=args.name, Role=args.role, EmployeeId=args.employee_id,
                  PasswordHash=hash_password(password), IsActive=True, BranchId=None if args.role == "superadmin" else args.branch_id)
    with get_db_session() as s:
        existing = s.execute(select(employee_login.c.Id)
                             .where(func.lower(employee_login.c.Username) == username)).first()
        if existing:
            s.execute(update(employee_login).where(employee_login.c.Id == existing[0]).values(**values))
            print(f"Updated login '{username}' ({args.role}).")
        else:
            s.execute(insert(employee_login).values(
                Username=username, CreatedAt=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None, microsecond=0), **values))
            print(f"Created login '{username}' ({args.role}).")


if __name__ == "__main__":
    main()
