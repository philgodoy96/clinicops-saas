# Membership Administration Model

## Purpose

ClinicOps separates tenant membership administration from tenant onboarding.

Onboarding creates the initial owner and allows invited users to join a tenant.
Membership administration manages the lifecycle of relationships that already
exist.

The administration boundary supports:

```text
change a non-owner membership role
disable a membership
enable a membership
remove a membership
transfer tenant ownership
```

These workflows are tenant-scoped, authorization-sensitive, and transactional.

The central invariant is:

```text
Every active tenant must retain exactly one active owner.
```

## API surface

The HTTP boundary exposes:

```text
PATCH  /api/v1/tenants/{tenant_id}/memberships/{membership_id}/role
POST   /api/v1/tenants/{tenant_id}/memberships/{membership_id}/disable
POST   /api/v1/tenants/{tenant_id}/memberships/{membership_id}/enable
DELETE /api/v1/tenants/{tenant_id}/memberships/{membership_id}

POST   /api/v1/tenants/{tenant_id}/ownership/transfer
```

All operations require bearer authentication and a persisted tenant context.

Generic membership administration requires:

```text
member:manage
```

Ownership transfer requires:

```text
ownership:transfer
```

The ownership capability is intentionally separate from general membership
management.

## Domain model

### Global user

`User` represents a platform identity.

A user may belong to multiple tenants through independent memberships.

Removing one membership does not remove:

```text
the global user
password credentials
authentication sessions
memberships in other tenants
```

### Membership

`Membership` represents the relationship between one global user and one
tenant.

It stores:

```text
tenant identity
user identity
tenant role
membership status
lifecycle timestamps
```

Supported roles:

```text
OWNER
ADMIN
STAFF
```

Supported statuses:

```text
ACTIVE
DISABLED
```

### Tenant owner

Ownership is represented by an active membership with:

```text
role = OWNER
status = ACTIVE
```

Ownership is not stored as a caller-controlled tenant field.

## Authorization model

### HTTP authorization

Membership administration routes resolve:

```text
Bearer access token
    -> authenticated principal
    -> persisted tenant
    -> persisted membership
    -> tenant role
    -> required tenant permission
```

Role, status, and ownership changes therefore affect authorization on the next
request.

ClinicOps does not cache tenant authorization state in this milestone.

### Transactional authorization

Membership administration services revalidate the actor inside the
transaction.

The common validation requires:

```text
tenant exists
tenant is active
actor membership exists in the tenant
actor membership is active
actor currently has member:manage
```

This protects:

```text
non-HTTP callers
stale authorization decisions
concurrent role changes
concurrent membership disabling
```

HTTP authorization rejects unauthorized calls early.

Application authorization protects the business transition using current
locked state.

## Trusted input boundaries

The caller cannot choose the administrative actor.

For membership administration:

```text
tenant_id
    -> authorized tenant context

actor_user_id
    -> authorized tenant context

membership_id
    -> URL path

requested role
    -> request body, when applicable
```

For ownership transfer:

```text
tenant_id
    -> authorized tenant context

expected current owner user ID
    -> authorized tenant context

new owner user ID
    -> request body
```

The request cannot provide:

```text
current owner identity
previous owner identity
actor identity
target tenant identity
target status
former owner role
new owner role
```

## Locking model

### Membership administration

Role, status, and removal workflows use:

```text
lock tenant
    -> lock actor and target memberships in membership-ID order
```

Actor and target memberships are selected in one tenant-scoped query and
ordered by primary key before `FOR UPDATE` locking.

This gives administrative operations a stable lock order even when two
transactions target overlapping memberships.

The tenant-scoped predicate ensures that a membership identifier from another
tenant is treated as not found.

### Ownership transfer

Ownership transfer uses:

```text
lock tenant
    -> lock current active owner
    -> lock target membership
```

The service then validates the expected owner identity before changing roles.

## Role management

### Endpoint

```text
PATCH /api/v1/tenants/{tenant_id}/memberships/{membership_id}/role
```

### Request

```json
{
  "role": "admin"
}
```

Allowed generic roles:

```text
ADMIN
STAFF
```

The generic endpoint cannot assign:

```text
OWNER
```

Ownership is changed only through the dedicated ownership-transfer workflow.

### Application workflow

```text
validate active tenant
validate actor authority
validate target membership
reject self-management
reject OWNER assignment
protect owner membership
require active target
capture previous role
apply ADMIN or STAFF
flush and refresh
return result
```

### Same-role behavior

A request that assigns the current role is idempotent.

Example:

```text
ADMIN -> ADMIN
```

The service returns a successful result without mutating or flushing the row.

### Owner protection

The generic endpoint cannot demote the active owner.

An ownership change requires a single transaction that both promotes the target
and demotes the current owner.

## Membership disabling

### Endpoint

