# Tenant and Membership Model

## Purpose

ClinicOps is a multi-tenant platform.

Global authentication identifies a user, but it does not grant access to any tenant. Tenant participation, lifecycle, and role are represented by an explicit membership between a global user and a tenant.

This model keeps identity, tenancy, and authorization responsibilities separate:

```text
Global User
    |
    | 1
    |
    | *
Membership
    |
    | *
    |
    | 1
Tenant
```

A global user may belong to multiple tenants and may hold a different role in each tenant.

## Ownership Boundary

The tenancy module owns:

- tenants;
- tenant lifecycle state;
- memberships;
- membership lifecycle state;
- tenant roles;
- initial tenant ownership;
- ownership transfer;
- tenancy persistence and locking operations.

The tenancy module does not own:

- global user credentials;
- authentication sessions;
- access or refresh tokens;
- invitation issuance or acceptance;
- API authorization middleware;
- role-to-permission mapping;
- subscription or billing state;
- audit logs;
- clinic-specific settings.

The identity module remains independent from tenancy. The tenancy module may depend on identity models and repositories because a membership references a global user.

## Tenant Model

The `tenants` table stores one isolated product boundary.

Fields:

```text
id
name
status
created_at
updated_at
disabled_at
```

Properties:

- `id` is a UUID;
- `name` is a tenant display name;
- names are not globally unique;
- `status` is either `active` or `disabled`;
- disabled tenants remain persisted;
- tenant ownership is not stored directly on the tenant row;
- subscription, billing, and clinic configuration fields are not part of this model.

A tenant does not contain an `owner_user_id` column. Ownership is represented exclusively by an owner membership, avoiding duplicated sources of truth.

## Membership Model

The `memberships` table connects one global user to one tenant.

Fields:

```text
id
tenant_id
user_id
role
status
created_at
updated_at
disabled_at
```

Properties:

- `id` is a UUID;
- `tenant_id` references one tenant;
- `user_id` references one global user;
- `role` is scoped to that membership's tenant;
- `status` is either `active` or `disabled`;
- disabled memberships remain persisted for historical and audit references.

The same global user may hold different roles across tenants:

```text
Tenant A -> owner
Tenant B -> admin
Tenant C -> staff
```

A role is never stored directly on the global user.

## Tenant Roles

ClinicOps currently persists three tenant roles:

```text
owner
admin
staff
```

### Owner

The owner represents control of the tenant lifecycle and is the only role that participates in ownership transfer.

### Admin

An admin represents elevated tenant administration without ownership.

### Staff

A staff member represents ordinary tenant participation.

These values are persisted state. A concrete permission matrix is intentionally deferred to the authorization layer.

## Lifecycle States

Tenant lifecycle:

```text
active
disabled
```

Membership lifecycle:

```text
active
disabled
```

Only active memberships may participate in tenant authorization.

Only an active membership may own a tenant.

A disabled membership cannot receive ownership, even if its persisted role is `admin` or `staff`.

Hard deletion is not part of the normal tenant or membership lifecycle.

## Tenant Name Policy

Tenant display names follow this policy:

```text
Minimum length: 2 characters
Maximum length: 120 characters
Surrounding whitespace: removed
Internal whitespace: preserved
Unicode: allowed
Global uniqueness: not required
```

Example:

```text
"  Northstar Health Clinic  "
```

is stored as:

```text
"Northstar Health Clinic"
```

The UUID is the technical tenant identity. The display name is intentionally not used as a globally unique identifier.

## Persistence Invariants

### Unique Membership

The database enforces:

```text
UNIQUE (user_id, tenant_id)
```

Constraint name:

```text
uq_memberships_user_id_tenant_id
```

A global user may belong to many tenants but may have only one membership within a specific tenant.

### Exactly One Active Owner

Exactly one active owner is a domain invariant. PostgreSQL enforces the
at-most-one half with a partial unique index. Transactional tenant creation and
ownership-transfer workflows preserve the at-least-one half, and ordinary
membership mutation paths cannot bypass the dedicated ownership workflow.

Enforcement split:

```text
Database (partial unique index)
    -> at most one active owner

Transactional application workflows
    -> at least one active owner across supported flows

Supported system behavior
    -> exactly one active owner
```

The database partial unique index is equivalent to:

```sql
UNIQUE (tenant_id)
WHERE role = 'owner' AND status = 'active'
```

Index name:

```text
uq_memberships_one_active_owner_per_tenant
```

Migration:

```text
migrations/versions/0003_add_tenants_and_memberships.py
```

A disabled historical owner does not conflict with the current active owner
because disabled memberships are excluded from the partial index.

The application preserves the at-least-one half by:

- creating every tenant with an active owner membership in the same transaction;
- rejecting generic role change, disable, enable, and remove paths that target
  the active owner;
- changing ownership only through the transactional ownership transfer service,
  which locks the tenant and memberships, demotes the current owner, promotes
  the target, and records audit state before the caller commits.

This is not a single database constraint proving both halves, and it does not
rely on a database trigger to enforce at-least-one.

## Tenant Creation

The creation service accepts:

```text
CreateTenantCommand
- name
- owner_user_id
```

It returns:

```text
CreatedTenant
- id
- name
- status
- owner_user_id
- created_at
```

The workflow is:

```text
Normalize tenant name
        |
        v
Lock global owner user
        |
        v
Require owner user exists
        |
        v
Require owner user is active
        |
        v
Create Tenant
        |
        v
Create active owner Membership
        |
        v
Flush the aggregate
        |
        v
Return CreatedTenant
```

