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

```text
Application Foundation
    |
Request Context and Logging
    |
Identity Foundation
    |
Authentication and Session Lifecycle
    |
Tenant and Membership Foundation
    |
Tenant Authorization and Ownership
    |
Audit Log Foundation
    |
Invitation Onboarding
    |
PostgreSQL Job Queue
    |
Fake Email Delivery
    |
Patient Management
    |
Appointment Lifecycle
    |
Appointment Reminder Jobs
    |
Billing Foundation
    |
Fake Payment Provider and Webhook Security
    |
Idempotent Payment Processing
    |
Billing Recovery and Tenant Suspension
    |
Platform Administration
    |
Operational Hardening
    |
Release Documentation
```

Some milestones could be developed independently in a larger team.

The initial sequence remains mostly linear because several modules depend on shared identity, tenant, audit, job, and billing foundations.

---

## 4. Milestone 1 — Application Foundation

### Objective

Establish a minimal, runnable, testable FastAPI application with PostgreSQL development infrastructure and continuous integration.

### Primary Deliverables

- Python project configuration;
- `src/` package layout;
- FastAPI application bootstrap;
- health endpoint;
- validated environment configuration;
- SQLAlchemy engine and session setup;
- PostgreSQL through Docker Compose;
- Alembic initialization;
- baseline structured logging;
- pytest foundation;
- GitHub Actions workflow;
- `.env.example`;
- common development commands.

### Engineering Risks

- application startup coupled too tightly to local infrastructure;
- hidden schema creation during startup;
- configuration access spread across modules;
- local and CI environments behaving differently;
- excessive scaffolding before business behavior exists.

### Test Focus

- application startup;
- health endpoint response;
- configuration validation;
- database connectivity;
- initial migration execution;
- formatting, linting, and test execution in CI.

### Completion Criteria

- the API runs locally;
- PostgreSQL runs through Docker Compose;
- Alembic can upgrade a clean database;
- the test suite runs locally and in CI;
- no business module is implemented prematurely.

---

## 5. Milestone 2 — Request Context and Logging

### Objective

Introduce structured operational logging, request identifiers, correlation identifiers, and consistent error responses.

### Primary Deliverables

- request context middleware;
- request ID generation;
- correlation ID propagation;
- structured request start and finish logs;
- application exception hierarchy;
- centralized HTTP error mapping;
- response identifier headers;
- context propagation utilities.

### Engineering Risks

- request context leaking between concurrent executions;
- secrets or sensitive data entering logs;
- inconsistent public error responses;
- untrusted identifier values accepted without validation;
- middleware obscuring exceptions.

### Test Focus

- each request receives a request ID;
- correlation IDs follow the defined propagation policy;
- concurrent requests retain isolated context;
- structured logs contain expected fields;
- unexpected failures produce generic public responses.

### Completion Criteria

- every HTTP request is traceable;
- identifiers are available to later audit and job workflows;
- application errors follow a consistent public format.

---

## 6. Milestone 3 — Identity Foundation

### Objective

Implement global user identities and secure password handling.

### Primary Deliverables

- User model;
- user status;
- globally unique normalized email;
- `PasswordHasher` abstraction;
- Argon2id implementation;
- user repository;
- registration workflow;
- database migration;
- unit and integration tests.

### Engineering Risks

- inconsistent email normalization;
- raw password exposure;
- duplicate user identities;
- direct password-library usage throughout the codebase;
- user records incorrectly containing tenant roles.

### Test Focus

- normalized email uniqueness;
- password hashing and verification;
- raw passwords never persisted;
- duplicate registration rejection;
- inactive user behavior.

### Completion Criteria

- users can be registered safely;
- identity remains global;
- password handling is isolated behind a stable abstraction;
- user records contain no tenant membership state.

---

## 7. Milestone 4 — Authentication and Session Lifecycle

### Objective

Implement global login, short-lived access tokens, hashed refresh tokens, refresh, logout, and current-user authentication.

### Primary Deliverables

