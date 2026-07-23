# ClinicOps SaaS — Implementation Plan

## 1. Purpose

This document defines the planned delivery sequence for ClinicOps SaaS.

The roadmap organizes the system into incremental engineering milestones so that each capability can be implemented, tested, reviewed, and documented without introducing unrelated scope.

The plan focuses on technical dependencies, observable outcomes, reliability risks, and completion criteria.

Detailed module scaffolds may evolve as implementation exposes concrete complexity, but the architectural boundaries defined in the project documentation remain authoritative.

---

## 2. Delivery Principles

ClinicOps will be delivered through small, coherent changes.

Each implementation milestone should:

- introduce one primary capability;
- preserve a working main branch;
- include the database migration required by the capability;
- include tests proportional to engineering risk;
- update technical documentation when behavior or architecture changes;
- avoid unrelated refactors;
- preserve tenant isolation and authorization boundaries;
- expose observable behavior through an API, worker, migration, script, or test.

The roadmap follows dependency order rather than feature visibility.

Foundational capabilities are implemented before the business workflows that depend on them.

---

## 3. Milestone Overview

### Completed foundation

The following capabilities are already implemented and are not remaining current-release milestones:

```text
Application Foundation
Request Context and Logging
Identity Foundation
Authentication and Session Lifecycle
Tenant and Membership Foundation
Tenant Authorization and Ownership
Invitation Onboarding
Billing Foundation
Fake Payment Provider and Webhook Security
Idempotent Webhook Processing
Billing Reconciliation and Concurrency Protections
```

Completed work includes the FastAPI application, PostgreSQL and Alembic, global identity, authentication sessions with refresh-token rotation, tenant isolation, membership RBAC, invitations, membership administration, ownership transfer, billing customer and subscription persistence, local price catalog, fake payment provider, idempotent provider operations, subscription creation and read APIs, scheduled plan changes, scheduled cancellation, webhook authentication and durable ingestion, webhook processing, provider state ordering, billing reconciliation, concurrency protections, and request and correlation identifiers.

Billing is complete for its approved synchronous and local-provider boundary. Remaining work does not reopen that boundary as incomplete.

### Remaining current-release milestones

```text
Background Jobs & Worker
    |
Durable Audit Logs
    |
Patients Domain
    |
Professionals Domain
    |
Final Hardening & Portfolio Release
```

Background Jobs & Worker remains the active macro-milestone. Its durable queue
foundation is complete; worker runtime and billing integration remain
incomplete.

This documentation-alignment work corrects public project scope. It is not itself a functional milestone.

---

## 4. Milestone 1 — Background Jobs & Worker

### Objective

Introduce PostgreSQL-backed durable jobs and a separate worker process that can
claim, execute, retry, and recover asynchronous work safely.

PostgreSQL is the deliberate source of truth for both domain state and queue
coordination. The durable queue foundation is implemented. The worker runtime
and billing integration remain incomplete within this same macro-milestone.

### Completed foundation

The following queue-foundation slices are implemented:

- `BackgroundJob` persistence and migration `0008_add_background_jobs`;
- PostgreSQL JSONB payloads with payload versioning;
- lifecycle states, database constraints, and partial idempotency-key
  uniqueness;
- idempotent transactional enqueueing with caller-owned commit;
- semantic replay comparison, availability scheduling, and priority ordering;
- safe concurrent claiming with `FOR UPDATE SKIP LOCKED`;
- claim tokens, worker ownership, and processing leases;
- successful completion, retryable failure, and terminal failure handling;
- exponential backoff with equal jitter and a maximum-delay cap;
- dead-letter state and stale-processing recovery;
- bounded and sanitized failure metadata;
- unit tests, PostgreSQL integration tests, and concurrency and failure-path
  tests;
- architecture documentation in `docs/architecture/background-jobs.md`.

### Remaining deliverables

The following slices remain inside Background Jobs & Worker:

