# ClinicOps SaaS — Directory Structure Principles

## 1. Purpose

This document defines the directory structure principles for ClinicOps SaaS.

The structure must communicate architectural boundaries, module ownership, testing strategy, runtime responsibilities, and the incremental evolution of the codebase.

The document describes the implemented portfolio-release repository structure and the evolution rules that govern future structural changes. Paths must be introduced only when their corresponding responsibilities exist.

---

## 2. Structural Goals

The project structure must:

- preserve the modular monolith architecture;
- make module ownership explicit;
- keep HTTP concerns separate from business workflows;
- keep persistence logic separate from transport logic;
- support both API and background worker processes;
- make tenant-scoped behavior easy to locate and review;
- support focused testing by responsibility;
- avoid speculative abstractions;
- avoid empty scaffolding for future capabilities;
- remain understandable to engineers reviewing the repository.

The structure should help answer:

```text
Which module owns this behavior?
Where does this business rule belong?
Where is tenant isolation enforced?
Which layer coordinates this workflow?
How is this behavior tested?
```

---

## 3. Implemented Repository Structure

The repository follows a top-level domain package layout under `src/clinicops` rather than a nested `modules/` tree.

```text
clinicops-saas/
├── .github/
│   └── workflows/
│
├── docs/
│   ├── api/
│   ├── architecture/
│   └── development/
│
├── migrations/
│
├── src/
│   └── clinicops/
│       ├── api/
│       │   └── v1/
│       │       ├── authentication/
│       │       ├── billing/
│       │       ├── invitations/
│       │       ├── tenants/
│       │       ├── audit_logs.py
│       │       ├── health.py
│       │       ├── patients.py
│       │       └── professionals.py
│       ├── authentication/
│       ├── authorization/
│       ├── billing/
│       │   ├── jobs/
│       │   ├── providers/
│       │   ├── repositories/
│       │   ├── services/
│       │   └── webhooks/
│       ├── cli/
│       ├── core/
│       ├── db/
│       ├── identity/
│       ├── invitations/
│       ├── jobs/
│       │   ├── repositories/
│       │   ├── runtime/
│       │   └── services/
│       ├── audit/
│       │   ├── repositories/
│       │   └── services/
│       ├── patients/
│       │   ├── repositories/
│       │   └── services/
│       ├── professionals/
│       │   ├── repositories/
│       │   └── services/
│       ├── tenancy/
│       ├── main.py
│       └── worker.py
│
├── tests/
│   ├── integration/
│   └── unit/
│
├── .dockerignore
├── .env.example
├── .gitignore
├── alembic.ini
├── compose.yml
├── Dockerfile
├── ENGINEERING_GUIDE.md
├── pyproject.toml
└── README.md
```

This tree reflects the feature-complete portfolio-release layout. It includes the
durable background-job queue package, worker runtime entry point, billing webhook
job handler, Durable Audit Logs package, Patients package, Professionals
package, their tenant-scoped HTTP routes, Dockerfile, and Compose `api` and
`worker` services.

Directories and files must be introduced only when their corresponding
responsibilities exist.

Appointments and platform administration remain intentionally deferred and are
not represented as required packages.

---

## 4. Incremental Scaffold Rule

Structure changes must follow concrete responsibility.

New directories require actual code or documentation ownership. Empty scaffolding
is avoided.

When a structural change is warranted, define:

```text
Affected modules
Proposed directory structure
File responsibilities
Allowed dependency direction
Files intentionally deferred
Expected future evolution
```

Module internals do not need identical depth.

A small module may use a compact structure.

A larger module with multiple entities and workflows may use separate packages
for APIs, models, schemas, repositories, and services.

The structure should follow actual complexity rather than a universal template.

---

## 5. Root-Level Directories

### `.github/`

Contains repository automation and continuous integration workflows.

Expected responsibilities include:

- formatting checks;
- linting;
- static analysis;
- unit tests;
- integration tests;
- migration validation.

Repository automation belongs at the root because it governs the full engineering workflow.

---

### `docs/`

Contains public technical documentation.

```text
docs/
├── api/
├── architecture/
└── development/
```