```text
POST /api/v1/tenants/{tenant_id}/memberships/{membership_id}/disable
```

The endpoint has no request body.

### Application workflow

```text
validate active tenant
validate actor authority
validate target membership
reject self-management
protect owner membership
reject already-disabled target
set status to DISABLED
set disabled_at
flush and refresh
return result
```

The persisted role is preserved.

Examples:

```text
ACTIVE ADMIN -> DISABLED ADMIN
ACTIVE STAFF -> DISABLED STAFF
```

### Access effect

A disabled membership no longer satisfies tenant-context authorization.

The change takes effect on the next request.

## Membership enabling

### Endpoint

```text
POST /api/v1/tenants/{tenant_id}/memberships/{membership_id}/enable
```

The endpoint has no request body.

### Application workflow

```text
validate active tenant
validate actor authority
validate target membership
reject self-management
protect owner role
reject already-active target
set status to ACTIVE
clear disabled_at
flush and refresh
return result
```

The persisted role is restored without modification.

Examples:

```text
DISABLED ADMIN -> ACTIVE ADMIN
DISABLED STAFF -> ACTIVE STAFF
```

A disabled membership with the owner role cannot be enabled through this
generic workflow because that could create a second active owner.

## Membership removal

### Endpoint

```text
DELETE /api/v1/tenants/{tenant_id}/memberships/{membership_id}
```

Successful removal returns:

```text
204 No Content
```

### Lifecycle decision

ClinicOps distinguishes reversible suspension from permanent tenant
detachment.

```text
Disable
    -> reversible
    -> membership remains persisted
    -> role remains persisted

Remove
    -> permanent tenant detachment
    -> membership row is deleted
    -> global user remains
```

Removal is implemented as a hard delete of the membership relationship.

This milestone does not add:

```text
removed status
removed_at
soft-delete query filters
historical membership reconstruction
```

Those capabilities would require a broader retention model and are not needed
for the current membership lifecycle.

### Application workflow

```text
validate active tenant
validate actor authority
validate target membership
reject self-management
protect owner membership
capture result identifiers
delete membership
flush
return result
```

Both active and disabled non-owner memberships may be removed.

### Isolation

Removing a membership affects only the selected tenant relationship.

Memberships held by the same global user in other tenants remain intact.

## Administrative self-management

Generic membership administration rejects operations against the actor's own
membership.

This applies to:

```text
role changes
disable
enable
removal
```

The rejection prevents administrative endpoints from becoming implicit
self-demotion or tenant-departure workflows.

A future `leave tenant` capability can define separate rules for:

```text
owner departure
admin departure
pending jobs
billing responsibility
audit retention
```

## Ownership transfer

### Endpoint

```text
POST /api/v1/tenants/{tenant_id}/ownership/transfer
```

### Request

```json
{
  "new_owner_user_id": "user-uuid"
}
```

### Dedicated permission

Ownership transfer requires:

```text
ownership:transfer
```

Current policy:

```text
OWNER
    -> allowed

ADMIN
    -> denied

STAFF
    -> denied
```

General `member:manage` authority is not sufficient.

### Application workflow

```text
lock tenant
validate active tenant
lock current active owner
verify expected current owner identity
reject current owner as target
lock target membership
require active target
require target role ADMIN or STAFF
demote current owner to ADMIN
flush
promote target to OWNER
flush
return result
```

The former owner always becomes:

```text
ADMIN
```

The target may previously be:

```text
ADMIN
STAFF
```

### Atomicity

The route commits only after the complete application transition succeeds.

A failure after demotion but before promotion rolls back the full transaction.

No committed state may expose:

```text
zero active owners
two active owners
partial ownership transfer
```

## Transaction ownership

ClinicOps application services flush but do not commit.

Membership administration follows:

```text
Application service
    -> lock
    -> validate
    -> mutate
    -> flush
    -> return result

HTTP route
    -> commit
    -> serialize response
```

When an expected application failure is raised:

```text
the route does not commit
the request-scoped database dependency rolls back
no partial state remains
```

This keeps the services reusable by future non-HTTP callers while preserving a
single transaction owner at the transport boundary.

## Persistence safeguards

### Membership uniqueness

```text
uq_memberships_user_id_tenant_id
```

Protects one membership per user and tenant.

### Active owner uniqueness

```text
uq_memberships_one_active_owner_per_tenant
```

Protects at most one active owner membership per tenant.

The database partial unique index is the final safeguard against two active
owners.

The requirement that an active tenant retain at least one active owner remains
application-enforced.

### Role and status values

PostgreSQL enums constrain membership roles to:

```text
owner
admin
staff
```

and statuses to:

```text
active
disabled
```

## Concurrency behavior

### Role change versus disable

Two requests may race against the same active staff membership.

Valid serial outcomes:

