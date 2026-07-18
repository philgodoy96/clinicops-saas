# ClinicOps SaaS — Engineering Guide

## 1. Purpose

This document defines the engineering standards for ClinicOps SaaS.

The guide establishes expectations for architecture, security, data access, transactions, testing, observability, database migrations, and repository contributions.

Its purpose is to keep the codebase consistent, reviewable, and safe as the system evolves.

---

## 2. Architecture Principles

ClinicOps is a modular monolith.

The application is developed from one codebase and uses PostgreSQL as its primary source of truth.

The architecture must preserve:

- explicit module ownership;
- strong transactional boundaries;
- thin HTTP handlers;
- application services for business workflows;
- repositories for persistence behavior;
- provider abstractions for external integrations;
- separate API and background worker processes;
- clear separation between business audit records and operational logs.

A shared database does not grant every module unrestricted access to every table.

Each module remains responsible for the rules and workflows of the entities it owns.

---

## 3. Module Boundaries

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

Each module owns:

- its persistent models;
- its transport schemas;
- its repositories;
- its application services;
- its business exceptions;
- its authorization policies where applicable.

Cross-module workflows must use explicit services or narrow interfaces.

Avoid:

- direct mutation of another module's entities;
- circular imports;
- business rules in shared utilities;
- provider-specific payloads leaking into domain modules;
- route handlers coordinating multiple repositories directly.

---

## 4. HTTP API Rules

Route handlers are transport adapters.

They may:

- validate request input;
- resolve authentication and authorization dependencies;
- invoke application services;
- return response schemas;
- rely on centralized exception mapping.

They must not:

- implement state transitions;
- contain complex authorization logic;
- manage multi-step transactions;
- perform unrestricted database updates;
- implement retry or webhook signature logic;
- load tenant-owned resources without tenant-scoped repository methods.

API routes must use explicit versioning under:

```text
/api/v1
```

Tenant-owned resources should use an explicit tenant context in the route.

Example:

```text
/api/v1/tenants/{tenant_id}/patients
```

---

## 5. Application Service Rules

Application services coordinate business use cases.

They may:

- validate workflow invariants;
- coordinate repositories;
- define transaction boundaries;
- invoke provider interfaces;
- create audit records;
- enqueue durable background jobs.

Application services must remain independent of FastAPI request and response objects.

A business workflow that must succeed or fail as one unit must use one explicit transaction boundary.

Examples include:

- tenant and owner-membership creation;
- invitation acceptance;
- ownership transfer;
- webhook processing;
- invoice and subscription transitions;
- business changes that require background jobs;
- critical changes that require audit records.

---

## 6. Repository Rules

Repositories own persistence behavior.

Repositories must:

- expose explicit query methods;
- require tenant context for tenant-owned resources;
- include tenant identifiers in database predicates;
- provide row-locking operations when concurrency control is required;
- keep transaction commits outside repository methods;
- return results that application services can interpret explicitly.

Repositories must not:

- decide whether an actor has permission;
- import FastAPI;
- raise HTTP exceptions;
- hide database commits;
- expose broad generic CRUD methods that weaken tenant isolation;
- load tenant-owned resources globally for tenant-scoped workflows.

Correct tenant-scoped access:

```text
resource.id = requested_resource_id
AND resource.tenant_id = requested_tenant_id
```

---

## 7. Multi-Tenancy Rules

Users are global platform identities.

Memberships connect users to tenants.

A user may belong to multiple tenants and may hold a different role in each tenant.

A user's role must not be stored on the global user record.

Every tenant-scoped operation must resolve:

```text
authenticated user
target tenant
active membership
required permission
```

A valid access token does not grant access to a tenant by itself.

Tenant authorization and tenant-scoped repository queries are complementary controls.

Cross-tenant access is a critical security defect.

---

## 8. Tenant Ownership Rules

Every tenant must have exactly one active owner.

The owner:

- cannot remove their own membership;
- cannot leave the tenant directly;
- cannot be removed by another tenant member;
- cannot be downgraded through a generic role update.

Invitations cannot assign the owner role.

Ownership transfer must:

- target an active member of the same tenant;
- lock the required records;
- promote the target member;
- demote the current owner;
- create an audit record;
- commit atomically.

The system must never expose a committed state with zero or multiple active owners.

---

## 9. Authorization Rules

V1 tenant roles are:

```text
owner
admin
staff
```

Permissions must be defined centrally and tested explicitly.

Authorization logic must not be scattered as repeated string comparisons across route handlers and services.

Platform administration uses a separate authorization boundary from tenant RBAC.

A Platform Admin is not automatically a tenant member and must not receive implicit access to tenant operational data.

