# ClinicOps SaaS — Directory Structure Principles

## 1. Purpose

This document defines the directory structure principles for ClinicOps SaaS.

The structure must communicate architectural boundaries, module ownership, testing strategy, runtime responsibilities, and the incremental evolution of the codebase.

The directory layout is not a fixed template to be generated upfront. It is a target structure that will evolve as implementation slices introduce concrete capabilities.

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

## 3. High-Level Target Structure

```text
clinicops-saas/
├── .github/
│   └── workflows/
│
├── docs/
│   ├── architecture/
│   ├── adr/
│   └── api/
│
├── migrations/
│
├── scripts/
│
├── src/
│   └── clinicops/
│       ├── api/
│       ├── cli/
│       │   ├── __init__.py
│       │   └── bootstrap_user.py
│       ├── core/
│       ├── db/
│       ├── modules/
│       ├── providers/
│       ├── shared/
│       ├── main.py
│       └── worker.py
│
├── tests/
│   ├── factories/
│   ├── unit/
│   ├── integration/
│   └── security/
│
├── .env.example
├── .gitignore
├── alembic.ini
├── compose.yml
├── Dockerfile
├── Makefile
├── pyproject.toml
└── README.md
```

This is a target structure.

Directories and files must be introduced only when their corresponding responsibilities exist.

---

## 4. Incremental Scaffold Rule

The internal structure of each module must be designed before the implementation slice that introduces it.

Each slice must define:

```text
Affected modules
Proposed directory structure
File responsibilities
Allowed dependency direction
Files intentionally deferred
Expected future evolution
```

Module internals do not need identical depth.

A small module may begin with a compact structure.

A larger module with multiple entities and workflows may begin with separate packages for APIs, models, schemas, repositories, and services.

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
├── architecture/
├── adr/
└── api/
```

#### `docs/architecture/`

Contains durable explanations of the system.

Examples:

- project context;
- system design;
- tenant isolation;
- authentication and authorization;
- billing and webhook processing;
- background job execution.

#### `docs/adr/`

Contains Architecture Decision Records for decisions with meaningful alternatives and long-term consequences.

Examples may include:

```text
0001-use-modular-monolith.md
0002-use-global-user-identities.md
0003-use-postgres-backed-job-queue.md
0004-use-fake-payment-provider-first.md
```

Not every technical choice requires an ADR.

#### `docs/api/`

Contains request examples and API usage notes that complement generated OpenAPI documentation.

---

### `migrations/`

Contains Alembic migration infrastructure and migration history.

Schema changes must be explicit, reviewable, and committed incrementally.

Application startup must not silently create or alter the production schema.

---

### `scripts/`

Contains operational and development utilities.

Examples:

- create the first Platform Admin;
- seed subscription plans;
- simulate signed payment webhooks;
- validate local environment setup.

Scripts should reuse application services where practical and must not duplicate business rules.

---

### `tests/`

Contains automated tests grouped by confidence type rather than mirroring source files mechanically.

The test structure should make critical security and reliability behavior easy to locate.

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

Starts the background worker process.

Responsibilities include:

- configuration loading;
- database setup;
- job registry initialization;
- worker loop startup;
- graceful shutdown.

It must not duplicate business logic from application services.

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

### `modules/`

Contains business and platform capabilities.

Expected modules include:

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

Each module owns its business rules, persistence behavior, application services, and public interfaces.

---

### `providers/`

Contains outbound external-system boundaries.

Expected provider families include:

```text
email
payments
```

Provider contracts and implementations must remain separate from core domain state transitions.

---

### `shared/`

Contains small reusable primitives with no natural module ownership.

Possible files include:

```text
pagination.py
typing.py
transaction.py
```

`shared/` must not become a generic dumping ground.

Module-specific enums, exceptions, and business rules must remain inside their owning modules.

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
create_appointment.py
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
InvalidAppointmentTransitionError
InvoiceTransitionNotAllowedError
```

Explicit exceptions make workflow behavior easier to test and reason about.

---

## 9. Example Tenant Module Structure

