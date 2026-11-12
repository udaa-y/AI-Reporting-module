/* 002_branches.sql — branch-aware logins (run once, after 001_app_schema.sql)
   Roles:  superadmin  all branches        BranchId NULL
           admin       own branch only     BranchId required
           staff       own branch only     BranchId required */

-- 1. Branch on each login
IF COL_LENGTH(N'app.EmployeeLogin', N'BranchId') IS NULL
    ALTER TABLE app.EmployeeLogin ADD BranchId INT NULL;
GO

-- 2. Allow the new superadmin role
IF OBJECT_ID(N'app.CK_EmployeeLogin_Role', N'C') IS NOT NULL
    ALTER TABLE app.EmployeeLogin DROP CONSTRAINT CK_EmployeeLogin_Role;
ALTER TABLE app.EmployeeLogin ADD CONSTRAINT CK_EmployeeLogin_Role
    CHECK (Role IN (N'superadmin', N'admin', N'staff'));
GO

-- 3. Give your EXISTING logins a branch (or promote one to superadmin), e.g.:
--    UPDATE app.EmployeeLogin SET Role = N'superadmin' WHERE Username = N'admin';
--    UPDATE app.EmployeeLogin SET BranchId = 1 WHERE Username = N'reception';

-- 4. Enforce it: every admin/staff login must belong to a branch.
--    Skipped (with a message) while any admin/staff login still has no branch.
IF OBJECT_ID(N'app.CK_EmployeeLogin_Branch', N'C') IS NULL
BEGIN
    IF EXISTS (SELECT 1 FROM app.EmployeeLogin WHERE Role <> N'superadmin' AND BranchId IS NULL)
        PRINT N'Not enforced yet: some admin/staff logins have no BranchId. Do step 3, then re-run step 4.';
    ELSE
        ALTER TABLE app.EmployeeLogin ADD CONSTRAINT CK_EmployeeLogin_Branch
            CHECK (Role = N'superadmin' OR BranchId IS NOT NULL);
END
GO