The global user is locked because tenant creation depends on that user's lifecycle state remaining stable while the aggregate is created.

The tenant and initial owner membership enter the same transaction. If either persistence operation fails, the caller may roll back the complete aggregate.

## Transaction Ownership

Tenant application services call `flush()` but do not call `commit()`.

This allows future workflows to compose tenancy operations with other responsibilities:

```text
Create Tenant
Create Subscription
Write Audit Log
Schedule Onboarding Work
Commit
```

The caller owns the final transaction decision.

Automated integration coverage verifies that tenant creation and ownership transfer disappear completely when the caller rolls back.

## Ownership Transfer

The transfer service accepts:

```text
TransferTenantOwnershipCommand
- tenant_id
- expected_current_owner_user_id
- new_owner_user_id
```

It returns:

```text
TransferredTenantOwnership
- tenant_id
- previous_owner_user_id
- new_owner_user_id
```

### Eligibility Rules

A transfer requires:

- the tenant exists;
- the tenant is active;
- the tenant has an active owner;
- the active owner matches `expected_current_owner_user_id`;
- the target is different from the current owner;
- the target has a membership in the same tenant;
- the target membership is active;
- the target role is `admin` or `staff`.

A membership in another tenant does not satisfy the target requirement.

### Stale-State Protection

The expected owner field protects workflows based on stale state.

Example:

```text
Caller reads Owner A
Another transaction transfers A -> B
Caller attempts A -> C
```

The second transfer is rejected because the committed owner no longer matches the caller's expectation.

This produces:

```text
TenantOwnershipConflictError
```

rather than silently transferring ownership from a state the caller did not authorize.

## Locking Strategy

Ownership transfer uses a stable lock order:

```text
Lock Tenant
    |
    v
Lock current owner Membership
    |
    v
Lock target Membership
```

The tenant row is the serialization point for ownership changes within one tenant.

Concurrent transfers for different tenants may proceed independently. Concurrent transfers for the same tenant must pass through the same tenant row lock.

After acquiring the lock, the service reads the current committed ownership state and evaluates the expected owner again.

Automated concurrency coverage verifies that two transfers based on the same expected owner produce:

```text
one committed transfer
one ownership conflict
exactly one active owner
```

## Two-Step Role Update

The active-owner partial unique index prevents two active owners from existing simultaneously.

The service therefore performs ownership transfer in two flushes:

```text
Current owner -> admin
Flush
        |
        v
Target member -> owner
Flush
```

There is temporarily no active owner inside the uncommitted transaction.

That intermediate state is not externally visible as committed tenant state.

If the promotion fails, the caller rolls back the transaction and restores:

```text
original owner -> owner
target member -> previous role
```

Automated integration coverage forces a failure after demotion and verifies that the original ownership remains intact.

## Failure Model

Expected tenancy failures include:

```text
InvalidTenantNameError
TenantNotFoundError
TenantDisabledError
MembershipNotFoundError
MembershipDisabledError
TenantOwnershipConflictError
InvalidOwnershipTransferError
```

Tenant creation may also reuse identity failures:

```text
UserNotFoundError
UserDisabledError
```

These exceptions:

- do not import FastAPI;
- do not contain HTTP status codes;
- do not expose persistence details;
- may be mapped by a future API layer.

Unexpected database failures and unrelated integrity violations remain infrastructure failures rather than being converted into misleading tenancy errors.

## Security Boundaries

The model establishes the following security rules:

- global authentication does not imply tenant access;
- tenant access requires an active membership in the explicit tenant context;
- a role is valid only within the membership's tenant;
- a role from one tenant must never authorize access to another tenant;
- disabled memberships must not authorize access;
- disabled memberships cannot receive ownership;
- disabled tenants cannot transfer ownership;
- ownership transfer is serialized;
- ownership cannot be inferred from global user state;
- client-provided tenant identifiers must be validated against membership state;
- invitation input must not assign ownership directly.

The future authorization layer will map these persisted roles to permissions after resolving a trusted tenant context.

## Indexing and Scaling

The membership model includes:

```text
UNIQUE (user_id, tenant_id)
INDEX (tenant_id)
PARTIAL UNIQUE INDEX (tenant_id) for active owners
```

These indexes support:

- membership lookup for a user and tenant;
- listing memberships within a tenant;
- active owner lookup;
- enforcement of global tenancy invariants.

Global users are stored once even when they participate in many tenants.

Ownership transfer locks only one tenant row and the relevant membership rows. Operations against unrelated tenants are not serialized together.

Tenant names do not require a global uniqueness index.

Caching is intentionally deferred. Membership and ownership are authorization-sensitive data, and premature caching would introduce invalidation complexity and risk stale access decisions.

## Intentionally Deferred

The following capabilities are intentionally deferred:

- tenant HTTP endpoints;
- tenant context middleware;
- authentication;
- permission evaluation;
- role-to-permission mapping;
- invitation issuance;
- invitation acceptance;
- direct member creation;
- role management;
- membership disabling workflows;
- owner removal;
- tenant suspension APIs;
- Platform Admin operations;
- audit logs;
- billing integration;
- clinic settings;
- tenant slugs;
- custom domains;
- membership caching.

Invitation acceptance will become the controlled workflow for creating additional memberships. Ownership will remain assignable only through the ownership transfer service.