CREATE TABLE Employee
(
    Id int IDENTITY(1,1) PRIMARY KEY,
    Name VARCHAR(300),
    Department VARCHAR(100),
    Type VARCHAR(50),
    Gender VARCHAR(20),
    TPS_Id INT
);

CREATE TABLE Patient
(
    Id INT IDENTITY(1,1) PRIMARY KEY,
    Name VARCHAR(300),
    MRNNumber INT,
    RegisteredOn DATE,
    DateOfBirth DATE,
    Gender VARCHAR(20),
    Nationality VARCHAR(300),
    ResidencyType VARCHAR(50),
    TPS_Id INT
);

CREATE TABLE Appointment
(
    Id INT IDENTITY(1,1) PRIMARY KEY,
    VisitDate DATE,
    PatientId INT,
    DoctorId INT,
    VisitStatus VARCHAR(50),
    PatientType VARCHAR(50),
    ParentAppointment INT,
    eRxReferenceNumber VARCHAR(100),
    TPS_Id INT,
    FOREIGN KEY (PatientId) REFERENCES Patient(Id),
    FOREIGN KEY (DoctorId) REFERENCES Employee(Id),
    FOREIGN KEY (ParentAppointment) REFERENcES Appointment(Id)
);

CREATE TABLE Complaints
(
    Id INT IDENTITY(1,1) PRIMARY KEY,
    Complaint VARCHAR(50),
    Appointment INT,
    TPS_Id INT,
    FOREIGN KEY (Appointment) REFERENCES Appointment(Id)
);

CREATE TABLE ServicePrescription
(
    Id INT IDENTITY(1,1) PRIMARY KEY,
    ServiceCode VARCHAR(50),
    ServiceName VARCHAR(300),
    ServiceType VARCHAR(50),
    Quantity INT,
    Cost DECIMAL(18,2),
    Facility VARCHAR(50),
    Appointment INT,
    TPS_Id INT,
    FOREIGN KEY (Appointment) REFERENCES Appointment(Id)
);

CREATE TABLE MedicinePrescription
(
    Id INT IDENTITY(1,1) PRIMARY KEY,
    MedicineCode VARCHAR(50),
    MedicineName VARCHAR(300),
    MedicineType VARCHAR(50),
    Quantity INT,
    UseType VARCHAR(50),
    Facility VARCHAR(50),
    Status VARCHAR(100),
    Manufacturer VARCHAR(300),
    Appointment INT,
    TPS_Id INT,
    FOREIGN KEY (Appointment) REFERENCES Appointment(Id)
);




CREATE TABLE [dbo].[diagnosisPrescription] (
    [Id] INT IDENTITY(1,1) NOT NULL,
    [DiagnosisCode] VARCHAR(50) NULL,
    [DiagnosisName] VARCHAR(300) NULL,
    [DiagnosisType] VARCHAR(50) NULL,
    [AppointmentId] INT NULL,
    [TPS_Id] INT NULL,
    [BranchId] INT NULL,

    PRIMARY KEY CLUSTERED ([Id] ASC)
);
GO

ALTER TABLE [dbo].[diagnosisPrescription]
WITH CHECK ADD FOREIGN KEY ([AppointmentId])
REFERENCES [dbo].[Appointment] ([Id]);
GO

ALTER TABLE [dbo].[diagnosisPrescription]
WITH CHECK ADD CONSTRAINT [FK_Diag_Pres_Branch]
FOREIGN KEY ([BranchId])
REFERENCES [dbo].[BranchMaster] ([BranchId]);
GO


alter table Appointment add BranchId int;
alter table Appointment
add constraint FK_Appointment_Branch
foreign key (BranchId)
references BranchMaster(BranchId);

alter table Complaints add BranchId int;
alter table Complaints
add constraint FK_Complaints_Branch
foreign key (BranchId)
references BranchMaster(BranchId);

alter table Employee add BranchId int;
alter table Employee
add constraint FK_Employee_Branch
foreign key (BranchId)
references BranchMaster(BranchId);

alter table MedicinePrescription add BranchId int;
alter table MedicinePrescription
add constraint FK_Med_Pres_Branch
foreign key (BranchId)
references BranchMaster(BranchId);

alter table Patient add BranchId int;
alter table Patient
add constraint FK_Patient_Branch
foreign key (BranchId)
references BranchMaster(BranchId);

alter table ServicePrescription add BranchId int;
alter table ServicePrescription
add constraint FK_Ser_Pres_Branch
foreign key (BranchId)
references BranchMaster(BranchId);

create table BranchMaster (
BranchId int identity(1,1) primary key,
BranchName varchar(255),
BranchCode varchar(10),
BranchRegion varchar(100),
BranchAddress varchar (255),
HQ_Branch int
);