# ClinicOps SaaS — Project Context

## 1. Project Overview

ClinicOps SaaS is a production-minded multi-tenant clinic management platform designed for small clinics and private practices.

The platform centralizes operational workflows such as patient management, appointment management, team access, subscription billing, payment processing, background jobs, and business auditability.

ClinicOps is not designed as a tutorial CRUD application. Its purpose is to demonstrate how a backend SaaS platform can enforce tenant isolation, authorization boundaries, transactional consistency, billing reliability, asynchronous processing, and operational traceability.

The initial product is API-first. A frontend may be introduced later, but the backend must remain complete, testable, and understandable without one.

---

## 2. Business Problem

Small clinics frequently operate through disconnected tools such as spreadsheets, messaging applications, calendars, and manually maintained financial records.

This creates several operational risks:

* patient and appointment data becomes fragmented;
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
* appointments;
* clinic staff;
* operational permissions.

### SaaS Platform Domain

* users;
* tenants;
* memberships;
* invitations;
* authentication;
* billing;
* payment webhooks;
* background jobs;
* audit logs;
* platform administration.

These domains coexist in the same system but have different responsibilities and data ownership boundaries.

---

## 3. Core Actors

### 3.1 Platform Admin

A Platform Admin operates the ClinicOps SaaS platform.

Responsibilities include:

* listing and inspecting tenants;
* inspecting platform status;
* suspending and reactivating tenants;
* viewing platform-level audit history;
* inspecting billing, webhook, or background-job health where appropriate.

A Platform Admin is not automatically a member of any tenant and does not receive implicit access to clinic operational data.

Platform administration uses a separate authorization boundary from tenant RBAC.

---

### 3.2 Tenant Owner

The Tenant Owner is the member ultimately responsible for a clinic account.

Responsibilities include:

* managing the clinic account;
* managing subscription billing;
* inviting and managing tenant members;
* assigning supported tenant roles;
* managing patients and appointments;
* viewing tenant audit history;
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

### 3.3 Tenant Admin

A Tenant Admin manages operational clinic workflows and tenant members within defined limits.

Responsibilities may include:

* inviting admins or staff;
* managing non-owner memberships;
* managing patients;
* managing appointments;
* viewing tenant operational audit history.

A Tenant Admin cannot:

* transfer ownership;
* remove or downgrade the owner;
* become owner through a generic role update;
* manage tenant billing unless explicitly permitted by a future policy;
* perform platform-level administration.

---

### 3.4 Staff

Staff members perform daily clinic operations.

Responsibilities include:

* viewing and managing patients;
* creating and updating appointments;
* cancelling or completing appointments according to lifecycle rules.

Staff cannot:

* manage billing;
* manage tenant ownership;
* manage memberships;
* create invitations;
* access platform administration.

The v1 `staff` role is intentionally broad.

Clinical positions such as doctor, receptionist, nurse, or assistant are not represented as separate authorization roles in v1. More granular permissions may be introduced later if product requirements justify the additional policy complexity.

---

### 3.5 Fake Payment Provider

The Fake Payment Provider simulates an external billing provider.

It sends signed webhook events containing:

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

The Fake Payment Provider allows ClinicOps to exercise realistic billing integration behavior without making a real provider the primary dependency of v1.

---

### 3.6 Patient

A Patient is an operational subject managed by members of a tenant.

A patient is not a platform user in v1.

Patients do not have:

* authentication credentials;
* access tokens;
* refresh tokens;
* tenant memberships;
* direct access to the ClinicOps API.

Each patient record belongs to exactly one tenant.

The same real-world person may have separate patient records in multiple clinics. These records remain independent and must never be linked, exposed, or synchronized across tenants in v1.

This decision avoids cross-tenant privacy leakage, global patient identity resolution, and ambiguous data ownership.

---

## 4. Identity and Membership Model

A `User` represents a global platform identity.

A `Membership` represents the relationship between one user and one tenant.

Conceptually:

```text
User
- global identity
- globally unique email
- password credentials
- account status

Membership
- user
- tenant
- role
- membership status
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

The previous owner becomes an admin after a successful transfer in v1.

A different post-transfer role may be introduced later if product requirements justify it.

---

## 6. Core Modules

ClinicOps is divided into the following business and platform capabilities.

### Identity

* user registration;
* login;
* password verification;
* access tokens;
* refresh tokens;
* logout;
* current-user identity.

### Tenants

* tenant creation;
* tenant lifecycle;
* memberships;
* tenant ownership;
* ownership transfer;
* tenant role context.

### Authorization

* membership resolution;
* tenant permissions;
* platform-admin permissions;
* tenant-status restrictions.

### Invitations

* invitation creation;
* secure invitation tokens;
* token hashing;
* expiration;
* acceptance;
* membership creation;
* fake email delivery.

### Patients

* tenant-scoped patient records;
* patient creation;
* patient retrieval;
* patient updates;
* patient deactivation or deletion policy;
* plan-limit enforcement.

### Appointments

* appointment creation;
* assignment;
* scheduling;
* update;
* cancellation;
* completion;
* lifecycle validation;
* reminder scheduling.

### Billing

* plans;
* subscriptions;
* invoices;
* billing state transitions;
* tenant entitlement resolution;
* grace-period behavior.

### Webhooks

* raw request handling;
* HMAC validation;
* provider event parsing;
* payment event deduplication;
* billing workflow invocation;
* processing status.

### Background Jobs

* durable job storage;
* safe worker locking;
* retries;
* exponential backoff;
* jitter;
* dead-job state;
* idempotent execution.

### Audit

* immutable business-relevant event records;
* actor context;
* tenant context;
* request and correlation identifiers;
* chronological querying.

### Platform Administration

* tenant inspection;
* tenant suspension and reactivation;
* platform audit access;
* operational status inspection.

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

### Appointment Ownership

An appointment must belong to the same tenant as its patient.

Any assigned professional membership must also belong to the same tenant.

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
* plan limits are exceeded through concurrent requests.

These failures must influence service boundaries, transaction design, locking strategies, tests, and operational logging.

---

## 9. Minimal Realistic V1

ClinicOps v1 includes:

* global email and password authentication;
* Argon2id password hashing;
* short-lived JWT access tokens;
* hashed refresh tokens;
* logout and token revocation;
* global users;
* tenants;
* memberships;
* owner, admin, and staff roles;
* explicit ownership transfer;
* invitation-based onboarding;
* FakeEmailProvider;
* tenant-scoped patients;
* tenant-scoped appointments;
* appointment lifecycle rules;
* subscription plans;
* subscriptions;
* invoices;
* simple plan entitlements;
* FakePaymentProvider;
* HMAC-signed payment webhooks;
* durable payment event records;
* idempotent webhook processing;
* PostgreSQL-backed background jobs;
* retries with exponential backoff and jitter;
* audit logs;
* structured application logs;
* request IDs;
* correlation IDs;
* Platform Admin API;
* offset and cursor pagination;
* Docker Compose;
* Alembic migrations;
* automated tests;
* GitHub Actions;
* professional documentation and Git history.

---

## 10. Intentionally Deferred Scope

The following capabilities are intentionally deferred from v1:

* frontend dashboard;
* patient portal;
* patient authentication;
* Google OAuth;
* other social login providers;
* Supabase Auth;
* real email delivery;
* Stripe as the primary billing provider;
* PIX or boleto payments;
* WhatsApp integration;
* AI features;
* cross-tenant patient identity;
* tenant-configurable custom roles;
* complex analytics;
* Redis-backed job queues;
* external message brokers;
* microservices;
* tenant-per-database isolation;
* Kubernetes;
* Terraform;
* Prometheus;
* Grafana;
* full OpenTelemetry tracing.

These are strategic scope decisions.

V1 prioritizes tenant isolation, authorization, authentication fundamentals, billing correctness, webhook security, durable background processing, and auditability.

---

## 11. Portfolio and Interview Trade-offs

### Modular Monolith

A modular monolith provides strong local transactions, simple deployment, and explicit module boundaries without introducing premature distributed-system complexity.

### Global Users and Tenant Memberships

Global users allow one identity to operate across multiple clinics while memberships preserve independent tenant roles and authorization.

### Exactly One Tenant Owner

A single owner provides a clear final authority for billing and account ownership. Explicit transactional ownership transfer prevents invalid zero-owner or multi-owner states.

### Tenant-Scoped Patient Records

Patient records remain independent between tenants to protect privacy and preserve clear data ownership.

### First-Party Authentication

First-party authentication is implemented before OAuth to exercise password security, token lifecycle, session revocation, tenant authorization, and invitation onboarding.

### Fake Providers First

Fake email and payment providers allow local, deterministic testing of provider boundaries, HMAC validation, duplicate delivery, retries, and failure handling.

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