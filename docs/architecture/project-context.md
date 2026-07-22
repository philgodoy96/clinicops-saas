# ClinicOps SaaS — Project Context

## 1. Project Overview

ClinicOps SaaS is a production-minded multi-tenant clinic management platform designed for small clinics and private practices.

The platform is designed around patient management, professional profiles, team access, subscription billing, payment-provider integration, durable background execution, and business auditability.

ClinicOps is not designed as a tutorial CRUD application. Its purpose is to demonstrate how a backend SaaS platform can enforce tenant isolation, authorization boundaries, transactional consistency, billing reliability, asynchronous processing, and operational traceability.

The initial product is API-first. A frontend may be introduced later, but the backend must remain complete, testable, and understandable without one.

---

## 2. Business Problem

Small clinics frequently operate through disconnected tools such as spreadsheets, messaging applications, calendars, and manually maintained financial records.

This creates several operational risks:

* patient and staff operational data becomes fragmented;
* staff access is difficult to control;
* user actions are not traceable;
* subscription billing requires manual intervention;
* duplicate or delayed payment notifications may create inconsistent billing state;
* failed operational tasks may be lost without retries;
* clinic data may be exposed if tenant isolation is implemented incorrectly.

ClinicOps addresses these risks by providing a centralized SaaS backend with explicit ownership, access control, billing state management, background processing, and audit records.

The product is not merely a patient registry.

It combines two distinct domains:

### Clinic Operations Domain

* patients;
* professionals;
* clinic staff access;
* operational permissions.

Appointment scheduling remains a natural future extension of this domain and is intentionally deferred beyond the current release.

### SaaS Platform Domain

* users;
* tenants;
* memberships;
* invitations;
* authentication;
* billing;
* payment webhooks;
* background jobs;
* audit logs.

These domains coexist in the same system but have different responsibilities and data ownership boundaries.

Platform administration is a separate authorization concern and is intentionally deferred beyond the current release.

---

## 3. Core Actors

### 3.1 Tenant Owner

The Tenant Owner is the member ultimately responsible for a clinic account.

Responsibilities include:

* managing the clinic account;
* managing subscription billing;
* inviting and managing tenant members;
* assigning supported tenant roles;
* managing patients and professionals;
* viewing tenant audit history once durable audit access is available;
* transferring tenant ownership.

Each tenant must have exactly one active owner.

The owner cannot:

* remove their own membership;
* leave the tenant while still being the owner;
* be removed by another tenant member;
* be downgraded through a generic role update;
* assign ownership through an invitation.

Ownership changes only through an explicit ownership-transfer workflow.

---

### 3.2 Tenant Admin

A Tenant Admin manages operational clinic workflows and tenant members within defined limits.

Responsibilities may include:

* inviting admins or staff;
* managing non-owner memberships;
* managing patients;
* managing professionals;
* viewing tenant operational audit history once durable audit access is available.

A Tenant Admin cannot:

* transfer ownership;
* remove or downgrade the owner;
* become owner through a generic role update;
* manage tenant billing unless explicitly permitted by a future policy;
* perform platform-level administration.

---

### 3.3 Staff

Staff members perform daily clinic operations.

Responsibilities include:

* viewing and managing patients;
* viewing and managing professionals according to tenant permissions.

Staff cannot:

* manage billing;
* manage tenant ownership;
* manage memberships;
* create invitations;
* access platform administration.

The `staff` role is intentionally broad.

Clinical positions such as doctor, receptionist, nurse, or assistant are not represented as separate authorization roles in the current release. More granular permissions may be introduced later if product requirements justify the additional policy complexity.

---

### 3.4 Fake Payment Provider

The Fake Payment Provider simulates an external billing provider.

It supports signed webhook events containing:

* provider event identifiers;
* event types;
* timestamps;
* payment payloads;
* HMAC signatures.

The provider may intentionally simulate:

* duplicated events;
* invalid signatures;
* delayed events;
* retries;
* successful and failed payment outcomes.

The Fake Payment Provider allows ClinicOps to exercise realistic billing integration behavior without making a real provider the primary dependency of the current release.

---

### 3.5 Patient

A Patient is a tenant-owned operational record managed by members of a tenant.

A patient is not a platform authentication identity.

A patient is not a User.

A patient is not a Membership.

Patients do not have:

* authentication credentials;
* access tokens;
* refresh tokens;
* tenant memberships;
* direct access to the ClinicOps API.

Each patient record belongs to exactly one tenant and is inaccessible across tenant boundaries.

The same real-world person may have separate patient records in multiple clinics. These records remain independent and must never be linked, exposed, or synchronized across tenants in the current release.

This decision avoids cross-tenant privacy leakage, global patient identity resolution, and ambiguous data ownership.

---

### 3.6 Professional

A Professional is a tenant-owned operational profile.

A professional is distinct from a global User.

A professional is distinct from a Membership.

