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
Background Jobs & Worker
Durable Audit Log Foundation
Durable Audit Log Integrations
Patients Domain
```

Completed work includes the FastAPI application, PostgreSQL and Alembic, global identity, authentication sessions with refresh-token rotation, tenant isolation, membership RBAC, invitations, membership administration, ownership transfer, billing customer and subscription persistence, local price catalog, fake payment provider, idempotent provider operations, subscription creation and read APIs, scheduled plan changes, scheduled cancellation, webhook authentication and durable ingestion, webhook processing, provider state ordering, billing reconciliation, concurrency protections, request and correlation identifiers, the PostgreSQL-backed durable job queue, worker runtime, billing webhook job integration, API and worker container services, the Durable Audit Log Foundation, Durable Audit Log Integrations, and Patients Domain.

Billing is complete for its approved synchronous, local-provider, and asynchronous webhook-job boundary. Remaining work does not reopen that boundary as incomplete.

Background Jobs & Worker is complete. Periodic reconciliation scheduling remains intentionally deferred beyond that milestone.

Durable Audit Logs are complete, including foundation persistence, domain and worker emission, and the tenant-scoped OWNER/ADMIN read API.

Patients Domain is complete, including tenant-owned patient persistence, contracts, repository isolation, create/read/list/search, partial optimistic update, archive and restore, FastAPI transport with RBAC, Problem Details, transactionally coupled audit events, authenticated integration coverage, and domain documentation.

### Remaining current-release milestones

```text
Professionals Domain
    ->
Final Hardening
```

Professionals Domain is the active implementation slice.

---

## 4. Completed — Background Jobs & Worker

### Objective

Introduce PostgreSQL-backed durable jobs and a separate worker process that can
claim, execute, retry, and recover asynchronous work safely.

PostgreSQL is the deliberate source of truth for both domain state and queue
coordination. The API and worker are separate processes from the same codebase
and the same Docker image.

### Completed slices

- durable persistence and migration;
- idempotent enqueueing;
- concurrent claiming;
- completion, retries, and dead-lettering;
- stale recovery;
- concurrency and failure testing;
- handler registry;
- worker runtime;
- billing webhook handler;
- atomic webhook ingestion and enqueueing;
- end-to-end billing job execution;
- worker process entrypoint;
- API and worker container services;
- architecture and operational documentation.

### Engineering risks addressed

- two workers claiming the same job;
- duplicate side effects;
- lost jobs;
- infinite retries;
- hidden transaction boundaries;
- non-deterministic retry tests;
- sensitive payload contents.

### Completion criteria met

- jobs are durably stored;
- multiple workers coordinate safely through the worker runtime;
- failures remain inspectable;
- billing webhook processing executes asynchronously through the worker.

Periodic billing subscription reconciliation scheduling remains intentionally
deferred.

---

## 5. Completed — Durable Audit Log Foundation

### Objective

Introduce append-only, tenant-scoped business audit persistence distinct from
application logs, without yet emitting domain events or exposing an audit HTTP
read API.

### Completed slices

- persistence model and migration;
- actor and source contracts;
- metadata validation;
- idempotent recording;
- append-only application surface;
- tenant-scoped repository queries;
- stable cursor pagination;
- concurrency and rollback tests;
- security and architecture documentation.

### Engineering risks addressed

- audit records confused with application logs;
- secrets or non-JSON-native values stored in metadata;
- audit rows updated or deleted through the application surface;
- domain mutations committing without coupled audit persistence;
- rolled-back mutations leaving durable audit rows;
- unscoped audit queries;
- conflicting idempotent replay.

### Completion criteria met

- tenant-scoped `AuditLogEntry` rows persist through migration `0009`;
- `RecordAuditLogService` validates actors, sources, and metadata, then flushes
  without committing;
- equivalent idempotent replay reuses an existing entry and conflicting replay
  is detected;
- repository queries remain tenant-scoped with stable
  `recorded_at DESC, id DESC` ordering;
- concurrency, tenant-isolation, and rollback coupling are covered by tests;
- public architecture and security documentation describe the foundation
  boundary.

---

## 6. Completed — Durable Audit Log Integrations

### Objective

Integrate the Durable Audit Log Foundation into domain workflows and expose
authorized tenant-scoped audit read access.

### Completed slices

- action catalog and resource catalog;
- `AuditRecordingContext` attribution helpers;
- `AuditRecorder` / `SqlAlchemyAuditRecorder` composition;
- opaque cursor transport;
- `ListAuditLogsService` and OWNER/ADMIN read policy;
- tenant-scoped `GET /api/v1/tenants/{tenant_id}/audit-logs` API;
- tenancy integrations for create, ownership transfer, role change, and removal;
- invitation integrations for issue, accept, and revoke;
- billing lifecycle integrations for create, plan change, and cancellation;
- worker webhook integrations for processed and ignored outcomes;
- end-to-end integration, authorization, pagination, rollback, and replay tests;
- architecture and security documentation.

### Engineering risks addressed

- critical workflows committing without required audit history;
- audit emission outside the caller-owned domain transaction;
- unauthorized cross-tenant audit reads;
- resource IDs treated as authorization grants;
- public schemas leaking unsafe metadata or internal idempotency keys;
- worker replay creating duplicate or conflicting audit rows.

### Completion criteria met

- approved domain workflows create durable audit records in the same local
  transaction as the corresponding mutation;
- tenant audit history is queryable through the authorized OWNER/ADMIN HTTP API;
- public invitation acceptance remains system-attributed;
- worker webhook audit emission remains system-attributed with durable
  correlation propagation;
- equivalent idempotent replay reuses one semantic audit fact without claiming
  exactly-once execution;
- deferred concerns such as export, retention jobs, metadata search, and
  platform-wide audit history remain outside this slice.

---

## 7. Completed — Patients Domain

### Objective

Implement tenant-owned patient records as operational clinic data, not platform
authentication identities.

### Completed slices

- patient persistence and migration;
- patient contracts and validation;
- tenant-scoped repository;
- create, read, list, and search;
- partial optimistic update;
- archive and restore;
- FastAPI transport and RBAC;
- Problem Details;
- transactionally coupled audit events;
- authenticated integration coverage for RBAC, isolation, pagination,
  lifecycle, and conflicts;
- domain documentation.

### Engineering risks addressed

- cross-tenant patient access;
- patients treated as Users or Memberships;
- sensitive patient information in logs or audit metadata;
- ambiguous archive or restore behavior;
- lost updates without optimistic concurrency control;
- unauthorized archive or restore by STAFF.

### Completion criteria met

- Patient remains separate from User/Membership;
- tenant isolation enforced;
- no hard delete;
- version conflicts fail with 409;
- STAFF cannot archive/restore;
- audit metadata excludes patient PII.

Patient portal access, patient authentication, global identity conflation,
field-level encryption, bulk import or export, and frontend surfaces remain
intentional scope decisions rather than shortcomings of this milestone.

---

## 8. Milestone 1 — Professionals Domain

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

## 9. Milestone 2 — Final Hardening & Portfolio Release

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

## 10. Intentionally Deferred Extensions

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
- periodic billing subscription reconciliation scheduling;
- complex reactivation or undo-cancellation workflows.

Appointments remain a natural future extension after Patients and Professionals establish tenant-owned operational boundaries.

---

## 11. Cross-Cutting Quality Requirements

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

## 12. Definition of Done

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

## 13. Roadmap Evolution

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

## 14. Next Milestone

The next implementation milestone is Professionals Domain.

Final Hardening follows.