- access-token encoding and validation;
- refresh-token persistence;
- refresh-token hashing;
- login endpoint;
- refresh endpoint;
- logout endpoint;
- current-user endpoint;
- authentication dependencies;
- expiration and revocation behavior.

### Engineering Risks

- raw refresh-token storage;
- refresh-token replay;
- authentication confused with tenant authorization;
- security details exposed through error responses;
- tenant roles embedded as authoritative token claims.

### Test Focus

- successful login;
- invalid credentials;
- access-token expiration;
- refresh-token hashing;
- logout revocation;
- revoked token reuse;
- inactive user rejection;
- current-user resolution.

### Completion Criteria

- users authenticate globally;
- access tokens identify the user;
- refresh tokens can be revoked;
- authentication alone does not grant tenant access.

### Deferred Hardening

Refresh-token rotation may be introduced after the initial lifecycle is stable if it can be added without obscuring the core session model.

---

## 8. Milestone 5 — Tenant and Membership Foundation

### Objective

Implement tenants, memberships, tenant creation, membership lookup, and the exactly-one-owner invariant.

### Primary Deliverables

- Tenant model;
- Membership model;
- tenant lifecycle status;
- membership lifecycle status;
- owner, admin, and staff roles;
- tenant creation workflow;
- owner membership creation in the same transaction;
- current user's tenant list;
- tenant-scoped membership resolution;
- database constraints.

### Engineering Risks

- tenant created without an owner;
- role stored on User;
- duplicate memberships;
- cross-tenant membership queries;
- unclear tenant route context;
- weak owner invariants.

### Test Focus

- tenant creation produces one owner;
- one user can belong to multiple tenants;
- duplicate membership rejection;
- role differences across tenants;
- membership lookup isolation;
- tenant list response.

### Completion Criteria

- each tenant has exactly one owner;
- a global user can hold different roles across tenants;
- tenant context can be resolved consistently.

---

## 9. Milestone 6 — Tenant Authorization and Ownership

### Objective

Implement centralized tenant permissions, membership management, suspended-tenant behavior, and transactional ownership transfer.

### Primary Deliverables

- permission model;
- centralized tenant policies;
- tenant authorization dependency;
- membership role changes;
- membership deactivation rules;
- owner self-removal prevention;
- ownership transfer service;
- suspended-tenant operation policy;
- concurrency-safe record locking.

### Engineering Risks

- scattered role comparisons;
- admin privilege escalation;
- zero or multiple owners;
- concurrent ownership transfers;
- inactive memberships authorizing requests;
- platform and tenant authorization becoming coupled.

### Test Focus

- owner permissions;
- admin restrictions;
- staff restrictions;
- owner removal prevention;
- ownership transfer authorization;
- same-tenant transfer target;
- atomic transfer behavior;
- concurrent transfer attempts.

### Completion Criteria

- tenant authorization is centralized;
- membership state controls access;
- ownership changes only through the explicit transactional workflow;
- the exactly-one-owner invariant survives concurrent requests.

---

## 10. Milestone 7 — Audit Log Foundation

### Objective

Introduce append-only business audit records before broader domain workflows depend on them.

### Primary Deliverables

- AuditLog model;
- actor types;
- audit service;
- tenant and platform audit context;
- request ID and correlation ID capture;
- append-only persistence behavior;
- tenant audit API;
- cursor pagination foundation;
- sensitive metadata policy.

### Engineering Risks

- audit records confused with application logs;
- secrets or excessive patient data stored in metadata;
- audit records modified after creation;
- critical workflows committing without required audit history;
- unstable cursor ordering.

### Test Focus

- audit persistence;
- actor and target context;
- identifier propagation;
- tenant audit isolation;
- append-only behavior;
- stable cursor pagination;
- sensitive field exclusion.

### Completion Criteria

- business workflows can create durable audit records;
- tenant audit history can be queried safely;
- audit records remain distinct from operational logs.

---

## 11. Milestone 8 — Invitation Onboarding

### Objective

Implement secure invitation creation and acceptance for new and existing users.

### Primary Deliverables

- Invitation model;
- secure random token generation;
- token hashing;
- expiration and status;
- invitation authorization policies;
- invitation creation;
- new-user acceptance;
- authenticated existing-user acceptance;
- membership creation;
- audit events.

