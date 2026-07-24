# ClinicOps SaaS

[![CI](https://github.com/philgodoy96/clinicops-saas/actions/workflows/ci.yml/badge.svg)](https://github.com/philgodoy96/clinicops-saas/actions/workflows/ci.yml)

ClinicOps SaaS is a production-minded multi-tenant clinic management backend for small clinics and private practices.

The system is designed around secure tenant isolation, role-based access control, invitation-based onboarding, clinic operations, subscription billing, payment webhooks, durable background jobs, and business auditability.

The initial product is API-first and implemented as a modular monolith using FastAPI and PostgreSQL.

---

## Project Status

Status:

```text
Feature-complete for the approved portfolio scope
```

ClinicOps currently has an implemented backend foundation covering:

- identity and authentication;
- tenant isolation and RBAC;
- invitation onboarding;
- membership administration and ownership transfer;
- billing lifecycle;
- webhook ingestion and processing;
- billing reconciliation;
- PostgreSQL-backed durable background jobs;
- worker runtime and billing webhook job execution;
- Docker Compose services for PostgreSQL, API, and worker;
- durable tenant-scoped audit-log persistence;
- domain and worker audit emission;
- tenant-scoped OWNER/ADMIN audit read API;
- tenant-scoped patient records with create, read, list, update, archive, and restore;
- patient optimistic concurrency and tenant-isolated persistence;
- patient lifecycle audit emission;
- tenant-owned professional provider profiles with active/archived lifecycle;
- professional optimistic concurrency and optional one-to-one Membership association;
- tenant-safe professional search and keyset pagination;
- transactional professional audit emission;
- professional preservation during Membership removal.

Durable Audit Logs are complete, including transactionally coupled recording, idempotent worker replay, and authorized tenant-scoped read access.

Patients Domain is complete for the approved tenant-scoped clinical-record boundary: staff-managed patient records, keyset listing and search, optimistic updates, archive and restore, and transactionally coupled audit events. Patients are not platform users and do not authenticate.

Professionals Domain is complete for the approved tenant-scoped provider-profile boundary: tenant-owned provider profiles, active/archived lifecycle, optimistic concurrency, optional one-to-one Membership association, tenant-safe search and keyset pagination, transactional audit, and preservation of Professionals during Membership removal. Frontend, scheduling, credential verification, and provider portal work remain intentionally deferred.

The approved portfolio scope is complete. Future work consists of intentional extensions and maintenance. No additional domain is required for the current release.

The product context, system design, directory structure principles, and engineering standards are documented.

---

## Business Context

Small clinics often manage patients, appointments, staff access, and billing through disconnected tools.

This creates risks such as:

- fragmented operational data;
- unclear staff permissions;
- limited traceability;
- manual subscription management;
- inconsistent payment processing;
- lost asynchronous work;
- accidental cross-tenant data access.

ClinicOps centralizes these concerns in a single backend platform with explicit ownership, authorization, lifecycle rules, transactional workflows, and operational traceability.

---

## Implemented Capabilities

ClinicOps implements:

- global identities and first-party authentication;
- persisted authentication sessions and refresh-token rotation;
- tenants and Membership-based RBAC;
- invitation onboarding;
- membership administration and ownership transfer;
- patient management;
- professional provider profiles;
- subscription lifecycle;
- fake payment-provider boundary;
- signed webhook ingestion;
- durable webhook storage, deduplication, and asynchronous processing;
- PostgreSQL-backed jobs and worker runtime;
- retries, stale-claim recovery, and dead-letter behavior;
- durable audit logs and authorized tenant audit reads;
- request IDs, correlation IDs, and structured logging;
- Docker Compose and GitHub Actions.

---

## Core Actors

### Tenant Owner

The member ultimately responsible for the clinic account, membership administration, billing, and ownership transfer.

Each tenant has exactly one active owner.

### Tenant Admin

Manages operational clinic data and non-owner memberships within the configured authorization policy.

### Staff

Performs patient and Professional operations according to tenant permissions.

### Fake Payment Provider

Simulates an external payment provider and sends signed webhook events for local and automated testing.

### Patient

A tenant-owned operational record.

Patients are not authenticated platform users in v1.

### Professional

A tenant-owned clinical provider profile.

Professionals are operational clinic records, intentionally separate from Memberships, and are not authenticated platform users in v1.

### Future extension: Platform Admin

Platform administration is intentionally deferred. When introduced, a Platform Admin would operate the SaaS platform, inspect tenant status, and perform approved platform-level actions, separate from tenant membership and without implicit access to clinic operational data.

---

## Multi-Tenant Model

Users are global platform identities.

Memberships connect users to tenants.

A user may belong to multiple tenants and may hold a different role in each one.

Conceptually:

```text
User
  |
  +-- Membership: Clinic Alpha / owner
  |
  +-- Membership: Clinic Beta / staff
```

Authentication is global.

Authorization is tenant-scoped.

Every tenant-owned database query must include tenant context.

A valid access token does not grant access to a tenant without an active membership and sufficient permission.

---

## Tenant Ownership

Every tenant must have exactly one active owner.

The owner cannot:

- remove their own membership;
- leave the tenant directly;
- be removed by another tenant member;
- be downgraded through a generic role update.

Ownership can only change through an explicit transactional transfer to another active member of the same tenant.

Invitations cannot assign ownership.

---

## Patient Data Ownership

Each patient record belongs to exactly one tenant.

The same real-world person may have independent patient records in different clinics.

ClinicOps does not create a shared cross-tenant patient identity in v1.

This preserves clear data ownership and prevents cross-tenant privacy leakage.

---

## Architecture

ClinicOps uses a modular monolith.

Current runtime topology:

```text
API Client
    |
    v
FastAPI API Process
    |
    +------------------+
    |                  |
    v                  v
PostgreSQL <----- Worker Process
```

The API and worker are separate processes built from the same codebase and the same Docker image. They share PostgreSQL as the source of truth for:

- application data;
- membership state;
- billing state;
- payment events;
- background job coordination.

---

## Implemented Modules

```text
identity
authentication
authorization
tenancy
invitations
billing
jobs
audit
patients
professionals
```

Each module owns its models, persistence behavior, application workflows, and business rules.

Cross-module changes use explicit services or narrow interfaces.

---

## Technology Stack

Core technologies:

- Python
- FastAPI
- PostgreSQL
- SQLAlchemy
- Alembic
- Pydantic
- Argon2id
- JWT access tokens
- hashed refresh tokens
- pytest
- httpx
- Docker Compose
- GitHub Actions
- structured logging

Additional dependencies are introduced only when they have a clear architectural purpose.

---

## Reliability and Security Focus

The project explicitly addresses:

- tenant isolation;
- authentication and authorization separation;
- secure password hashing;
- hashed invitation and refresh tokens;
- transactional ownership transfer;
- webhook HMAC validation;
- durable webhook deduplication;
- delayed and duplicate payment events;
- concurrent background workers;
- retries and dead jobs;
- transactionally coupled append-only audit records;
- idempotent audit recording for at-least-once workflows;
- sensitive data handling;
- request and workflow traceability.

---

## Background Jobs

ClinicOps uses a PostgreSQL-backed job queue for durable asynchronous work. No external message broker is required for the current architecture.

The implemented queue and worker runtime provide:

- durable enqueueing;
- worker-safe acquisition with `FOR UPDATE SKIP LOCKED`;
- sequential one-job-at-a-time execution per worker process;
- horizontal scaling through multiple worker processes;
- retry scheduling with exponential backoff and jitter;
- stale-claim recovery;
- claim-token ownership;
- graceful shutdown;
- correlation ID propagation.

Delivery follows at-least-once semantics. Handlers that may repeat side effects must be idempotent.

Detailed queue and worker architecture lives in [Background Jobs](docs/architecture/background-jobs.md).

---

## Billing and Webhooks

The implemented billing integration uses a fake payment provider for local and automated testing.

The current foundation covers:

- subscription persistence and lifecycle transitions;
- signed webhook ingestion;
- raw-body HMAC verification;
- durable webhook storage and duplicate handling;
- atomic webhook-event persistence and processing-job enqueueing;
- asynchronous `billing.webhook.process` execution through the worker;
- webhook processing with provider-state ordering;
- billing reconciliation.

Authenticated provider deliveries persist a durable event, enqueue one semantic processing job in the same HTTP transaction, and return `202 Accepted`. The worker claims the job and applies billing state through the existing processing service.

Provider-specific payloads are translated before entering the billing domain. Periodic subscription reconciliation scheduling remains intentionally deferred.

---

## Audit Logs and Application Logs

ClinicOps distinguishes business audit records from operational application logs.

Durable Audit Logs provide tenant-scoped, append-only `AuditLogEntry` persistence with transactionally coupled recording, safe metadata normalization, optional internal idempotency keys for at-least-once workflows, domain and worker emission, and a tenant-scoped OWNER/ADMIN read API. Domain services record audit entries in the same local transaction as the corresponding business mutation so that audit persistence failure prevents the mutation from committing and rolled-back mutations leave no durable audit row. Worker webhook replay reuses equivalent semantic audit facts without claiming exactly-once execution.

Structured application logs already record operational request behavior, including request and correlation identifiers.

Examples:

```text
request_started
request_finished
webhook_signature_invalid
database_error
```

Audit records and application logs serve different purposes and are stored and reviewed separately. Detailed audit architecture lives in [Durable Audit Logs](docs/architecture/audit-logs.md).

---

## Patients Domain

ClinicOps treats a Patient as a tenant-scoped clinical record managed by tenant members.

OWNER, ADMIN, and STAFF may create, read, list, and update active patients. OWNER and ADMIN may archive and restore patients. STAFF cannot archive or restore. No hard-delete endpoint exists. Archived patients remain readable and are read-only until restored.

Patient mutations use optimistic concurrency through an explicit `expected_version`. Cross-tenant access is rejected as not found. Audit events record lifecycle changes without patient PII in metadata.

Detailed design lives in [Patients Domain](docs/architecture/patients.md).

---

## Professionals Domain

ClinicOps treats a Professional as a tenant-owned clinical provider profile managed by tenant members.

OWNER and ADMIN may create, update, archive, restore, and link or unlink Memberships. OWNER, ADMIN, and STAFF may read and list. No hard-delete endpoint exists. Archived professionals remain readable and are read-only until restored.

Professionals support an active/archived lifecycle, optimistic concurrency through an explicit `expected_version`, optional one-to-one Membership association, tenant-safe search and keyset pagination, and transactionally coupled audit events. Removing a Membership unlinks any associated Professional without deleting the provider profile.

Frontend workflows, appointment scheduling, credential verification, and provider portal work remain intentionally deferred.

Unit and integration coverage includes persistence constraints, tenant isolation, lifecycle and concurrency, Membership linking and removal, audit atomicity, search and keyset listing, authorization, and authenticated API workflows.

Detailed design lives in [Professionals Domain](docs/architecture/professionals-domain.md). API contracts live in [Professionals API](docs/api/professionals.md).

---

## Documentation

- [Project Context](docs/architecture/project-context.md)
- [System Design](docs/architecture/system-design.md)
- [Directory Structure Principles](docs/architecture/directory-structure.md)
- [Implementation Plan](docs/implementation-plan.md)
- [Engineering Guide](ENGINEERING_GUIDE.md)
- [Local Development Setup](docs/development/local-setup.md)
- [Request Tracing Model](docs/architecture/request-tracing.md)
- [Global Identity Model](docs/architecture/global-identity.md)
- [Tenant and Membership Model](docs/architecture/tenant-membership.md)
- [Invitation Lifecycle](docs/architecture/invitation-lifecycle.md)
- [Authentication Sessions](docs/architecture/authentication-sessions.md)
- [Tenant Authorization](docs/architecture/tenant-authorization.md)
- [HTTP API Transport Model](docs/architecture/http-api-transport.md)
- [Tenant API Authorization Model](docs/architecture/tenant-api-authorization.md)
- [Tenant Onboarding Model](docs/architecture/tenant-onboarding.md)
- [Membership Administration Model](docs/architecture/membership-administration.md)
- [Billing Lifecycle](docs/architecture/billing-lifecycle.md)
- [Background Jobs](docs/architecture/background-jobs.md)
- [Durable Audit Logs](docs/architecture/audit-logs.md)
- [Patients Domain](docs/architecture/patients.md)
- [Professionals Domain](docs/architecture/professionals-domain.md)
- [Professionals API](docs/api/professionals.md)
- [Security](docs/architecture/security.md)

Additional ADRs may be introduced when future changes create decisions with durable architectural consequences.

---

## Completed Delivery Scope

The approved portfolio release covers:

- application foundation: Python project configuration, FastAPI bootstrap, PostgreSQL development environment, Alembic, testing foundation, and continuous integration;
- global identity, first-party authentication, persisted sessions, and refresh-token rotation;
- tenants, Membership-based RBAC, invitation onboarding, membership administration, and ownership transfer;
- tenant-scoped patient management with optimistic concurrency, archive and restore, and lifecycle audit;
- tenant-owned professional provider profiles with active/archived lifecycle, optional Membership association, search and keyset pagination, and transactional audit;
- subscription lifecycle, fake payment-provider boundary, signed webhook ingestion, durable webhook storage and deduplication, and asynchronous webhook processing;
- PostgreSQL-backed jobs and worker runtime with retries, stale-claim recovery, and dead-letter behavior;
- durable audit logs with authorized tenant-scoped reads;
- request IDs, correlation IDs, structured logging, Docker Compose, and GitHub Actions.

---

## Intentionally Deferred

The following capabilities are intentionally deferred from the current release:

- appointments, availability, reminders, and calendar integration;
- invoice and payment-record models beyond the implemented subscription boundary;
- entitlements, refunds, credits, and proration;
- platform administration;
- real email delivery;
- real payment-provider adapters;
- frontend, patient portal, and provider portal;
- OAuth providers;
- cross-tenant patient or Professional identity;
- custom tenant roles;
- infrastructure expansion such as external brokers, microservices, Kubernetes, and infrastructure as code.

The current release prioritizes backend correctness, tenant isolation, authorization, billing reliability, webhook security, durable background processing, and auditability.

---

## Repository Standards

The repository uses focused branches, reviewable commits, automated tests, database migrations, and documented architectural trade-offs.

Pull Requests describe:

- what changed;
- why it changed;
- how to test it;
- relevant trade-offs;
- follow-up work.

The Git history is intended to preserve the system's technical evolution in small, coherent changes.