- worker composition root and continuous execution loop;
- handler registry;
- transactional webhook-job enqueue integration;
- billing webhook-processing handler;
- reconciliation handler or scheduling decision;
- graceful shutdown;
- worker-specific settings;
- Docker and Compose worker integration;
- end-to-end webhook-to-job processing tests;
- operational worker documentation.

### Engineering Risks

- two workers claiming the same job;
- duplicate side effects;
- lost jobs;
- infinite retries;
- hidden transaction boundaries;
- non-deterministic retry tests;
- sensitive payload contents.

### Test Focus

Completed foundation coverage includes durable enqueue, concurrent
acquisition, retry scheduling, maximum attempts, dead-letter behavior, and
stale-processing recovery.

Remaining coverage must include:

- correlation propagation through the worker runtime;
- idempotent billing job handlers;
- end-to-end webhook-to-job processing.

### Completion Criteria

This macro-milestone is complete when:

- jobs are durably stored;
- multiple workers coordinate safely through the worker runtime;
- failures remain inspectable;
- billing webhook processing and reconciliation can execute asynchronously
  through the worker.

The durable queue foundation alone does not complete this milestone.

---

## 5. Milestone 2 — Durable Audit Logs

### Objective

Introduce append-only, tenant-scoped business audit records distinct from application logs.

### Primary Deliverables

- append-only audit records;
- tenant-scoped audit ownership;
- actor identity;
- action;
- resource type and ID;
- request ID;
- correlation ID;
- safe metadata;
- transactional audit emission;
- secret-redaction policy;
- authorized tenant-scoped read access.

### Engineering Risks

- audit records confused with application logs;
- secrets or excessive patient data stored in metadata;
- audit records modified after creation;
- critical workflows committing without required audit history.

### Test Focus

- audit persistence;
- actor and resource context;
- identifier propagation;
- tenant audit isolation;
- append-only behavior;
- sensitive field exclusion;
- authorized read access.

### Completion Criteria

- business workflows can create durable audit records;
- tenant audit history can be queried safely;
- audit records remain distinct from operational logs.

---

## 6. Milestone 3 — Patients Domain

### Objective

Implement tenant-owned patient records as operational clinic data, not platform authentication identities.

### Primary Deliverables

- patient create, read, list, update, and archive or deactivate workflows;
- tenant isolation;
- permissions;
- validation;
- indexing and uniqueness rules;
- audit emission.

### Engineering Risks

- cross-tenant patient access;
- patients treated as Users or Memberships;
- sensitive patient information in logs or audit metadata;
- ambiguous archive or deactivation behavior.

### Test Focus

- tenant isolation;
- RBAC;
- create, read, list, and update behavior;
- archive or deactivate behavior;
- uniqueness and validation rules;
- audit records.

### Completion Criteria

- members manage only patients belonging to their tenant;
- patients do not authenticate into the platform;
- patient operations are auditable.

---

## 7. Milestone 4 — Professionals Domain

### Objective

Implement tenant-owned professional profiles distinct from global Users and Memberships.

### Primary Deliverables

- professional create, read, list, update, and deactivate workflows;
- tenant isolation;
- optional membership association;
- permissions;
- uniqueness rules;
- audit emission.

### Engineering Risks

- professionals treated as equivalent to Memberships;
- cross-tenant professional access;
- invalid membership associations across tenants;
- uniqueness conflicts within a tenant.

### Test Focus

- tenant isolation;
- RBAC;
- optional membership association rules;
- uniqueness behavior;
- deactivate behavior;
- audit records.

### Completion Criteria

- professionals remain tenant-owned operational profiles;
- optional membership association preserves tenant boundaries;
- professional operations are auditable.

---

## 8. Milestone 5 — Final Hardening & Portfolio Release

### Objective

Stabilize the integrated system and prepare the repository as a coherent portfolio artifact.

### Primary Deliverables