### Engineering Risks

- raw token persistence;
- invitation reuse;
- invitation email mismatch;
- duplicate membership creation;
- invitation assigning ownership;
- concurrent acceptance.

### Test Focus

- only token hashes are persisted;
- expired invitation rejection;
- accepted invitation rejection;
- target email validation;
- new-user flow;
- existing-user flow;
- concurrent acceptance;
- owner role rejection.

### Completion Criteria

- invitation acceptance creates at most one valid membership;
- both acceptance paths enforce the same invariants;
- ownership cannot be assigned through invitations.

---

## 12. Milestone 9 — PostgreSQL Job Queue

### Objective

Implement durable asynchronous job execution using PostgreSQL.

### Primary Deliverables

- BackgroundJob model;
- job statuses;
- enqueue service;
- worker entry point;
- handler registry;
- `FOR UPDATE SKIP LOCKED` acquisition;
- attempts and maximum attempts;
- exponential backoff;
- jitter;
- retry classification;
- dead-job state;
- stale-running-job recovery policy;
- request and correlation ID propagation.

### Engineering Risks

- two workers claiming the same job;
- duplicate side effects;
- lost jobs;
- infinite retries;
- hidden transaction boundaries;
- non-deterministic retry tests;
- sensitive payload contents.

### Test Focus

- durable enqueue;
- concurrent acquisition;
- retry scheduling;
- maximum attempts;
- dead-job behavior;
- stale-lock recovery;
- correlation propagation;
- idempotency keys where required.

### Completion Criteria

- jobs are durably stored;
- multiple workers coordinate safely;
- failures remain inspectable;
- retry behavior follows explicit policy.

---

## 13. Milestone 10 — Fake Email Delivery

### Objective

Connect invitation workflows to background processing through a replaceable email provider interface.

### Primary Deliverables

- EmailProvider interface;
- FakeEmailProvider;
- invitation delivery handler;
- inspectable local delivery behavior;
- invitation job creation;
- retry-safe delivery policy.

### Engineering Risks

- email delivery occurring inside the HTTP transaction;
- invitation committed without a durable delivery job;
- raw invitation tokens leaking into general logs;
- retries producing uncontrolled duplicate delivery.

### Test Focus

- invitation and job commit together;
- worker invokes the provider abstraction;
- retry behavior;
- duplicate execution policy;
- local delivery inspection.

### Completion Criteria

- invitation creation produces durable asynchronous delivery work;
- provider replacement does not require invitation domain changes.

---

## 14. Milestone 11 — Patient Management

### Objective

Implement tenant-scoped patient operations with plan-limit enforcement.

### Primary Deliverables

- Patient model;
- patient lifecycle policy;
- create, list, retrieve, and update workflows;
- tenant-scoped repositories;
- offset pagination;
- authorization policies;
- patient audit events;
- patient entitlement checks.

### Engineering Risks

- cross-tenant patient access;
- patient counts evaluated globally;
- entitlement races;
- sensitive patient information in logs;
- ambiguous deletion behavior.

### Test Focus

- tenant isolation;
- RBAC;
- create and update behavior;
- pagination;
- entitlement enforcement;
- concurrent limit behavior where required;
- audit records;
- lifecycle behavior.

### Completion Criteria

- members manage only patients belonging to their tenant;
- plan limits are enforced consistently;
- patient operations are auditable.

---

## 15. Milestone 12 — Appointment Lifecycle

### Objective

Implement tenant-scoped appointment scheduling and explicit state transitions.

### Primary Deliverables

- Appointment model;
- scheduled, cancelled, and completed states;
- create, list, update, cancel, and complete workflows;
- patient ownership validation;
- professional membership assignment where included;
- explicit lifecycle rules;
- audit events;
- offset pagination.

### Engineering Risks

- cross-tenant patient association;
- cross-tenant professional assignment;
- arbitrary status updates;
- terminal appointment mutation;
- inconsistent timestamp handling.

### Test Focus