A professional profile may optionally reference a Membership when the approved design associates clinic access with an operational profile. A professional may also exist without immediate platform access when the approved design permits it.

A Professional is not merely a billing-plan label.

---

## 4. Identity and Membership Model

A `User` represents a global platform identity.

A `Membership` represents the relationship between one user and one tenant.

A `Professional` represents a tenant-owned operational profile that may optionally reference a membership.

A `Patient` represents a tenant-owned operational record with no platform access.

Conceptually:

```text
User
    -> global authentication identity

Membership
    -> tenant access and role

Professional
    -> tenant-owned operational profile
    -> may optionally reference a Membership

Patient
    -> tenant-owned operational record
```

A user may have memberships in multiple tenants.

Example:

```text
User: doctor@example.com

Membership A
- Tenant: Clinic Alpha
- Role: owner

Membership B
- Tenant: Clinic Beta
- Role: staff
```

Authentication is global.

Authorization is tenant-scoped.

The system must evaluate tenant access using:

```text
authenticated user
+ target tenant
+ active membership
+ membership role or permission
```

A valid access token does not automatically grant access to any tenant.

---

## 5. Tenant Ownership Rules

The following invariants govern tenant ownership:

1. Every tenant must have exactly one active owner.
2. The current owner cannot leave the tenant directly.
3. The current owner cannot remove or deactivate their own membership.
4. No tenant member can remove, deactivate, or downgrade the current owner.
5. Ownership cannot be granted through an invitation.
6. Ownership cannot be granted through a generic membership role update.
7. Ownership can only be transferred to an active member of the same tenant.
8. Ownership transfer must promote the target member and demote the current owner atomically.
9. A failed ownership transfer must leave the existing ownership unchanged.

The previous owner becomes an admin after a successful transfer in the current release.

A different post-transfer role may be introduced later if product requirements justify it.

---

## 6. Core Capabilities

ClinicOps is divided into the following business and platform capabilities.

### Implemented foundation

* global user identity and password credentials;
* login and persisted authentication sessions;
* refresh-token rotation and session revocation;
* tenants, memberships, and membership-based RBAC;
* invitation onboarding and membership administration;
* ownership transfer;
* billing customer and subscription persistence;
* local price catalog and fake payment provider;
* idempotent provider operations;
* subscription creation and read APIs;
* scheduled plan changes and scheduled cancellation;
* webhook authentication, durable ingestion, and processing;
* provider state ordering;
* billing reconciliation and concurrency protections;
* request IDs and correlation IDs;
* structured application logs.

### Remaining current-release capabilities

* Background Jobs & Worker;
* Durable Audit Logs;
* Patients Domain;
* Professionals Domain;
* Final Hardening & Portfolio Release.

### Background Jobs

Remaining work includes:

* durable job storage;
* transactional enqueueing;
* safe worker locking;
* retries;
* exponential backoff;
* jitter;
* dead-letter behavior;
* idempotent execution;
* worker runtime and operational integration.

### Audit

Remaining work includes:

* append-only business-relevant event records;
* actor, tenant, resource, and identifier context;
* transactional audit emission;
* secret-redaction policy;
* authorized tenant-scoped read access.

Regular application logs are not a durable audit trail.

### Patients

Remaining work includes:

* tenant-scoped patient records;
* create, read, list, update, and archive or deactivate workflows;
* tenant isolation and permissions;
* validation, indexing, and uniqueness rules;
* audit emission.

### Professionals

Remaining work includes:

* tenant-scoped professional profiles;
* create, read, list, update, and deactivate workflows;
* optional membership association;
* tenant isolation and permissions;
* uniqueness rules;
* audit emission.

---

## 7. Critical Invariants

### Tenant Isolation

A tenant member must never access resources owned by another tenant.

All tenant-owned data access must include tenant context in the database query itself.

### Global User Identity

A user email is globally unique across ClinicOps.

A user does not have a `tenant_id`.

### Membership Uniqueness

A user may have at most one membership per tenant.

### Tenant Authorization

Tenant authorization is based on the user's active membership in the requested tenant.

### Owner Presence

Every tenant must always have exactly one active owner.

### Patient Ownership

Each patient record belongs to exactly one tenant.

Patients do not authenticate into the platform.

### Professional Ownership

Each professional profile belongs to exactly one tenant.

A professional may optionally reference a membership in the same tenant when association is required by the approved design.

### Invitation Security

Raw invitation tokens must never be stored.

Only a secure token hash may be persisted.

### Password Security

Passwords must be hashed using Argon2id through a `PasswordHasher` abstraction.

Raw passwords must never be logged or persisted.

### Refresh Token Security

Refresh tokens must be stored as hashes.

Logout revokes the corresponding refresh token or session.

### Webhook Authenticity

Payment provider events must not enter billing workflows before their HMAC signature is validated.

### Webhook Idempotency

A provider event must never produce billing side effects more than once.

### Job Safety

Background job execution is at least once.