#### `docs/architecture/`

Contains durable explanations of the system.

Examples:

- project context;
- system design;
- tenant isolation;
- authentication and authorization;
- billing and webhook processing;
- background job queue and worker execution;
- durable audit logs;
- patients domain;
- professionals domain.

#### `docs/api/`

Contains public API usage documents such as the Professionals API.

OpenAPI remains the generated contract surface. Usage documents complement that
contract with request examples and operational notes.

#### ADRs

Architecture Decision Records may be introduced later when a decision warrants a
durable decision record. An ADR directory is not required for project
completion.

---

### `migrations/`

Contains Alembic migration infrastructure and migration history.

Schema changes must be explicit, reviewable, and committed incrementally.

Application startup must not silently create or alter the production schema.

---

### `tests/`

Contains automated tests grouped by confidence type rather than mirroring source files mechanically.

Tests are organized under `tests/unit/` and `tests/integration/`. High-risk
security boundaries are currently exercised inside those suites rather than in
a separate top-level security directory.

Optional future organization such as a dedicated `tests/security/` tree or a
`tests/factories/` package may be introduced only when a concrete need
justifies the split.

---

### Optional root utilities

A `scripts/` directory or root `Makefile` may be added later as optional
organizational choices when operational or development utilities have concrete
ownership. They are not required paths for the portfolio release.

---

## 6. Application Entry Points

### `main.py`

Creates and configures the FastAPI application.

Responsibilities include:

- application initialization;
- router registration;
- middleware registration;
- exception handler registration;
- startup and shutdown behavior;
- API metadata.

It must not contain business workflows.

---

### `worker.py`

Entry point for the background worker process.

Responsibilities include:

- configuration loading;
- database session-factory setup;
- explicit job-handler registry initialization;
- worker identity resolution;
- SIGINT and SIGTERM shutdown handling;
- sequential worker loop startup.

It must not duplicate business logic from application services.

Start the process with:

```text
python -m clinicops.worker
```

The durable queue foundation lives under `src/clinicops/jobs/`. The worker
composition root lives in `src/clinicops/worker.py`.

---

## 7. Top-Level Application Packages

### `api/`

Contains application-wide HTTP composition.

Possible responsibilities include:

- root router composition;
- API version registration;
- shared HTTP dependencies;
- centralized error mapping.

Module-specific routes remain inside their owning modules.

---

### `cli`

Contains explicit operational transport boundaries that compose application
services outside HTTP and worker processes.

CLI modules own their outer database transaction but do not reimplement
domain or application rules. They are executed manually and are not imported
as application-startup side effects.

The initial command,
`python -m clinicops.cli.bootstrap_user`, creates the first local global
identity through `CreateUserService`.

---

### `core/`

Contains cross-cutting technical infrastructure.

Possible files include:

```text
config.py
exceptions.py
logging.py
request_context.py
security.py
time.py
```

`core/` must remain small.

Business rules must not be placed in `core/`.

---

### `db/`

Contains shared SQLAlchemy infrastructure.

Possible files include:

```text
base.py
session.py
naming_conventions.py
types.py
```

Entity models remain owned by their modules.

---

### Domain packages

Business and platform capabilities live as top-level packages under `src/clinicops`.

Implemented packages:

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

The `jobs` package owns the durable PostgreSQL-backed queue foundation and the
worker runtime composition helpers under `jobs/runtime/`. Billing owns the
`billing.webhook.process` handler and typed job payload under `billing/jobs/`.
The `audit` package owns Durable Audit Log persistence, contracts, metadata
normalization, recording context, idempotent recording, cursor transport, read
policy, public schemas, and tenant-scoped query services. Domain packages emit
audit facts through `AuditRecorder`, and `src/clinicops/api/v1/audit_logs.py`
exposes the tenant-scoped read API. The `patients` package owns tenant-scoped
patient persistence, lifecycle workflows, validation, cursor pagination,
contracts, public schemas, and audit emission, with HTTP composition under
`src/clinicops/api/v1/patients.py`. The `professionals` package owns
tenant-scoped professional persistence, lifecycle and membership-link
workflows, validation, cursor pagination, contracts, public schemas, and
domain-specific audit command builders, with HTTP composition under
`src/clinicops/api/v1/professionals.py`.

