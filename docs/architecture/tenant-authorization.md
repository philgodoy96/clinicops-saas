# Tenant Authorization

## Purpose

ClinicOps separates global authentication from tenant-scoped authorization.

Authentication establishes:

```text
Who is the global user?
Which authentication session is being used?
Is that session currently active?
```

Tenant authorization establishes:

```text
Which tenant is selected?
Does the authenticated user have an active membership?
Which tenant role is currently assigned?
Does that role grant the required permission?
```

This separation keeps tenant authorization state out of access tokens and ensures that current PostgreSQL state remains authoritative.

## Trust Chain

Protected tenant-scoped operations follow this chain:

```text
Access JWT
    |
    v
AuthenticatedPrincipal
    |
    v
TenantContext
    |
    v
Required TenantPermission
    |
    v
AuthorizedTenantContext
    |
    v
Application operation
```

Each stage receives a trusted result from the previous stage.

Callers do not supply:

```text
membership_id
role
granted permissions
```

These values are derived from persisted state and the authorization policy.

## Module Ownership

### Authentication Module

The authentication module owns:

- access JWT validation;
- current authentication session validation;
- current global user validation;
- authenticated principal construction.

### Authorization Module

The authorization module owns:

- selected tenant resolution;
- active tenant validation;
- user-scoped membership resolution;
- active membership validation;
- tenant context construction;
- tenant permission definitions;
- role-to-permission policy;
- permission enforcement.

### Domain Services

Domain services remain responsible for:

- locking mutable rows;
- revalidating actor authority for sensitive transitions;
- enforcing target-resource invariants;
- applying mutations;
- flushing without committing.

Request authorization does not replace domain-level invariant enforcement.

## Authenticated Principal

### Command

```text
ResolveAuthenticatedPrincipalCommand
- access_token
```

The access token is excluded from object representations.

### Result

```text
AuthenticatedPrincipal
- user_id
- session_id
- access_token_id
- authenticated_at
- access_token_expires_at
- session_expires_at
```

The principal contains global identity and session information only.

It contains no:

```text
tenant_id
membership_id
role
permissions
```

### Resolution Flow

```text
Decode and validate access JWT
        |
        v
Resolve AuthSession using JWT.sid and JWT.sub
        |
        v
Require persisted session exists
        |
        v
Require session status = active
        |
        v
Require session expiration > current time
        |
        v
Require global user status = active
        |
        v
Return AuthenticatedPrincipal
```

### Subject and Session Binding

The repository requires:

```text
AuthSession.id = JWT.sid
AuthSession.user_id = JWT.sub
```

A signed token containing a valid user ID and a session ID owned by another user cannot resolve a principal.

The failure does not disclose the actual session owner.

### Current-State Validation

A valid JWT signature is not sufficient for protected access.

The current database state must also satisfy:

```text
AuthSession.status = active
AuthSession.expires_at > current_time
User.status = active
```

This allows logout, token-family compromise, and user disabling to invalidate protected requests before the access token reaches its natural expiration.

### Access Token and Session Expiration

Two independent expiration boundaries exist:

```text
Access token
    -> short-lived bearer credential
    -> normally 15 minutes

Authentication session
    -> persistent login boundary
    -> normally 30 days
```

An unexpired access token cannot authorize a request after the persisted session expires.

## Tenant Context

### Command

```text
ResolveTenantContextCommand
- principal
- tenant_id
```

The command receives a trusted `AuthenticatedPrincipal`.

It does not receive an arbitrary user ID.

### Result

```text
TenantContext
- user_id
- session_id
- tenant_id
- membership_id
- role
```

The membership ID and role come from PostgreSQL.

### Resolution Flow

```text
Receive trusted AuthenticatedPrincipal
        |
        v
Load selected Tenant
        |
        v
Join Membership scoped by tenant_id and principal.user_id
        |
        v
Require tenant exists
        |
        v
Require tenant status = active
        |
        v
Require membership exists
        |
        v
Require membership status = active
        |
        v
Return TenantContext
```

### Tenant and Membership Query

The repository starts from the selected tenant and performs a left outer join with membership using:

```text
Membership.tenant_id = Tenant.id
Membership.user_id = principal.user_id
```

This supports a precise distinction:

```text
tenant missing
    -> TenantNotFoundError

tenant exists but user membership missing
    -> TenantMembershipNotFoundError
```

### Cross-Tenant Isolation

A membership in one tenant grants no authority in another tenant.