Authorization decisions must consider:

- authenticated user status;
- tenant status;
- membership status;
- membership role;
- required permission;
- resource ownership.

---

## 10. Authentication Rules

Authentication is global.

The login endpoint does not require a tenant identifier.

Access tokens are short-lived and identify the global user.

Refresh tokens are stored as hashes.

Logout revokes the corresponding refresh token or session.

Raw access tokens and refresh tokens must never be persisted in application logs or audit records.

Authentication behavior must distinguish:

- invalid credentials;
- inactive users;
- expired access tokens;
- invalid tokens;
- revoked refresh tokens.

Public error responses must not expose unnecessary security details.

---

## 11. Password Hashing Rules

Passwords must be hashed using Argon2id through a `PasswordHasher` abstraction.

Raw passwords must never be:

- persisted;
- logged;
- included in audit metadata;
- exposed in exception details;
- stored in snapshots or fixtures.

Password hashing and verification must not be implemented through direct library calls scattered across the codebase.

Hashing parameters must be configurable and documented.

---

## 12. Invitation Rules

Invitation tokens must be cryptographically secure random values.

Only token hashes may be stored.

An invitation includes:

- tenant;
- target email;
- assigned role;
- inviter;
- expiration;
- status.

Invitations cannot assign ownership.

Invitation acceptance must:

- validate the token hash;
- validate expiration and status;
- validate the target email;
- prevent token reuse;
- prevent duplicate membership creation;
- create an audit record;
- commit atomically.

New-user and existing-user acceptance flows must enforce the same core invariants.

---

## 13. Patient Rules

Patients are tenant-owned operational records.

Patients are not authenticated platform users in v1.

Each patient record belongs to exactly one tenant.

The same real-world person may have independent records in different tenants.

Patient records must not be linked, exposed, or synchronized across tenants.

Sensitive patient information must not be copied unnecessarily into:

- application logs;
- audit metadata;
- background job payloads;
- error messages.

---

## 14. Appointment Rules

Appointments belong to one tenant.

The associated patient must belong to the same tenant.

An assigned professional membership must belong to the same tenant and be active when required.

Appointment status changes must follow explicit lifecycle rules.

Arbitrary status updates are forbidden.

Invalid transitions must fail explicitly.

Lifecycle rules must be tested independently from HTTP behavior.

---

## 15. Billing Rules

Billing records must follow explicit lifecycle rules.

Plans define entitlements such as:

- maximum active users;
- maximum active patients;
- appointment reminder availability.

Entitlement resolution must be centralized.

Subscription and invoice state must not be changed through unrestricted generic update endpoints.

Monetary values must use precise decimal or integer minor-unit representations, never binary floating-point values.

Billing workflows must create appropriate audit records.

Billing changes and required durable jobs should commit in the same transaction when appropriate.

---

## 16. Webhook Security Rules

Webhook verification must use the exact raw request body.

The system must:

- compute the expected HMAC;
- compare signatures using a timing-safe method;
- validate required signature metadata;
- enforce the configured timestamp or replay policy;
- reject invalid signatures before business processing.

Invalid webhook requests must not create:

- payment events;
- invoice transitions;
- subscription transitions;
- business audit events;
- background jobs.

Provider secrets must come from validated configuration and must never appear in logs.

---

## 17. Webhook Idempotency Rules

Every provider event must include a unique provider event identifier.

The identifier must be protected by a database uniqueness constraint.

A duplicate event must not produce duplicate:

- billing state transitions;
- audit records;
- receipts;
- background jobs.

Idempotency must rely on durable database state rather than in-memory tracking.

Previously processed duplicate events may return a successful response after the persisted event state is confirmed.

---

## 18. Background Job Rules

Background jobs use PostgreSQL as a durable queue.

Supported states include:

```text
pending
running
succeeded
failed
dead
```

Job acquisition must support multiple workers safely using:

```sql
FOR UPDATE SKIP LOCKED
```

Jobs must preserve:

- job type;
- payload;
- attempts;
- maximum attempts;
- scheduled run time;
- lock information;
- last error;
- idempotency key;
- request ID;
- correlation ID.

Job execution follows at-least-once semantics.

Handlers that may repeat side effects must be idempotent.

Retries must use exponential backoff with jitter.

Retryable and non-retryable failures must be distinguished.

Repeatedly failing jobs must transition to a durable dead state and remain inspectable.

Sensitive information must not be placed in job payloads unless required and appropriately protected.

---

## 19. Audit Log Rules

Audit logs record business-relevant actions.

Examples include:

```text
tenant.created
tenant.ownership_transferred
user.invited
invitation.accepted
patient.created
appointment.cancelled
subscription.created
invoice.paid
tenant.suspended
webhook.processed
```

Audit records are append-only.

They must preserve:

- actor;
- tenant when applicable;
- action;
- target;
- meaningful metadata;
- request ID;
- correlation ID;
- timestamp.

Audit records must not contain:

- passwords;
- password hashes;
- raw access tokens;
- raw refresh tokens;
- raw invitation tokens;
- provider secrets;
- unnecessary sensitive patient data.

Critical audit writes should participate in the related business transaction.

---

## 20. Application Logging Rules

Application logs describe runtime and operational behavior.

Logs must be structured.

Relevant fields include:

```text
event
request_id
correlation_id
user_id
tenant_id
job_id
payment_event_id
duration_ms
status_code
```

Examples include:

```text
request_started
request_finished
authentication_failed
webhook_signature_invalid
webhook_duplicate_received
job_retry_scheduled
job_marked_dead
database_error
```

Application logs do not replace audit records.

Secrets, raw tokens, passwords, and unnecessary patient data must not be logged.

---

## 21. Request ID and Correlation ID Rules

Every incoming HTTP request must have a request ID.

A request ID identifies one execution.

A correlation ID connects related work across:

- HTTP requests;
- payment events;
- billing transitions;
- audit records;
- background jobs;
- worker executions.

A worker execution receives its own execution identifier while preserving the originating correlation identifier.

Request and correlation identifiers must be propagated consistently to structured logs and relevant durable records.

---

## 22. Database Migration Rules

Database schema changes use Alembic migrations.

Migrations must be:

- incremental;
- reviewable;
- named clearly;
- committed with the corresponding application change;
- validated against PostgreSQL;
- safe for the supported upgrade path.

Application startup must not silently create or mutate the production schema.

Destructive or irreversible migrations require explicit documentation and review.

Database constraints must enforce critical invariants where practical.

Examples include:

- globally unique normalized user email;
- unique membership per user and tenant;
- unique provider event identifier;
- unique job idempotency keys where required.

---

## 23. Testing Standards

Testing effort must follow engineering risk.

The test suite should include:

- unit tests;
- integration tests;
- security-boundary tests;
- concurrency tests where required;
- idempotency tests;
- failure simulations;
- manual API validation where useful.

Critical examples include:

- Tenant A cannot access Tenant B data.
- An admin cannot transfer ownership.
- An invitation cannot be accepted twice.
- Invalid webhook signatures create no billing effects.
- Duplicate webhook events create one billing effect.
- Two workers do not claim the same job simultaneously.
- Retried handlers do not duplicate external side effects.
- Concurrent ownership transfers preserve exactly one owner.

Tests should validate business outcomes, not only HTTP status codes.

---

## 24. Error Handling Rules

Expected business failures must use explicit application exceptions.

Examples include:

```text
InvitationExpiredError
MembershipAlreadyExistsError
OwnershipTransferNotAllowedError
InvalidAppointmentTransitionError
InvoiceTransitionNotAllowedError
```

HTTP error mapping should be centralized.

Unexpected exceptions must:

- produce generic public responses;
- preserve request and correlation identifiers;
- create structured internal logs;
- avoid exposing implementation details or secrets.

---

## 25. Dependency Management

Dependencies must have a clear architectural purpose.

Avoid adding tools or libraries only to increase the visible technology surface.

New dependencies should be evaluated for:

- ownership and maintenance;
- security history;
- operational cost;
- testability;
- compatibility with the architecture;
- whether the standard library or an existing dependency already solves the problem.

Dependency versions must be managed through the project's Python package configuration and lock file.

---

## 26. Repository Contribution Standards

Development must not occur directly on `main`.

Branches should represent focused changes and use descriptive prefixes such as:

```text
docs/
chore/
feat/
fix/
refactor/
test/
```

Commits must be small, coherent, and professionally named.

Examples:

```text
chore: initialize FastAPI application
feat: add tenant domain models
feat: implement ownership transfer
test: add tenant isolation coverage
fix: prevent duplicate webhook processing
docs: document billing state transitions
```

Avoid vague commit messages such as:

```text
update
changes
fixes
final
done
```

Pull Requests must describe:

```text
Summary
What changed
Why it changed
How to test
Notes / trade-offs
Follow-up work
```

Before merge, changes must be reviewed for:

- architecture compliance;
- tenant isolation;
- authorization boundaries;
- transaction safety;
- concurrency behavior;
- idempotency;
- failure handling;
- structured logging;
- auditability;
- migration correctness;
- test quality;
- documentation accuracy;
- unnecessary complexity.