Each package owns its business rules, persistence behavior, application services, and public interfaces.

Appointments and platform administration remain intentionally deferred and are not required packages.

---

### Provider boundaries

Outbound external-system boundaries remain owned by the integrating package.

Billing currently colocates its payment-provider contracts and fake adapter under
`src/clinicops/billing/providers/`.

Email delivery is intentionally deferred beyond the current release and must not
be introduced as speculative scaffolding.

Provider contracts and implementations must remain separate from core domain
state transitions.

---

## 8. Module Internal Structure

A module with multiple sub-responsibilities may use:

```text
module/
├── api/
├── models/
├── schemas/
├── repositories/
├── services/
├── policies.py
└── exceptions.py
```

Not every module must contain every package or file.

Each artifact must exist because it has a concrete responsibility.

---

### `api/`

Contains FastAPI route definitions and module-specific HTTP dependencies.

Route handlers should:

- validate transport input;
- resolve authentication and authorization dependencies;
- invoke application services;
- return response schemas.

Route handlers should not:

- coordinate complex workflows;
- contain state machines;
- perform unrestricted database mutations;
- contain duplicated authorization logic.

---

### `models/`

Contains SQLAlchemy models owned by the module.

Models represent persistent state and database relationships.

Business workflows should not be implemented as arbitrary model mutations.

---

### `schemas/`

Contains Pydantic transport schemas.

Examples include:

- request payloads;
- response payloads;
- pagination responses;
- public projections.

Transport schemas should not become substitutes for domain workflow design.

---

### `repositories/`

Contains persistence operations.

Responsibilities include:

- tenant-scoped queries;
- explicit row-locking queries;
- persistence operations;
- database projections.

Repositories must not:

- decide actor permissions;
- import FastAPI;
- return HTTP-specific errors;
- hide transaction commits.

Repositories must still enforce tenant scoping in tenant-owned queries.

---

### `services/`

Contains application workflows and transaction coordination.

Examples include:

```text
create_tenant.py
transfer_ownership.py
accept_invitation.py
process_payment_webhook.py
```

Services coordinate repositories, policies, providers, audit records, and background jobs.

They must remain independent of HTTP request and response objects.

---

### `policies.py`

Contains centralized role and permission rules when a dedicated policy file improves clarity.

Examples include:

- who may invite members;
- who may manage billing;
- who may transfer ownership;
- which operations are allowed for suspended tenants.

---

### `exceptions.py`

Contains explicit failures owned by the module.

Examples include:

```text
InvitationExpiredError
MembershipAlreadyExistsError
OwnershipTransferNotAllowedError
```

Explicit exceptions make workflow behavior easier to test and reason about.

---

## 9. Example Tenant Package Structure

The tenancy package has multiple entities and workflows, so it may justify a package-oriented structure.

```text
tenancy/
├── models.py
├── exceptions.py
├── names.py
├── repository.py
├── query_repository.py
├── membership_administration_repository.py
└── services/
    ├── create_tenant.py
    ├── transfer_ownership.py
    ├── membership_administration.py
    ├── change_membership_role.py
    ├── enable_membership.py
    ├── disable_membership.py
    ├── remove_membership.py
    └── queries.py
```

Exact filenames may evolve with demonstrated complexity. The example above reflects the repository's current tenancy layout rather than a speculative nested template.

`Owner` is not a separate model.

Ownership is represented by a membership holding the owner role.

Ownership-specific behavior belongs in services and policies.

---

## Billing Package

The implemented billing package uses the current top-level domain layout under
`src/clinicops`.

