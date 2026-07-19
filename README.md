# ClinicOps SaaS

ClinicOps SaaS is a production-minded multi-tenant clinic management backend for small clinics and private practices.

The system is designed around secure tenant isolation, role-based access control, invitation-based onboarding, clinic operations, subscription billing, payment webhooks, durable background jobs, and business auditability.

The initial product is API-first and implemented as a modular monolith using FastAPI and PostgreSQL.

---

## Project Status

Current phase:

```text
Implementation planning
```

The product context, system design, directory structure principles, and engineering standards are documented.

The implementation roadmap defines the planned delivery sequence, engineering risks, test focus, and completion criteria for each major capability.

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

## Core Capabilities

ClinicOps is designed to support:

- global user identities;
- tenant memberships;
- owner, admin, and staff roles;
- invitation-based onboarding;
- patient management;
- appointment scheduling and lifecycle management;
- subscription plans;
- tenant subscriptions;
- invoice lifecycles;
- signed payment webhooks;
- idempotent payment event processing;
- PostgreSQL-backed background jobs;
- retries with exponential backoff and jitter;
- append-only audit logs;
- structured application logs;
- request IDs;
- correlation IDs;
- API-only platform administration.

---

## Core Actors

### Platform Admin

Operates the SaaS platform, inspects tenant status, and performs approved platform-level actions.

Platform administration is separate from tenant membership and does not grant implicit access to clinic operational data.

### Tenant Owner

The member ultimately responsible for the clinic account, membership administration, billing, and ownership transfer.

Each tenant has exactly one active owner.

### Tenant Admin

Manages operational clinic data and non-owner memberships within the configured authorization policy.

### Staff

Performs patient and appointment operations according to tenant permissions.

### Fake Payment Provider

Simulates an external payment provider and sends signed webhook events for local and automated testing.

### Patient

A tenant-owned operational record.

Patients are not authenticated platform users in v1.

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

Initial runtime topology:

```text
API Client
    |
    v
FastAPI API Process
    |
    v
PostgreSQL
    ^
    |
Background Worker Process
```

The API and worker are separate runtime processes built from the same codebase.

PostgreSQL is the source of truth for:

- application data;
- membership state;
- billing state;
- payment events;
- audit records;
- background jobs.

---

## Planned Modules

```text
identity
tenants
invitations
patients
appointments
billing
webhooks
jobs
audit
platform_admin
```

Each module owns its models, persistence behavior, application workflows, and business rules.

Cross-module changes use explicit services or narrow interfaces.

---

## Technology Stack

Planned core technologies:

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

Dependencies will be introduced only when they have a clear architectural purpose.

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
- append-only audit records;
- sensitive data handling;
- request and workflow traceability.

---

## Background Jobs

ClinicOps uses a PostgreSQL-backed job queue.

The queue is designed for:

- durable enqueueing;
- worker-safe acquisition;
- `FOR UPDATE SKIP LOCKED`;
- retry scheduling;
- exponential backoff;
- jitter;
- stale-lock recovery;
- dead-job inspection;
- correlation ID propagation.

Execution follows at-least-once semantics.

Handlers that may repeat side effects must be idempotent.

---

## Billing and Webhooks

The initial billing integration uses a fake payment provider.

This allows the system to exercise:

- signed webhook ingestion;
- raw-body HMAC verification;
- replay protection;
- duplicate delivery;
- idempotent event processing;
- invoice transitions;
- subscription transitions;
- payment audit events;
- asynchronous receipts.

Provider-specific payloads are translated before entering the billing domain.

---

## Audit Logs and Application Logs

Audit logs record business-relevant actions.

Examples:

```text
tenant.created
tenant.ownership_transferred
user.invited
invitation.accepted
patient.created
appointment.cancelled
invoice.paid
tenant.suspended
```

Application logs record operational behavior.

Examples:

```text
request_started
request_finished
webhook_signature_invalid
job_retry_scheduled
database_error
```

Audit records and application logs serve different purposes and are stored and reviewed separately.

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

Additional architecture documents and ADRs will be added as implementation introduces concrete decisions.

---

## Planned Delivery Milestones

### Application Foundation

- Python project configuration;
- FastAPI bootstrap;
- PostgreSQL development environment;
- Alembic;
- testing foundation;
- continuous integration.

### Identity and Tenant Access

- users;
- password hashing;
- authentication;
- refresh tokens;
- tenants;
- memberships;
- RBAC;
- ownership transfer.

### Invitation Onboarding

- secure invitation tokens;
- new-user acceptance;
- existing-user acceptance;
- fake email delivery.

### Clinic Operations

- patient management;
- appointment lifecycle;
- reminder jobs;
- tenant and entitlement enforcement.

### Billing and Payment Events

- plans;
- subscriptions;
- invoices;
- fake payment provider;
- HMAC validation;
- idempotent payment processing;
- tenant suspension and recovery.

### Operational Capabilities

- PostgreSQL-backed worker;
- retry and dead-job behavior;
- audit APIs;
- Platform Admin APIs;
- failure and concurrency hardening.

---

## Intentionally Deferred

The following capabilities are intentionally deferred from v1:

- frontend dashboard;
- patient portal;
- patient authentication;
- OAuth providers;
- real email delivery;
- real payment processing as the primary implementation;
- regional payment integrations;
- cross-tenant patient identity;
- tenant-configurable custom roles;
- microservices;
- external message brokers;
- Kubernetes;
- infrastructure as code;
- full observability platform.

V1 prioritizes backend correctness, tenant isolation, authorization, billing reliability, webhook security, durable background processing, and auditability.

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