- tenant isolation;
- same-tenant patient invariant;
- same-tenant professional invariant;
- valid transitions;
- invalid transitions;
- RBAC;
- audit records;
- terminal-state behavior.

### Completion Criteria

- appointment state cannot be changed arbitrarily;
- related patient and professional records share tenant ownership;
- operational actions remain traceable.

---

## 16. Milestone 13 — Appointment Reminder Jobs

### Objective

Connect eligible appointments to entitlement-aware reminder jobs.

### Primary Deliverables

- appointment reminder job type;
- reminder scheduling;
- plan entitlement evaluation;
- fake email reminder delivery;
- duplicate reminder prevention;
- cancellation behavior;
- rescheduling behavior.

### Engineering Risks

- reminder sent after cancellation;
- duplicate reminders;
- stale reminders after rescheduling;
- reminders enabled for unsupported plans;
- sensitive data in job payloads.

### Test Focus

- entitlement behavior;
- reminder scheduling;
- cancellation handling;
- rescheduling handling;
- idempotent execution;
- retry behavior;
- correlation propagation.

### Completion Criteria

- eligible appointments create durable reminder work;
- cancelled or rescheduled appointments do not produce incorrect reminders.

---

## 17. Milestone 14 — Billing Foundation

### Objective

Implement plans, subscriptions, invoices, entitlements, and explicit billing state transitions.

### Primary Deliverables

- Plan model;
- Subscription model;
- Invoice model;
- default plan definitions;
- user and patient limits;
- reminder entitlement;
- subscription lifecycle;
- invoice lifecycle;
- owner billing API;
- audit events.

### Engineering Risks

- unrestricted status changes;
- inconsistent tenant ownership;
- multiple unintended active subscriptions;
- floating-point monetary values;
- entitlement behavior scattered across modules.

### Test Focus

- plan creation or seeding;
- subscription invariants;
- invoice transitions;
- invalid transition rejection;
- owner-only billing access;
- entitlement resolution;
- tenant isolation;
- monetary precision.

### Completion Criteria

- billing entities follow explicit lifecycle rules;
- entitlements are resolved centrally;
- tenant owners can inspect billing state safely.

---

## 18. Milestone 15 — Fake Payment Provider and Webhook Security

### Objective

Implement the fake payment provider boundary and authenticated webhook ingestion.

### Primary Deliverables

- PaymentProvider interface;
- FakePaymentProvider;
- provider event schema;
- webhook endpoint;
- exact raw-body access;
- HMAC signing and verification;
- replay-window policy;
- signed webhook simulation script;
- invalid-signature operational logs.

### Engineering Risks

- verifying parsed JSON rather than raw bytes;
- insecure signature comparison;
- secret leakage;
- business processing before verification;
- provider schemas leaking into Billing.

### Test Focus

- valid signature;
- invalid signature;
- modified body;
- stale timestamp;
- malformed event;
- no business effect from rejected requests;
- provider-to-domain translation.

### Completion Criteria

- only authenticated provider events reach payment event processing;
- local signed webhook simulation is available.

---

## 19. Milestone 16 — Idempotent Payment Processing

### Objective

Process payment events transactionally without duplicating billing effects.

### Primary Deliverables

- PaymentEvent model;
- unique provider event identifier;
- event processing states;
- duplicate detection;
- invoice transitions;
- subscription transitions;
- delayed-event policy;
- audit events;
- payment receipt jobs;
- cursor-paginated event inspection.

### Engineering Risks

- duplicate invoice payment;
- duplicate audit records;
- duplicate background jobs;
- delayed events reverting newer state;
- partial processing;
- ambiguous retry behavior after rollback.

### Test Focus

- first delivery;
- sequential duplicate delivery;
- concurrent duplicate delivery;
- delayed events;
- invalid state transitions;
- transaction rollback;
- exactly one billing effect;
- exactly one related job where required.

### Completion Criteria

- provider retries are safe;
- payment processing is traceable across webhook, billing, audit, and jobs;
- duplicates do not repeat side effects.

---

## 20. Milestone 17 — Billing Recovery and Tenant Suspension

### Objective