Given:

```text
User A
    -> owner of Tenant X
    -> no membership in Tenant Y
```

Selecting Tenant Y resolves no membership for the pair:

```text
user_id = User A
tenant_id = Tenant Y
```

Result:

```text
TenantMembershipNotFoundError
```

### Cross-User Isolation

A membership belonging to another user cannot be reused.

Given:

```text
AuthenticatedPrincipal.user_id = User A
Tenant X membership belongs to User B
```

The membership join remains scoped to User A and returns no membership.

### Lifecycle Validation

Tenant context cannot be resolved when:

```text
Tenant.status = disabled
    -> TenantDisabledError

Membership.status = disabled
    -> TenantMembershipDisabledError
```

A disabled membership removes access only to that tenant.

It does not disable:

- the global user;
- authentication sessions;
- memberships in other tenants.

## Tenant Permissions

Tenant permissions are represented by the `TenantPermission` string enum.

Declared permissions:

```text
tenant:read
member:read
member:invite
member:manage
invitation:read
invitation:create
invitation:revoke
billing:read
billing:manage
audit_log:read
patient:read
patient:create
patient:update
patient:archive
patient:restore
```

Permissions provide a stable language for:

- application services;
- future API dependencies;
- tests;
- documentation;
- audit events;
- security reviews.

A permission may be defined before an HTTP endpoint exists.

## Code-Based Role Policy

ClinicOps currently uses a code-based permission policy.

The policy maps `TenantRole` values to immutable permission sets.

```text
TenantRole
    -> frozenset[TenantPermission]
```

The mapping uses:

```text
MappingProxyType
```

The permission collections use:

```text
frozenset
```

The policy cannot be modified accidentally during runtime.

## Role Matrix

### Owner

Owner receives every declared tenant permission:

```text
tenant:read
member:read
member:invite
member:manage
invitation:read
invitation:create
invitation:revoke
billing:read
billing:manage
audit_log:read
patient:read
patient:create
patient:update
patient:archive
patient:restore
```

### Admin

Admin receives:

```text
tenant:read
member:read
member:invite
member:manage
invitation:read
invitation:create
invitation:revoke
billing:read
audit_log:read
patient:read
patient:create
patient:update
patient:archive
patient:restore
```

Admin does not receive:

```text
billing:manage
```

### Staff

Staff receives:

```text
tenant:read
patient:read
patient:create
patient:update
```

Staff cannot:

- read or manage memberships;
- invite members;
- create or revoke invitations;
- manage billing;
- read audit logs;
- archive or restore patients.

## Why the Policy Is in Code

A code-based policy is appropriate for the current role model because:

- roles are fixed domain concepts;
- permissions require code-level behavior;
- policy changes should receive code review;
- tests can validate the complete matrix;
- no runtime administration interface is required;
- invalid database permission configuration is impossible;
- operational complexity remains low.

Database-managed permissions and custom tenant roles are intentionally deferred until product requirements justify the additional data model, validation, caching, and administration boundaries.

## Permission Enforcement

### Command

```text
RequireTenantPermissionCommand
- tenant_context
- permission
```

The permission is a `TenantPermission` enum value, not an arbitrary string.

### Result

```text
AuthorizedTenantContext
- user_id
- session_id
- tenant_id
- membership_id
- role
- granted_permission
```

### Enforcement Flow

```text
Receive trusted TenantContext
        |
        v
Read current persisted role from context
        |
        v
Resolve immutable role policy
        |
        v
Require requested permission
        |
        +---- denied
        |        |
        |        v
        |   TenantPermissionDeniedError
        |
        v
Return AuthorizedTenantContext
```

Permission denial uses a generic public message.

It does not reveal:

- the complete role policy;
- which role would be required;
- which other permissions are available.

## Why AuthorizedTenantContext Exists

Returning `AuthorizedTenantContext` makes the proven authorization decision explicit.

The result records:

```text
which global user
which authentication session
which tenant
which membership
which persisted role
which permission was granted
```

This creates a clear application-service boundary and can later support audit event construction without recomputing the permission decision.

## Read-Only Resolution

Authenticated principal and tenant context resolution do not use row locks.

They are read guards for normal request traffic.

Using `FOR UPDATE` on every protected request would:

- serialize unrelated reads;
- increase lock contention;
- extend transaction duration;
- reduce throughput;
- create avoidable deadlock surfaces.

Permission enforcement is a pure in-memory policy evaluation and does not access the database.