The tenant module has multiple entities and workflows, so it may justify a package-oriented structure from the beginning.

```text
modules/
└── tenants/
    ├── api/
    │   ├── __init__.py
    │   ├── tenants.py
    │   ├── memberships.py
    │   └── ownership.py
    │
    ├── models/
    │   ├── __init__.py
    │   ├── tenant.py
    │   └── membership.py
    │
    ├── schemas/
    │   ├── __init__.py
    │   ├── tenant.py
    │   ├── membership.py
    │   └── ownership.py
    │
    ├── repositories/
    │   ├── __init__.py
    │   ├── tenant_repository.py
    │   └── membership_repository.py
    │
    ├── services/
    │   ├── __init__.py
    │   ├── create_tenant.py
    │   ├── transfer_ownership.py
    │   └── manage_membership.py
    │
    ├── policies.py
    └── exceptions.py
```

`Owner` is not a separate model.

Ownership is represented by a membership holding the owner role.

Ownership-specific behavior belongs in services and policies.

---

## Billing Module

The implemented billing module uses the current top-level domain layout under
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
└── services/
    ├── __init__.py
    └── create_subscription.py
```

The implemented HTTP composition for billing lives under the repository's
versioned API package:

```text
src/clinicops/api/v1/billing/
├── __init__.py
├── dependencies.py
├── routes.py
└── schemas.py
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

billing/services/create_subscription.py
    -> multi-transaction tenant subscription-creation orchestration
    -> client idempotency replay
    -> durable provider-operation reservation and claiming
    -> provider calls outside database transactions
    -> persistence of provider customer and subscription results

api/v1/billing/dependencies.py
    -> Idempotency-Key extraction and validation
    -> BILLING_MANAGE authorization dependency
    -> application-scoped PaymentProvider resolution
    -> subscription service construction

api/v1/billing/routes.py
    -> tenant-scoped POST subscription route
    -> 201 first execution and 200 successful replay

api/v1/billing/schemas.py
    -> public request and response models
    -> exclusion of provider identifiers from the HTTP contract
```

Billing currently uses one `models.py` file because the four mapped entities
belong to the same billing persistence boundary and remain manageable
together.

The repository package is justified by the four distinct persistence
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

The billing subscription service is an application orchestrator rather than a
single-transaction domain service.

It intentionally owns multiple commits because provider calls must occur
without an open database transaction.

The orchestrator reserves and claims durable provider operations, commits,
calls the provider, and then reloads and locks persisted state before applying
the result.

The API package remains under `api/v1/` because this is the repository's
implemented HTTP composition convention.

The payment provider remains application-scoped because the deterministic
fake adapter stores in-memory idempotency and ambiguous-outcome state across
requests.

The billing module now contains an implemented provider abstraction, fake
adapter, subscription-creation service, and versioned HTTP composition. It
does not currently contain:

```text
plan-change service and route
cancellation service and route
billing jobs
billing workers
webhook HTTP handlers
real provider adapters
entitlement enforcement
```

Those capabilities are added only when the corresponding application,
transport, or background-processing responsibilities are implemented.

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

## 10. Naming Guidance

Use entity-oriented names for persistent models and transport schemas.

Examples:

```text
models/tenant.py
models/membership.py
schemas/invitation.py
schemas/appointment.py
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
repositories/payment_event_repository.py
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
- appointment transitions;
- invoice transitions;
- retry calculations;
- HMAC verification.

---

### `tests/integration/`

Validates PostgreSQL and FastAPI behavior.

Examples:

- login and token refresh;
- invitation acceptance transactions;
- tenant-scoped patient access;
- duplicate webhook handling;
- background job acquisition;
- audit persistence.

---

### `tests/security/`

Makes high-risk boundaries explicit.

Examples:

- cross-tenant access rejection;
- RBAC enforcement;
- invalid webhook signature rejection;
- revoked token behavior;
- Platform Admin boundary checks.

---

### `tests/factories/`

Provides valid test data builders while keeping tenant ownership and membership roles visible.

Factories must not hide important relationships that are relevant to the behavior under test.

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