Implement overdue invoice processing, grace periods, tenant suspension, and billing recovery.

### Primary Deliverables

- overdue invoice job;
- grace-period policy;
- tenant suspension service;
- approved billing recovery operations;
- reactivation behavior;
- owner recovery access;
- audit events;
- idempotent suspension processing.

### Engineering Risks

- suspending the wrong tenant;
- repeated suspension effects;
- blocking recovery access;
- operational writes continuing after suspension;
- payment and suspension races.

### Test Focus

- overdue processing;
- grace-period boundaries;
- idempotent suspension;
- suspended tenant write restrictions;
- owner recovery access;
- payment before or after suspension;
- reactivation;
- audit trace.

### Completion Criteria

- unpaid tenants follow a predictable lifecycle;
- successful recovery restores approved operations safely;
- suspension remains auditable and idempotent.

---

## 21. Milestone 18 — Platform Administration

### Objective

Implement API-only platform operations through a separate authorization boundary.

### Primary Deliverables

- Platform Admin identity strategy;
- platform authorization policy;
- tenant listing and inspection;
- tenant suspension and reactivation endpoints;
- platform audit access;
- job and payment event inspection where appropriate;
- Platform Admin bootstrap script.

### Engineering Risks

- Platform Admin treated as tenant owner;
- implicit patient-data access;
- tenant RBAC reused incorrectly;
- privileged actions missing audit records;
- unsafe bootstrap behavior.

### Test Focus

- normal users rejected from platform endpoints;
- Platform Admin access to approved operations;
- no implicit tenant membership;
- privileged action audit records;
- restricted clinic operational data.

### Completion Criteria

- platform operations use an independent authorization boundary;
- approved platform workflows are available without a frontend;
- Platform Admin access does not bypass tenant data ownership.

---

## 22. Milestone 19 — Operational Hardening

### Objective

Harden the integrated system through failure simulation, concurrency coverage, security review, and operational validation.

### Primary Deliverables

- expanded tenant isolation tests;
- authorization matrix coverage;
- concurrency tests;
- failure simulations;
- idempotency review;
- logging and sensitive-data review;
- migration upgrade review;
- API request examples;
- operational scripts;
- CI coverage improvements;
- health and readiness refinement.

### Engineering Risks

- happy-path-only confidence;
- cross-module transaction defects;
- sensitive logs;
- inconsistent error behavior;
- migrations valid only on empty databases;
- local and CI configuration drift.

### Test Focus

- tenant isolation across all tenant resources;
- authorization boundaries;
- invitation and ownership races;
- webhook duplication;
- payment and suspension races;
- worker crashes and stale locks;
- retry exhaustion;
- audit completeness;
- migration upgrade behavior.

### Completion Criteria

- critical failure modes have explicit automated or documented validation;
- operational behavior is inspectable;
- security and reliability documentation matches the implementation.

---

## 23. Milestone 20 — Release Documentation

### Objective

Prepare the repository as a complete technical artifact and document the implemented v1 system.

### Primary Deliverables

- complete setup instructions;
- architecture overview;
- API request examples;
- environment variable reference;
- migration instructions;
- worker execution instructions;
- testing guide;
- operational behavior;
- documented trade-offs;
- intentionally deferred scope;
- current project status;
- ADR review;
- release notes or v1 milestone.

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
- major decisions and trade-offs are visible and defensible.

---

## 24. Cross-Cutting Quality Requirements

Every implementation milestone must preserve the following qualities.

### Tenant Isolation

Tenant-owned resources must always be accessed through tenant-scoped authorization and tenant-scoped database queries.

### Transaction Safety

Workflows that cannot commit partially must use explicit transactions.

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

## 25. Definition of Done

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

## 26. Roadmap Evolution

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

## 27. Next Milestone

The first implementation milestone is Application Foundation.

It will establish:

- Python project configuration;
- FastAPI bootstrap;
- PostgreSQL development infrastructure;
- Alembic;
- test tooling;
- baseline logging;
- Docker Compose;
- continuous integration.

Business modules will be introduced only after the application foundation is runnable and testable.