Job handlers that may produce external or repeated side effects must be idempotent.

### Audit Integrity

Audit records are append-only business records.

They must not contain passwords, raw tokens, secrets, or unnecessary sensitive patient information.

---

## 8. Main Failure Modes

ClinicOps must explicitly address the following failures:

* a user attempts to access another tenant's data;
* a user has a valid token but no membership in the requested tenant;
* a membership is inactive;
* an owner attempts to remove themselves;
* an admin attempts to remove the owner;
* two ownership transfers execute concurrently;
* an invitation expires before acceptance;
* an invitation is accepted more than once;
* an existing user accepts an invitation for a different email;
* a refresh token is revoked or reused;
* a webhook signature is invalid;
* the same payment event is delivered repeatedly;
* a payment event arrives after a newer billing event;
* two workers attempt to claim the same background job;
* a worker crashes after claiming a job;
* a job repeatedly fails;
* a tenant becomes suspended during an operational workflow;
* audit creation fails during a critical transaction;
* concurrent writes race against tenant-owned operational limits where such limits exist.

These failures must influence service boundaries, transaction design, locking strategies, tests, and operational logging.

---

## 9. Current Release Scope

The current release includes the implemented SaaS foundation plus the remaining macro-milestones:

* Background Jobs & Worker;
* Durable Audit Logs;
* Patients Domain;
* Professionals Domain;
* Final Hardening & Portfolio Release.

The implemented foundation already covers identity, authentication sessions, tenant isolation, membership RBAC, invitations, membership administration, ownership transfer, and the approved billing and webhook boundary.

Billing is complete for its approved synchronous and local-provider scope. Background Jobs will later operationalize webhook processing and reconciliation through durable asynchronous execution.

---

## 10. Intentionally Deferred Scope

The following capabilities are intentionally deferred beyond the current release:

* Appointments;
* appointment scheduling;
* appointment reminders;
* availability management;
* calendar integration;
* invoices;
* payment records beyond the current subscription lifecycle;
* refunds;
* credits;
* proration;
* entitlements;
* platform administration;
* fake email delivery;
* external email delivery;
* real payment-provider adapters;
* frontend application;
* automatic provider subscription discovery;
* administrative billing reconciliation endpoints;
* complex reactivation or undo-cancellation workflows;
* patient portal and patient authentication;
* Google OAuth and other social login providers;
* cross-tenant patient identity;
* tenant-configurable custom roles;
* Redis-backed job queues;
* external message brokers;
* microservices;
* schema-per-tenant or tenant-per-database isolation;
* Kubernetes and infrastructure-as-code platforms;
* full observability platforms.

These are strategic scope decisions.

Appointments were intentionally deferred because the current project prioritizes multi-tenant SaaS architecture, billing reliability, durable background execution, auditability, and tenant-owned operational data.

The Patient and Professional boundaries preserve a clear path for a future appointment lifecycle without coupling the current release to scheduling complexity.

---

## 11. Portfolio and Interview Trade-offs

### Modular Monolith

A modular monolith provides strong local transactions, simple deployment, and explicit module boundaries without introducing premature distributed-system complexity.

### Global Users and Tenant Memberships

Global users allow one identity to operate across multiple clinics while memberships preserve independent tenant roles and authorization.

### Exactly One Tenant Owner

A single owner provides a clear final authority for billing and account ownership. Explicit transactional ownership transfer prevents invalid zero-owner or multi-owner states.

### Tenant-Scoped Patients and Professionals

Patient records and professional profiles remain independent between tenants to protect privacy and preserve clear data ownership. Patients do not access the platform. Professionals remain distinct from Users and Memberships.

### First-Party Authentication

First-party authentication is implemented before OAuth to exercise password security, token lifecycle, session revocation, tenant authorization, and invitation onboarding.

### Fake Payment Provider First

A fake payment provider allows local, deterministic testing of provider boundaries, HMAC validation, duplicate delivery, retries, and failure handling without making a real provider the primary dependency.

### PostgreSQL-Backed Jobs

A PostgreSQL-backed queue prioritizes transactional reliability, inspectable job state, and low operational complexity.

### Audit Logs and Application Logs

Audit logs explain business-relevant actions. Application logs explain runtime behavior. Keeping them distinct improves incident analysis and compliance-oriented reasoning.

### Request IDs and Correlation IDs

Request IDs identify individual executions. Correlation IDs connect broader workflows across HTTP requests, billing transitions, audit records, and background jobs.

---

## 12. Project Success Criteria

ClinicOps is successful when the project demonstrates that:

* tenant data cannot cross authorization boundaries;
* business invariants remain valid under concurrent operations;
* webhook duplication does not duplicate side effects;
* background jobs can fail and retry safely;
* billing workflows are traceable;
* critical actions are auditable;
* modules have clear ownership;
* commits and Pull Requests document the system's incremental evolution;
* architectural trade-offs can be defended clearly in an engineering interview.