## Authorization and Mutation Races

Request authorization cannot guarantee that security state remains unchanged until a later mutation commits.

Example:

```text
Request resolves active admin membership
        |
        v
Another transaction disables the membership
        |
        v
Original request attempts a sensitive mutation
```

The correct boundary is:

```text
Request authorization
    -> early rejection
    -> trusted request context

Mutation service
    -> lock actor membership
    -> refresh persisted state
    -> revalidate actor authority
    -> lock target resource
    -> enforce domain invariants
    -> mutate
    -> flush without commit
```

Sensitive domain services must not trust an earlier read guard as their only authority.

## Current-State Recalculation

Authorization is recalculated for every new request using current PostgreSQL state.

The test suite validates these transitions with independent SQLAlchemy sessions.

### Session Revocation

```text
Request A resolves principal
Session becomes revoked and commits
Request B presents the same unexpired JWT
Request B is rejected
```

### User Disabling

```text
Request A resolves principal
User becomes disabled and commits
Request B is rejected
```

### Tenant Disabling

```text
Request A resolves tenant context
Tenant becomes disabled and commits
Request B cannot resolve tenant context
```

### Membership Disabling

```text
Request A resolves tenant context
Membership becomes disabled and commits
Request B cannot resolve tenant context
```

### Role Demotion

```text
Request A resolves admin role
member:invite is granted

Membership role changes to staff and commits

Request B resolves staff role
member:invite is denied
```

These transitions demonstrate why tenant authorization state is not embedded in access tokens.

## Failure Model

### Authentication Failures

```text
AccessTokenInvalidError
AccessTokenExpiredError
AuthenticationSessionNotFoundError
AuthenticationSessionInactiveError
AuthenticationSessionExpiredError
UserDisabledError
```

### Tenant Context Failures

```text
TenantNotFoundError
TenantDisabledError
TenantMembershipNotFoundError
TenantMembershipDisabledError
```

### Permission Failure

```text
TenantPermissionDeniedError
```

These exceptions:

- do not depend on FastAPI;
- do not contain HTTP status codes;
- do not disclose another user's membership;
- do not disclose the complete permission matrix;
- can be mapped by a future transport layer.

## Security Boundaries

The tenant authorization foundation establishes these rules:

- access token signature alone does not authorize a protected request;
- current authentication session state is validated server-side;
- current global user state is validated server-side;
- authentication sessions are bound to the JWT subject;
- tenant authorization state is not embedded in access JWTs;
- tenant selection is explicit;
- membership resolution is scoped by tenant ID and authenticated user ID;
- callers cannot supply trusted membership IDs;
- callers cannot supply trusted roles;
- callers cannot supply granted permissions;
- disabled tenants cannot authorize operations;
- disabled memberships cannot authorize operations;
- memberships in other tenants grant no authority;
- memberships belonging to other users grant no authority;
- permissions are derived from immutable code-based policy;
- permission denial is generic;
- authorization is recalculated from current state on each request;
- sensitive domain mutations must revalidate authority under locks.

## Scaling Considerations

Principal resolution performs one joined query over:

```text
auth_sessions
users
```

Tenant context resolution performs one query over:

```text
tenants
memberships
```

Existing keys and uniqueness constraints support:

- authentication session lookup by primary key;
- user ownership validation;
- tenant lookup by primary key;
- membership lookup by tenant and user.

The authorization policy is an in-process immutable mapping with no network or database dependency.

This design remains appropriate across multiple API instances because persisted lifecycle state remains authoritative in PostgreSQL.

A future cache would need explicit invalidation for:

- session revocation;
- session compromise;
- user disabling;
- tenant disabling;
- membership disabling;
- role changes.

Caching is intentionally deferred until measured request volume justifies that complexity.

## Intentionally Deferred

The following capabilities remain intentionally deferred:

- FastAPI authentication dependencies;
- FastAPI tenant-context dependencies;
- route-level permission guards;
- tenant ID transport through headers;
- tenant ID transport through URL paths;
- current-user endpoints;
- platform administrator roles;
- custom tenant roles;
- database-managed permission assignments;
- per-resource ownership rules;
- field-level authorization;
- billing-resource authorization details;
- audit-event persistence;
- authorization decision logging;
- permission caching;
- distributed policy engines;
- API keys;
- service accounts;
- impersonation;
- support-agent access;
- frontend route protection.

These capabilities can be introduced through explicit boundaries without changing the core trust chain.