- full test-suite stabilization;
- migration verification;
- index review;
- authorization review;
- failure-path review;
- Docker and Compose validation;
- worker runbook;
- environment-variable documentation;
- README finalization;
- architecture overview;
- API examples;
- project-status cleanup;
- trade-off documentation;
- intentional-deferral documentation;
- ADR review;
- release checklist;
- repository cleanup.

### Engineering Risks

- happy-path-only confidence;
- documentation drift from implemented behavior;
- local and CI configuration drift;
- incomplete failure coverage for jobs, audit, and operational domains.

### Review Focus

- documentation accuracy;
- setup reproducibility;
- implementation and architecture consistency;
- quality of failure-mode coverage;
- clarity of design decisions;
- completeness of repository navigation.

### Completion Criteria

- a new engineer can run and understand the system;
- the implemented scope is accurately documented;
- major decisions, trade-offs, and intentional deferrals are visible and defensible.

---

## 9. Intentionally Deferred Extensions

The following capabilities are intentionally deferred beyond the current release and are not remaining macro-milestones:

- Appointments;
- appointment scheduling;
- appointment reminders;
- availability management;
- calendar integration;
- invoices;
- payment records beyond the current subscription lifecycle;
- refunds;
- credits;
- proration;
- entitlements;
- platform administration;
- fake email delivery;
- external email delivery;
- real payment-provider adapters;
- frontend application;
- automatic provider subscription discovery;
- administrative billing reconciliation endpoints;
- complex reactivation or undo-cancellation workflows.

Appointments remain a natural future extension after Patients and Professionals establish tenant-owned operational boundaries.

---

## 10. Cross-Cutting Quality Requirements

Every implementation milestone must preserve the following qualities.

### Tenant Isolation

Tenant-owned resources must always be accessed through tenant-scoped authorization and tenant-scoped database queries.

### Transaction Safety

Workflows that cannot commit partially must use explicit transactions.

Ordinary repositories do not commit. Ordinary services flush but do not commit. Routes own commits for ordinary HTTP workflows. Explicit multi-transaction provider orchestration may own commits. Provider calls occur outside database locks.

### Idempotency

Webhook processing and retryable background work must tolerate repeated execution safely.

### Auditability

Business-relevant actions must produce durable audit records where required.

### Operational Traceability

Request IDs and correlation IDs must connect logs, audit records, provider events, and background jobs.

### Migration Integrity

Schema changes must be incremental and reproducible against PostgreSQL.

### Documentation Accuracy

Technical documentation must change when implemented behavior or architecture changes.

---

## 11. Definition of Done

A milestone is complete when:

1. the planned capability is implemented;
2. database migrations are included where required;
3. automated tests cover the primary behavior and risks;
4. relevant manual validation is documented or completed;
5. tenant isolation and authorization implications are reviewed;
6. logging and audit behavior are appropriate;
7. documentation reflects the implementation;
8. the repository remains runnable;
9. CI passes;
10. the change is reviewable as a coherent unit.

---

## 12. Roadmap Evolution

This roadmap may evolve as implementation reveals new constraints.

A meaningful roadmap change should document:

- the technical reason for the change;
- affected dependencies;
- whether scope changed;
- whether the change alters architecture or only delivery order.

Roadmap changes should preserve the project's primary goals:

- tenant isolation;
- authorization correctness;
- transactional reliability;
- secure provider integration;
- durable asynchronous execution;
- auditability;
- maintainable module boundaries.

---

## 13. Next Milestone

The next implementation milestone remains Background Jobs & Worker.

The durable PostgreSQL-backed queue foundation is already in place. Remaining
work in this milestone introduces:

- worker composition root and execution loop;
- handler registry;
- transactional webhook-job enqueue integration;
- billing webhook-processing and reconciliation handlers;
- graceful shutdown and worker settings;
- Docker and Compose worker integration;
- end-to-end webhook-to-job processing tests;
- operational worker documentation.

Durable Audit Logs, Patients, Professionals, and Final Hardening follow in that order.
