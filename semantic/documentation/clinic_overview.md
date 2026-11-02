# Clinic data overview

This database holds outpatient clinic operations data.

- One **Patient** can have many **Appointments** (visits). `Appointment.PatientId` points to `Patient.Id`.
- `Appointment.DoctorId` points to `Employee.Id`; doctors and other staff are rows in **Employee**.
- Each Appointment can have many **Complaints**, **ServicePrescription** rows (services, lab tests, procedures, with `Quantity` and `Cost`) and **MedicinePrescription** rows.
- `Appointment.ParentAppointment` links a follow-up visit to the original visit.
- Patient age is derived from `Patient.DateOfBirth`, e.g. `DATEDIFF(YEAR, DateOfBirth, GETDATE())`.

<!--
Add your own business rules here in plain English. Vanna is trained on every
.md file in this folder. Useful things to write down:
- What the values in VisitStatus / PatientType / ResidencyType / Status mean
  (e.g. which VisitStatus values count as a completed visit).
- Whether ServicePrescription.Cost is a unit price or a line total.
- What TPS_Id refers to.
- Which visits count as "new" vs "follow-up" patients.
-->