```text
role change wins first
    -> STAFF becomes ADMIN
    -> membership becomes DISABLED
    -> final state ADMIN / DISABLED

disable wins first
    -> membership becomes DISABLED as STAFF
    -> role change returns membership_disabled
    -> final state STAFF / DISABLED
```

The final state must correspond to one valid serial order.

### Concurrent ownership transfers

Two requests may attempt to transfer ownership from the same owner to different
targets.

Required outcome:

```text
exactly one transfer commits
exactly one active owner remains
former owner becomes ADMIN
winning target becomes OWNER
```

The losing request may be rejected by:

```text
HTTP authorization
    -> former owner no longer has ownership:transfer

application validation
    -> expected owner no longer matches active owner
```

Both outcomes preserve the same ownership invariant.

### Removal versus ownership transfer

Removal and ownership transfer may race against the same target membership.

Valid outcomes:

```text
transfer wins
    -> target becomes OWNER
    -> removal returns membership_owner_protected

removal wins
    -> target membership is deleted
    -> transfer returns membership_not_found
```

The system must never commit both transitions.

## Problem Details

Membership administration failures use:

```text
application/problem+json
```

Current mapping:

| Failure | Status |
|---|---:|
| Membership not found | `404` |
| Tenant not found | `404` |
| Actor not authorized | `403` |
| Tenant disabled | `403` |
| Generic role not allowed | `400` |
| Target membership disabled | `409` |
| Owner membership protected | `409` |
| Administrative self-management rejected | `409` |
| Membership already disabled | `409` |
| Membership already active | `409` |
| Ownership state changed | `409` |
| Ownership transfer invalid | `409` |
| HTTP request validation failed | `422` |

Cross-tenant membership identifiers are represented as `404` and do not reveal
resource existence outside the selected tenant.

Authorization and lifecycle failures do not emit an HTTP bearer challenge.
Bearer challenges remain reserved for authentication failures.

## Response contracts

### Role change

```json
{
  "membership_id": "membership-uuid",
  "tenant_id": "tenant-uuid",
  "user_id": "user-uuid",
  "previous_role": "staff",
  "role": "admin",
  "updated_at": "2026-08-30T15:00:00Z"
}
```

### Disable

```json
{
  "membership_id": "membership-uuid",
  "tenant_id": "tenant-uuid",
  "user_id": "user-uuid",
  "role": "staff",
  "disabled_at": "2026-08-30T15:00:00Z"
}
```

### Enable

```json
{
  "membership_id": "membership-uuid",
  "tenant_id": "tenant-uuid",
  "user_id": "user-uuid",
  "role": "staff",
  "updated_at": "2026-08-30T15:00:00Z"
}
```

### Ownership transfer

```json
{
  "tenant_id": "tenant-uuid",
  "previous_owner_user_id": "previous-owner-uuid",
  "new_owner_user_id": "new-owner-uuid"
}
```

### Removal

```text
204 No Content
```

## Verification strategy

### Contract tests

Schema tests verify:

```text
only ADMIN and STAFF are accepted by generic role requests
OWNER is rejected
trusted identity fields are rejected
application results map directly to HTTP responses
response models reject unexpected fields
response models are immutable
```

### Application integration tests

PostgreSQL-backed service tests verify:

```text
allowed role transitions
same-role idempotency
actor authorization revalidation
cross-tenant isolation
owner protection
self-management rejection
disable and enable lifecycle
role preservation
global-user preservation during removal
other-tenant membership preservation
service-owned flush without commit
ownership-transfer rollback behavior
```

### Route tests

HTTP unit tests verify:

```text
permission dependencies
trusted command translation
route-owned commits
no commit after validation or application failure
empty 204 removal response
OpenAPI security and response contracts
owner-only ownership permission
```

### Concurrency tests

PostgreSQL-backed HTTP tests verify:

```text
role-versus-disable serialization
one-winner ownership transfer
removal-versus-transfer consistency
exactly one active owner after every race
```

## Observability

All routes continue to use the standard request-context middleware.

Responses and failure logs can be correlated through:

```text
request ID
correlation ID
```

Membership administration does not expose:

```text
password data
authentication tokens
session secrets
cross-tenant membership details
internal database errors
```

## Intentional boundaries

This milestone intentionally does not implement:

```text
self-service tenant departure
custom tenant roles
custom permission editing
bulk membership administration
soft-deleted membership history
ownership acceptance or approval
temporary access grants
role-change notifications
SCIM provisioning
audit-log query endpoints
billing authorization
```

Self-service departure remains separate because it requires different actor and
ownership rules.

Soft deletion remains deferred because disable already provides reversible
suspension, while removal intentionally models permanent tenant detachment.

Custom roles are not introduced because the current `OWNER`, `ADMIN`, and
`STAFF` model is sufficient for the system's authorization and ownership
objectives.

Billing authorization is handled in the next milestone rather than being
coupled to general membership administration.