```text
src/clinicops/billing/
├── __init__.py
├── catalog.py
├── enums.py
├── exceptions.py
├── fingerprints.py
├── idempotency_keys.py
├── models.py
├── reconciliation.py
├── state_machine.py
├── providers/
│   ├── __init__.py
│   ├── base.py
│   ├── contracts.py
│   ├── control.py
│   ├── exceptions.py
│   ├── fake.py
│   ├── idempotency.py
│   └── periods.py
├── repositories/
│   ├── __init__.py
│   ├── billing_customer_repository.py
│   ├── subscription_repository.py
│   ├── provider_operation_repository.py
│   └── billing_webhook_event_repository.py
├── services/
│   ├── __init__.py
│   ├── create_subscription.py
│   ├── get_subscription.py
│   ├── schedule_plan_change.py
│   └── schedule_cancellation.py
├── webhooks/
│   ├── __init__.py
│   ├── contracts.py
│   ├── enqueue_processing_job.py
│   ├── handlers.py
│   ├── ingest.py
│   ├── process.py
│   └── signatures.py
└── jobs/
    ├── __init__.py
    ├── constants.py
    ├── payloads.py
    └── process_billing_webhook_event.py
```

The implemented HTTP composition for billing lives under the repository's
versioned API package:

```text
src/clinicops/api/v1/billing/
├── __init__.py
├── dependencies.py
├── routes.py
├── schemas.py
└── webhooks.py
```

Responsibilities are separated by concrete architectural boundary:

```text
catalog.py
    -> server-owned plans, billing intervals, prices, and provider mappings

enums.py
    -> stable billing lifecycle and persistence identifiers

exceptions.py
    -> billing-domain application errors

fingerprints.py
    -> deterministic command fingerprints

idempotency_keys.py
    -> validation of opaque client idempotency keys

state_machine.py
    -> pure subscription lifecycle rules

models.py
    -> SQLAlchemy billing persistence models and PostgreSQL enum bindings

reconciliation.py
    -> billing reconciliation against provider state

repositories/
    -> billing persistence queries, row locks, flushes, and known-conflict
       translation

providers/base.py
    -> synchronous PaymentProvider Protocol

providers/contracts.py
    -> immutable provider request and result contracts

providers/control.py
    -> thread-safe scripted outcomes for local reliability tests

providers/exceptions.py
    -> provider boundary failures and retryability contracts

providers/fake.py
    -> deterministic thread-safe in-memory payment-provider adapter

providers/idempotency.py
    -> provider operation keys and provider request fingerprints

providers/periods.py
    -> calendar-accurate monthly and yearly period calculation

billing/services/
    -> subscription creation, read, scheduled plan change, and scheduled
       cancellation orchestration

billing/webhooks/
    -> webhook authentication, durable ingestion, processing-job enqueueing,
       and processing

billing/jobs/
    -> typed billing.webhook.process payload and worker handler

api/v1/billing/
    -> tenant-scoped billing HTTP composition
```

Billing currently uses one `models.py` file because the mapped entities
belong to the same billing persistence boundary and remain manageable
together.

The repository package is justified by distinct persistence
responsibilities:

```text
BillingCustomer
Subscription
ProviderOperation
BillingWebhookEvent
```

The provider package is colocated with billing because its contracts are
specific to the billing lifecycle and the persisted
`ProviderOperationType` values.

`PaymentProvider` defines the application-facing boundary.

`FakePaymentProvider` is an adapter for deterministic local development and
reliability testing. It is not a real payment integration.

Fake-provider controls remain outside the request contracts so production
application code cannot depend on test-only failure flags.

The provider package does not access SQLAlchemy sessions or billing
repositories. Database transaction orchestration belongs to billing
application services.

Billing application services that call the provider intentionally own
multiple commits because provider calls must occur without an open database
transaction.

The orchestrator reserves and claims durable provider operations, commits,
calls the provider, and then reloads and locks persisted state before applying
the result.

The API package remains under `api/v1/` because this is the repository's
implemented HTTP composition convention.

The payment provider remains application-scoped because the deterministic
fake adapter stores in-memory idempotency and ambiguous-outcome state across
requests.

Billing is complete for its approved synchronous, local-provider, and
asynchronous webhook-job scope.
It does not currently contain:

```text
worker-backed reconciliation scheduling
real provider adapters
entitlement enforcement
invoice lifecycles
```

The durable background-job queue and worker runtime exist under
`src/clinicops/jobs/` and `src/clinicops/worker.py`. Billing webhook processing
runs through the registered `billing.webhook.process` handler. Periodic
reconciliation scheduling, real provider adapters, entitlements, and invoices
remain intentionally deferred beyond the current release.

