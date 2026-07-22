# Tenant API Authorization Model

## Purpose

ClinicOps exposes tenant-scoped capabilities through a protected HTTP boundary
that resolves current authorization state from PostgreSQL for every request.

The tenant API establishes a trusted progression from global authentication to
tenant-scoped authorization:

```text
Bearer access token
    -> AuthenticatedPrincipal
    -> selected tenant ID
    -> current persisted Tenant and Membership
    -> TenantContext
    -> required TenantPermission
    -> AuthorizedTenantContext
    -> tenant-scoped application operation
```

A tenant identifier in a URL selects a resource boundary. It does not establish
authorization by itself.

## API surface

The current tenant API is exposed under:

```text
/api/v1/tenants
```

Available operations:

```text
GET  /api/v1/tenants
GET  /api/v1/tenants/{tenant_id}
GET  /api/v1/tenants/{tenant_id}/memberships

GET  /api/v1/tenants/{tenant_id}/invitations
POST /api/v1/tenants/{tenant_id}/invitations
POST /api/v1/tenants/{tenant_id}/invitations/{invitation_id}/revoke

POST /api/v1/tenants/{tenant_id}/billing/subscription
```

## Global tenant discovery

`GET /api/v1/tenants` operates from the authenticated global principal.

The query returns only tenants for which the user currently has:

```text
an active membership
and
an active tenant
```

Each item includes the caller's current membership:

```json
{
  "id": "tenant-uuid",
  "name": "North Clinic",
  "status": "active",
  "current_membership": {
    "id": "membership-uuid",
    "role": "admin",
    "status": "active"
  }
}
```

The endpoint does not accept a tenant identifier because its purpose is to
discover which tenant contexts are currently available to the authenticated
user.

Disabled tenants and disabled memberships are intentionally excluded from this
global discovery response.

## Tenant-context resolution

Tenant-scoped operations use a reusable FastAPI dependency that combines:

```text
tenant_id from the route path
AuthenticatedPrincipal
request-scoped SQLAlchemy Session
ResolveTenantContextService
```

The dependency creates:

```text
ResolveTenantContextCommand(
    principal=authenticated_principal,
    tenant_id=path_tenant_id,
)
```

The application service resolves current persisted state and returns:

```text
TenantContext
    user_id
    session_id
    tenant_id
    membership_id
    role
```

The caller does not provide:

```text
user_id
membership_id
role
permissions
```

These values come from trusted authentication and current database state.

## Persisted authorization state

Tenant authorization is not embedded in access tokens.

Access tokens contain global user and authentication-session identity only.

For every tenant-scoped request, ClinicOps reloads:

```text
Tenant status
Membership existence
Membership status
Membership role
```

This ensures that changes affect the next request without requiring token
rotation.

Examples:

```text
ADMIN -> STAFF
    -> administrative permissions are removed on the next request

ACTIVE membership -> DISABLED membership
    -> tenant access is denied on the next request

ACTIVE tenant -> DISABLED tenant
    -> tenant access is denied on the next request
```

No tenant role or permission cache is shared across requests.

FastAPI may reuse a resolved dependency inside one request dependency graph, but
that cache is request-scoped only.

## Permission enforcement

Routes declare one explicit tenant capability using:

```text
require_tenant_permission(permission)
```

The dependency delegates to:

```text
RequireTenantPermissionService
```

The HTTP layer does not contain a role matrix.

It does not implement logic such as:

```text
if role is OWNER
if role is ADMIN
if role is STAFF
```

Role-to-permission mapping remains inside the authorization policy.

Current route mapping:

| Operation | Required permission |
|---|---|
| Read tenant details | `TENANT_READ` |
| List tenant memberships | `MEMBER_READ` |
| List tenant invitations | `INVITATION_READ` |
| Issue tenant invitation | `INVITATION_CREATE` |
| Revoke tenant invitation | `INVITATION_REVOKE` |
| Create billing subscription | `BILLING_MANAGE` |

The permission result is represented by:

```text
AuthorizedTenantContext
```

It contains the trusted tenant identity, current membership identity, current
role, and the permission proven for the operation.

## Cross-tenant isolation

Every tenant-owned query is scoped by the trusted tenant identifier.

Examples:

```text
Membership.tenant_id == AuthorizedTenantContext.tenant_id

Invitation.tenant_id == AuthorizedTenantContext.tenant_id
```

Knowing a valid tenant or invitation UUID does not grant access.

A user who belongs to tenant A cannot read tenant B through a path containing
tenant B's identifier.