Billing models are registered centrally through:

```text
src/clinicops/db/models.py
```

Alembic uses the shared metadata exported from that module.

The current implementation follows the repository's actual top-level domain
layout:

```text
src/clinicops/billing/
```

It does not introduce a parallel `modules/billing/` tree.

It does not introduce a parallel `src/clinicops/billing/api/` tree.

---

## Jobs Package

The implemented jobs package provides the durable PostgreSQL-backed queue
foundation and worker runtime under `src/clinicops`.

```text
src/clinicops/jobs/
├── contracts.py
├── enums.py
├── exceptions.py
├── models.py
├── retry.py
├── repositories/
│   └── background_job_repository.py
├── services/
│   ├── enqueue_background_job.py
│   ├── claim_background_jobs.py
│   ├── complete_background_job.py
│   ├── fail_background_job.py
│   └── recover_stale_background_jobs.py
└── runtime/
    ├── handler.py
    ├── identity.py
    ├── registry.py
    ├── exceptions.py
    └── worker.py
```

The process entry point remains:

```text
src/clinicops/worker.py
```

Capability-level ownership:

```text
models.py
    -> BackgroundJob persistence and lifecycle constraints

enums.py
    -> stable job lifecycle identifiers

contracts.py
    -> typed enqueue, claim, completion, failure, and recovery inputs

exceptions.py
    -> queue-foundation application errors

retry.py
    -> capped exponential backoff with equal jitter

repositories/
    -> availability selection, claim locking, and persistence flushes

services/
    -> idempotent enqueueing, concurrent claiming, completion, failure,
       and stale-processing recovery

runtime/
    -> handler protocol, explicit registry, worker identity, and sequential
       execution loop

worker.py
    -> process composition root and signal-aware entrypoint
```

Intentionally deferred under jobs:

```text
recurring scheduler
job administration API
manual replay API
metrics or tracing backend integration
external message broker
```

Detailed queue and worker semantics live in
`docs/architecture/background-jobs.md`.

Jobs models are registered centrally through:

```text
src/clinicops/db/models.py
```

---

## Audit Package

The implemented audit package provides Durable Audit Logs under `src/clinicops`.

```text
src/clinicops/
├── api/v1/
│   └── audit_logs.py
└── audit/
    ├── actions.py
    ├── context.py
    ├── contracts.py
    ├── cursor.py
    ├── enums.py
    ├── exceptions.py
    ├── metadata.py
    ├── models.py
    ├── policies.py
    ├── recording.py
    ├── schemas.py
    ├── repositories/
    │   └── audit_log_repository.py
    └── services/
        ├── list_audit_logs.py
        └── record_audit_log.py
```

Supporting tests and schema migration:

```text
tests/
├── unit/audit/
└── integration/audit/

docs/architecture/audit-logs.md
migrations/versions/0009_add_audit_log_entries.py
```

Capability-level ownership:

```text
actions.py
    -> stable audit action and resource-type catalogs

context.py
    -> AuditRecordingContext attribution helpers

cursor.py
    -> opaque URL-safe Base64 cursor encode and decode

policies.py
    -> OWNER/ADMIN audit-read authorization

recording.py
    -> AuditRecorder and SqlAlchemyAuditRecorder composition

schemas.py
    -> public read response contracts omitting idempotency_key

models.py
    -> AuditLogEntry persistence and database-generated recorded_at

enums.py
    -> actor types and audit sources

contracts.py
    -> typed actor, command, and recorded-entry contracts

exceptions.py
    -> audit application errors

metadata.py
    -> bounded, deterministic, JSON-native metadata normalization

repositories/
    -> insert-only persistence, tenant-scoped lookup, and cursor pagination

services/
    -> RecordAuditLogService and ListAuditLogsService

api/v1/audit_logs.py
    -> tenant-scoped GET /tenants/{tenant_id}/audit-logs route
```

Domain integration points:

```text
tenancy services
    -> tenant create, ownership transfer, membership role change, membership removal

invitation services
    -> issue, accept, revoke

billing services
    -> subscription create, plan change, cancellation

billing webhook processing and worker handler
    -> processed and ignored outcomes with worker system attribution

patients services
    -> create, update, archive, and restore

professionals services
    -> create, update, archive, restore, membership link and unlink
```

Intentionally deferred under audit:

```text
export and retention jobs
platform-wide audit history
metadata search
actor and date-range filters
frontend audit screens
```

Detailed audit semantics live in `docs/architecture/audit-logs.md`.

Audit models are registered centrally through:

```text
src/clinicops/db/models.py
```

---

## Patients Package

The implemented patients package provides tenant-scoped patient records under
`src/clinicops`.

```text
src/clinicops/
├── api/v1/
│   └── patients.py
└── patients/
    ├── contracts.py
    ├── cursor.py
    ├── enums.py
    ├── exceptions.py
    ├── models.py
    ├── schemas.py
    ├── validation.py
    ├── repositories/
    │   └── patient_repository.py
    └── services/
        ├── archive_patient.py
        ├── create_patient.py
        ├── get_patient.py
        ├── list_patients.py
        ├── restore_patient.py
        └── update_patient.py
```

Supporting tests and schema migration:

```text
tests/
├── unit/patients/
└── integration/patients/

docs/architecture/patients.md
migrations/versions/0010_add_patients.py
```

Capability-level ownership:

```text
models.py
    -> Patient persistence and optimistic concurrency version

enums.py
    -> stable patient lifecycle status identifiers

contracts.py
    -> typed create, update, archive, restore, and list inputs

exceptions.py
    -> patient-domain application errors

validation.py
    -> shared field constraints and uniqueness helpers

cursor.py
    -> opaque URL-safe Base64 cursor encode and decode

schemas.py
    -> public request and response transport contracts

repositories/
    -> tenant-scoped persistence, row locks, and cursor pagination

services/
    -> create, get, list, update, archive, and restore orchestration

api/v1/patients.py
    -> tenant-scoped patients HTTP routes
```

Intentionally deferred under patients:

```text
appointments and medical-record workflows
patient portal access
field-level encryption
bulk import or export
global or cross-tenant patient identity
```

Detailed patient semantics live in `docs/architecture/patients.md`.

Patient models are registered centrally through:

```text
src/clinicops/db/models.py
```

---

## Professionals Package

The implemented professionals package provides tenant-scoped professional
profiles under `src/clinicops`.

```text
src/clinicops/professionals/
├── __init__.py
├── audit.py
├── contracts.py
├── cursor.py
├── enums.py
├── exceptions.py
├── models.py
├── schemas.py
├── validation.py
├── repositories/
│   ├── __init__.py
│   └── professional_repository.py
└── services/
    ├── __init__.py
    ├── archive_professional.py
    ├── create_professional.py
    ├── get_professional.py
    ├── link_professional_membership.py
    ├── list_professionals.py
    ├── restore_professional.py
    ├── unlink_professional_for_membership_removal.py
    ├── unlink_professional_membership.py
    └── update_professional.py
```

HTTP composition and schema migration:

```text
src/clinicops/api/v1/professionals.py
migrations/versions/0011_add_professionals.py
```

Supporting tests and architecture documentation:

```text
tests/
├── unit/professionals/
└── integration/professionals/

docs/architecture/professionals-domain.md
```

Capability-level ownership:

```text
contracts.py / validation.py
    -> domain contracts and shared field constraints

models.py / repositories/
    -> persistence and guarded mutations with tenant scoping and row locks

services/
    -> create, get, list, update, archive, restore, membership link and
       unlink orchestration and transaction boundaries

schemas.py / api/v1/professionals.py
    -> public request and response transport contracts and tenant-scoped
       professionals HTTP routes

audit.py
    -> domain-specific audit command builders for professional lifecycle
       and membership association facts

enums.py
    -> stable professional lifecycle status identifiers

exceptions.py
    -> professional-domain application errors

cursor.py
    -> opaque URL-safe Base64 cursor encode and decode
```

Intentionally deferred under professionals:

```text
appointments and scheduling workflows
payroll and compensation
external credential verification
bulk import or export
global or cross-tenant professional identity
```

Detailed professional semantics live in
`docs/architecture/professionals-domain.md`.

Professional models are registered centrally through:

```text
src/clinicops/db/models.py
```

---

## 10. Naming Guidance

Use entity-oriented names for persistent models and transport schemas.

Examples:

```text
models/tenant.py
models/membership.py
schemas/invitation.py
schemas/patient.py
```

Use use-case-oriented names for application services.

Examples:

```text
services/create_tenant.py
services/transfer_ownership.py
services/accept_invitation.py
services/process_payment_webhook.py
```

Use repository names that communicate owned persistence.

Examples:

```text
repositories/tenant_repository.py
repositories/membership_repository.py
repositories/billing_webhook_event_repository.py
```

Avoid vague filenames such as:

```text
helpers.py
utils.py
manager.py
common.py
```

unless their responsibility is explicit and narrow.

---

## 11. Dependency Direction

The intended dependency direction is:

```text
HTTP API
    |
    v
Application Services
    |
    v
Repositories, policies, domain rules, and provider interfaces
    |
    v
Database and external-provider implementations
```

Rules:

1. Repositories must not import FastAPI.
2. Services must not depend on HTTP request or response objects.
3. Route handlers must not coordinate complex persistence workflows.
4. Provider implementations must not decide billing transitions.
5. Shared infrastructure must not import business modules.
6. Core infrastructure must not depend on module APIs.
7. One module must not arbitrarily mutate another module's owned models.
8. Cross-module workflows must use explicit services or narrow interfaces.
9. Worker handlers must call application services rather than HTTP routes.
10. Tests may cross boundaries intentionally to validate integrated behavior.

---

## 12. Testing Structure

### `tests/unit/`

Validates isolated business and security rules.

Examples:

- password hashing;
- ownership-transfer validation;
- invitation expiration;
- subscription lifecycle rules;
- retry calculations;
- HMAC verification;
- RBAC policy decisions;
- cross-tenant authorization rejection at the unit boundary.

Unit tests are organized by owning package under `tests/unit/` (for example
`api/`, `audit/`, `authentication/`, `authorization/`, `billing/`, `identity/`,
`invitations/`, `jobs/`, `patients/`, `professionals/`, and `tenancy/`).

---

### `tests/integration/`

Validates PostgreSQL and FastAPI behavior.

Examples:

- login and token refresh;
- invitation acceptance transactions;
- tenant-scoped patient and professional access;
- duplicate webhook handling;
- background job enqueue, claim, completion, failure, concurrency, and
  worker-runtime behavior;
- end-to-end billing webhook job execution;
- audit persistence, idempotent recording, tenant isolation, concurrency, and
  rollback coupling;
- cross-tenant access rejection and revoked-token behavior at the HTTP boundary.

Integration tests are organized by owning package under `tests/integration/`
(for example `api/`, `audit/`, `authentication/`, `authorization/`, `billing/`,
`identity/`, `invitations/`, `jobs/`, `patients/`, `professionals/`, and
`tenancy/`).

High-risk security boundaries are currently tested inside these unit and
integration suites. Dedicated `tests/security/` or `tests/factories/`
directories are optional future organizational choices and are not required
paths.

---

## 13. Structures Intentionally Avoided Initially

Do not introduce without a concrete need:

- generic base repositories;
- generic CRUD services;
- universal entity frameworks;
- mediator frameworks;
- internal event buses;
- dependency injection containers;
- custom ORM wrappers;
- a global `utils/` directory;
- multiple architectural layers inside every module;
- empty directories for future capabilities;
- generic abstractions that hide tenant-scoped queries;
- duplicated provider-specific structures inside domain modules.

Explicit code is preferred over speculative abstraction.

---

## 14. Evolution Rule

A file should become a package when:

- it contains several independent workflows;
- navigation becomes difficult;
- different areas evolve independently;
- tests and imports become unclear;
- editing conflicts become frequent;
- the package structure communicates a real domain distinction.

The project must not split files based only on arbitrary line-count limits.

Structure should evolve with demonstrated complexity.