A user who belongs to both tenant A and tenant B still cannot revoke an
invitation from tenant B through tenant A's route.

The invitation workflow resolves the invitation using both:

```text
tenant_id
invitation_id
```

A cross-tenant lookup therefore returns a public not-found result instead of
mutating another tenant's resource.

## Read application boundary

Tenant and invitation list operations use dedicated read-only query
repositories.

These repositories are separate from transactional repositories that acquire
row locks for state-changing workflows.

The read boundary follows:

```text
HTTP route
    -> query application service
    -> read-only query repository
    -> application read model
    -> Pydantic response schema
```

Routes do not return SQLAlchemy ORM models.

The separation is:

```text
SQLAlchemy entity
    -> application read model
    -> HTTP response schema
```

Read operations do not:

```text
acquire FOR UPDATE locks
flush
commit
mutate state
```

The request-scoped database dependency rolls back any implicit read transaction
during cleanup and closes the Session.

## Tenant and membership queries

### Tenant details

```text
GET /api/v1/tenants/{tenant_id}
```

Requires:

```text
TENANT_READ
```

The response includes tenant lifecycle fields and the caller's current trusted
membership.

### Membership list

```text
GET /api/v1/tenants/{tenant_id}/memberships
```

Requires:

```text
MEMBER_READ
```

The query returns both active and disabled memberships.

Including disabled memberships is intentional because tenant administrators
need lifecycle visibility for operational and administrative decisions.

Membership responses expose:

```text
membership ID
tenant ID
global user ID
role
status
created and updated timestamps
disabled timestamp
```

They do not expose authentication sessions, password credentials, access
tokens, refresh tokens, or identity secrets.

## Invitation queries

```text
GET /api/v1/tenants/{tenant_id}/invitations
```

Requires:

```text
INVITATION_READ
```

The query returns invitations in all lifecycle states:

```text
pending
accepted
revoked
expired
```

The public invitation representation excludes:

```text
plaintext invitation token
token digest
creator membership ID
accepted user ID
```

Administrative clients receive lifecycle status and timestamps without internal
security data.

## Invitation issuance

```text
POST /api/v1/tenants/{tenant_id}/invitations
```

Requires:

```text
INVITATION_CREATE
```

The request body contains only:

```json
{
  "invited_email": "member@example.com",
  "role": "staff"
}
```

The request does not accept:

```text
tenant_id
issuer_user_id
issuer membership ID
token
token digest
```

The command is built from the authorized context:

```text
IssueInvitationCommand(
    tenant_id=context.tenant_id,
    issuer_user_id=context.user_id,
    invited_email=request.invited_email,
    role=request.role,
)
```

Only `admin` and `staff` invitation roles are accepted.

The `owner` role cannot be assigned through the invitation workflow.

## One-time invitation token

Invitation issuance returns a plaintext token exactly once.

The application service generates the token and persists only its digest.

The HTTP route follows this order:

```text
validate workflow
    -> persist invitation and token digest
    -> flush
    -> commit
    -> return plaintext token
```

The plaintext token is not returned before the transaction commits.

The token field is excluded from Pydantic object representations while
remaining serializable in the successful issuance response.

Invitation list responses never contain a plaintext token or token digest.

## Invitation revocation

```text
POST /api/v1/tenants/{tenant_id}/invitations/{invitation_id}/revoke
```

Requires:

```text
INVITATION_REVOKE
```

The command uses trusted actor and tenant identity:

```text
RevokeInvitationCommand(
    tenant_id=context.tenant_id,
    invitation_id=path_invitation_id,
    actor_user_id=context.user_id,
)
```

The service validates tenant state, actor membership, actor role, invitation
ownership, and invitation lifecycle inside the transaction.

The route commits only after successful revocation.

## Billing subscription mutation

```text
POST /api/v1/tenants/{tenant_id}/billing/subscription
```

Requires:

```text
TenantPermission.BILLING_MANAGE
```

The route uses the existing tenant-context resolution and permission policy.

It does not authorize through a direct role comparison in the route.

### Billing permission matrix

```text
OWNER
    billing read: allowed
    billing manage: allowed

ADMIN
    billing read: allowed
    billing manage: denied

STAFF
    billing read: denied
    billing manage: denied
```

### Trusted identity and tenant selection

For billing mutations:

```text
the authenticated access token identifies the user and session
the token does not contain tenant billing authority
{tenant_id} comes from the route path
tenant and membership state are reloaded from persistence
disabled tenants and memberships remain subject to existing authorization
    failures
BILLING_MANAGE is evaluated against the resolved membership
the client cannot place another tenant ID in the request body
```

### Expected authorization outcomes

```text
tenant does not exist
    -> 404

active user has no tenant membership
    -> 404

tenant is disabled
    -> 403

membership is disabled
    -> 403

membership lacks BILLING_MANAGE
    -> 403
```

### Deferred billing routes

Future billing read routes should use:

```text
TenantPermission.BILLING_READ
```

Plan changes and cancellations are still deferred even though their eventual
mutations will also require `BILLING_MANAGE`.

## Authorization defense in depth

Invitation write routes perform two authorization stages.

### HTTP permission gate

The tenant API rejects callers who do not currently hold the required
capability.

This avoids unnecessary transactional work.

### Transactional service validation

The invitation service revalidates tenant, membership, role, and invitation
state while executing the state transition.

This protects:

```text
database invariants
concurrent state changes
non-HTTP callers
future background-job callers
```

The two layers serve different purposes and are intentionally retained.

## Transaction ownership

Application services flush state changes but do not commit.

Invitation routes own the final transaction result.

Successful issuance:

```text
IssueInvitationService
    -> flush

HTTP route
    -> commit
    -> return 201 response
```

Successful revocation:

```text
RevokeInvitationService
    -> flush

HTTP route
    -> commit
    -> return 200 response
```

When a service raises an expected failure, the route does not commit.

The request database dependency rolls back unfinished work during cleanup.

## Problem Details

Tenant and invitation failures use:

```text
application/problem+json
```

Tenant authorization mapping:

| Failure | Status |
|---|---:|
| Tenant or membership not found | `404` |
| Tenant disabled | `403` |
| Membership disabled | `403` |
| Permission denied | `403` |

Invitation mapping:

| Failure | Status |
|---|---:|
| Invitation not found | `404` |
| Invitation actor not authorized | `403` |
| Invitation issuer not authorized | `403` |
| Invitation already pending | `409` |
| Invited user already has membership | `409` |
| Invitation already accepted | `409` |
| Invitation revoked | `409` |
| Invitation expired | `409` |
| Invitation role not allowed | `400` |

A `404` response is used for cross-tenant resources so the API does not confirm
the existence of resources outside the selected tenant boundary.

A `409` response represents a valid command that conflicts with the current
resource lifecycle state.

Request-schema violations return:

```text
422 request_validation_error
```

For example, an invitation body containing a caller-controlled `tenant_id` is
rejected before application execution.

## OpenAPI behavior

Protected tenant routes declare the existing HTTP bearer security scheme.

Path tenant identifiers and invitation identifiers are represented as required
UUID parameters.

Invitation issuance declares:

```text
201 Created
```

Invitation revocation declares:

```text
200 OK
```

List and detail operations declare:

```text
200 OK
```

Tenant permissions remain internal application capabilities rather than OAuth
scopes.

## Verification strategy

The tenant API is covered through several layers.

### Unit tests

Unit coverage verifies:

```text
dependency command translation
permission dependency behavior
request-scoped dependency caching
query service mapping
absence of commits and flushes in reads
HTTP response translation
OpenAPI security declarations
Problem Details mappings
```

### PostgreSQL-backed integration tests

Integration coverage verifies:

```text
inactive access excluded from tenant discovery
cross-tenant path rejection
disabled tenant applied on the next request
disabled membership applied on the next request
role demotion applied on the next request
invitation list isolation
cross-tenant invitation revocation rejection
caller-controlled tenant field rejection
failed operations create no invitation state
```

These tests use real authentication, real persisted authorization state, real
invitation workflows, and committed setup data visible across HTTP request
sessions.

## Intentional boundaries

This milestone intentionally does not expose:

```text
tenant creation
invitation acceptance
membership role changes
membership activation or deactivation
membership removal
ownership transfer
billing plan changes
billing cancellations
billing read routes
patients
professionals
audit-log queries
```

Tenant onboarding and invitation acceptance require a separate authorization
flow because the invited user may not yet have a membership.

Membership administration and ownership transfer require dedicated
transactional invariants, including protection of the tenant's single active
owner.

Subscription creation is the only billing mutation exposed through the current
tenant API. Plan changes, cancellations, and billing reads remain deferred.

Those remaining capabilities are addressed in subsequent milestones instead of
being partially introduced into the current tenant read, invitation-
administration, and subscription-creation boundary.
