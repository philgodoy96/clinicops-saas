# Tenant Onboarding Model

## Purpose

ClinicOps supports two onboarding paths:

```text
Authenticated global user
    -> create a tenant
    -> become the initial owner

Invitation recipient
    -> present an invitation token
    -> join an existing tenant
```

Both workflows are transactional and preserve tenant ownership and membership
invariants.

The onboarding API is intentionally separate from tenant administration.
Creating a tenant does not require an existing tenant context, and accepting an
invitation happens before the recipient has a tenant membership.

## API surface

The onboarding boundary exposes:

```text
POST /api/v1/tenants
POST /api/v1/invitations/accept
```

`POST /api/v1/tenants` requires bearer authentication.

`POST /api/v1/invitations/accept` does not require bearer authentication. The
opaque invitation token is the credential for that single workflow.

## Tenant creation

### Request

```http
POST /api/v1/tenants
Authorization: Bearer <access-token>
Content-Type: application/json
```

```json
{
  "name": "Northstar Health Clinic"
}
```

The request cannot provide:

```text
owner user ID
owner membership ID
owner role
tenant status
```

The initial owner identity comes from the authenticated global principal.

### Trusted command translation

The HTTP route creates:

```text
CreateTenantCommand(
    name=request.name,
    owner_user_id=principal.user_id,
)
```

The request schema normalizes surrounding whitespace and enforces the public
tenant-name length boundary.

The application service remains responsible for canonical tenant-name
validation.

### Transactional workflow

The application service:

```text
normalizes the tenant name
locks the owner user
verifies that the owner exists
verifies that the owner is active
creates the tenant
creates the initial owner membership
flushes the transaction
returns CreatedTenant
```

The HTTP route then commits the transaction.

The route does not implement tenant creation invariants itself.

### Ownership invariant

Tenant creation establishes:

```text
exactly one active owner membership
```

The creator becomes the initial owner.

Tenant and owner membership creation happen in the same transaction.

A later failure before commit rolls back both records.

The database partial unique index protecting one active owner per tenant remains
the final persistence-level safeguard.

### Response

A successful request returns:

```text
201 Created
```

```json
{
  "id": "tenant-uuid",
  "name": "Northstar Health Clinic",
  "status": "active",
  "owner_user_id": "user-uuid",
  "created_at": "2026-08-21T15:00:00Z"
}
```

The response reflects the existing `CreatedTenant` application result.

It does not introduce a synthetic membership identifier that the application
service does not return.

## Invitation acceptance

### Route placement

Invitation acceptance is exposed at:

```text
POST /api/v1/invitations/accept
```

It is not nested under:

```text
/api/v1/tenants/{tenant_id}
```

The recipient does not yet have a trusted tenant context.

The invitation token identifies the invitation and the tenant-scoped transition
that may be performed.

### Request

```http
POST /api/v1/invitations/accept
Content-Type: application/json
```

Existing-user example:

```json
{
  "token": "opaque-invitation-token"
}
```

New-user example:

```json
{
  "token": "opaque-invitation-token",
  "password": "Correct-Horse-Battery-Staple-2026!"
}
```

The request cannot provide:

```text
tenant ID
invitation ID
invited email
membership role
membership ID
accepted user ID
invitation status
```

Those values come from the persisted invitation and the resolved identity.

### Secret handling

The request schema marks both:

```text
token
password
```

as excluded from Pydantic object representations.

Whitespace is preserved for both secrets.

The API does not:

```text
log the plaintext token
return the token after acceptance
return the password
return the token digest
```

Only the token digest is used for persistence lookup.

### Trusted command translation

The HTTP route creates:

```text
AcceptInvitationCommand(
    token=request.token,
    password=request.password,
)
```

No tenant identity or membership role is accepted from the caller.

### Existing global user

When the invitation email resolves to an existing active global user:

```text
the existing user is reused
no password is required
the password field is ignored when supplied
a tenant membership is created
user_was_created is false
```

Acceptance is bound to the invitation's persisted email.

The request cannot submit a different login or recipient email.

### New global user

When no global user exists for the invitation email:

```text
a password is required
CreateUserService creates the user
the existing Argon2 password boundary is reused
the new user receives the invited tenant membership
user_was_created is true
```

Password policy remains enforced by the identity application layer.

The HTTP schema mirrors the current public length range but does not replace
application-layer validation.

### Transactional workflow

The acceptance service performs:

```text
digest plaintext token
resolve invitation by digest
lock tenant
lock invitation
revalidate token digest
validate invitation lifecycle
validate tenant status
lock or create invited user
check existing membership
create membership using invitation role
mark invitation accepted
record accepted user and timestamp
flush
```

The HTTP route commits only after successful application execution.

A failure after creating a new user but before commit rolls back:

```text
the user
the password credential
the membership
the invitation transition
```

### Membership role

The membership role comes from the invitation.

The acceptance request cannot choose or elevate the role.

The invitation model and issuance workflow prevent the owner role from being
assigned through invitations.

Ownership transfer is therefore not possible through invitation acceptance.

### Response

A successful request returns:

```text
200 OK
```

```json
{
  "invitation_id": "invitation-uuid",
  "tenant_id": "tenant-uuid",
  "membership_id": "membership-uuid",
  "user_id": "user-uuid",
  "role": "staff",
  "user_was_created": true,
  "accepted_at": "2026-08-22T15:00:00Z"
}
```

The response contains no invitation credential or password data.

## Invitation lifecycle validation

Acceptance requires all of the following:

```text
the token resolves to an invitation
the token digest still matches the locked invitation
the tenant exists
the tenant is active
the invitation is pending
the invitation is not expired by status
the invitation has not reached expires_at
the invitation is not revoked
the invitation has not already been accepted
the user does not already have a tenant membership
```

The service rejects:

```text
invalid token
expired invitation
revoked invitation
accepted-token replay
disabled tenant
disabled existing user
existing membership
missing password for a new user
```

## Authentication and authorization boundaries

### Tenant creation

Tenant creation uses:

```text
Bearer access token
    -> AuthenticatedPrincipal
    -> CreateTenantCommand
```

It does not use `TenantContext` because no tenant exists yet.

### Invitation acceptance

Invitation acceptance uses:

```text
opaque invitation token
    -> AcceptInvitationCommand
```

It does not use:

```text
Bearer authentication
TenantContext
TenantPermission
```

The token authorizes only the acceptance transition.

It does not grant permission to:

```text
read tenant details
list memberships
manage invitations
manage billing
access audit logs
```

After acceptance commits, future authenticated requests can resolve tenant
access through the newly created membership.

## Transaction ownership

ClinicOps application services flush but do not commit.

The onboarding routes own the final transaction outcome.

Tenant creation:

```text
CreateTenantService
    -> flush tenant and owner membership

HTTP route
    -> commit
    -> return 201
```

Invitation acceptance:

```text
AcceptInvitationService
    -> flush user, membership, and invitation state

HTTP route
    -> commit
    -> return 200
```

When a service raises an expected application failure:

```text
the route does not commit
the database dependency rolls back
no partial onboarding state remains
```

## Concurrency model

### Concurrent invitation acceptance

Two requests may attempt to consume the same token.

The workflow locks the tenant and invitation before applying the transition.

Valid outcome:

```text
one request returns 200
one request returns 409 invitation_already_accepted
exactly one user exists for a newly invited email
exactly one membership exists
the invitation is accepted once
```

The unique membership constraint on:

```text
(user_id, tenant_id)
```

provides a final persistence-level safeguard.

### Acceptance versus revocation

Invitation acceptance and revocation may race.

Valid terminal outcomes:

```text
acceptance wins
    -> invitation accepted
    -> one membership created
    -> revocation returns conflict

revocation wins
    -> invitation revoked
    -> no membership created
    -> acceptance returns conflict
```

Both operations lock the tenant before the invitation, preserving a consistent
lock order.

The system must never commit both terminal transitions.

### Concurrent tenant creation

The same authenticated user may create independent tenants concurrently.

Each successful transaction creates:

```text
one tenant
one active owner membership for that tenant
```

The user row lock may serialize part of execution, but the operations remain
independent and valid.

## Persistence constraints

The onboarding workflows rely on database constraints as final safeguards.

### Membership uniqueness

```text
uq_memberships_user_id_tenant_id
```

Protects one membership per global user and tenant.

### Active owner uniqueness

```text
uq_memberships_one_active_owner_per_tenant
```

Protects one active owner per tenant.

### Invitation token uniqueness

```text
uq_invitations_token_digest
```

Protects uniqueness of invitation token digests.

### Pending invitation uniqueness

```text
uq_invitations_one_pending_per_tenant_email
```

Protects one pending invitation per tenant and invited email.

### Invitation accepted state

```text
ck_invitations_accepted_state
```

Requires accepted invitations to contain accepted-user and accepted-time data,
while non-accepted invitations cannot contain those fields.

### Invitation revoked state

```text
ck_invitations_revoked_state
```

Keeps revoked timestamp state consistent with invitation status.

### Invitation role

```text
ck_invitations_role_not_owner
```

Prevents the owner role from being assigned through the invitation lifecycle.

## Problem Details

Onboarding failures use:

```text
application/problem+json
```

Current mapping:

| Failure | Status |
|---|---:|
| Invalid tenant name | `400` |
| Invalid invitation token | `400` |
| Password required for new user | `400` |
| Owner user not found | `404` |
| Disabled owner or invited user | `401` |
| Disabled tenant | `403` |
| Invitation already accepted | `409` |
| Invitation revoked | `409` |
| Invitation expired | `409` |
| Existing tenant membership | `409` |
| Invalid HTTP request schema | `422` |

An invalid invitation token does not return a bearer challenge because the
acceptance route is not authenticated through HTTP bearer credentials.

Lifecycle failures use `409` because the request conflicts with the current
state of the invitation or membership.

## OpenAPI behavior

Tenant creation declares:

```text
POST /api/v1/tenants
201 Created
HTTP bearer security
```

Invitation acceptance declares:

```text
POST /api/v1/invitations/accept
200 OK
no HTTP bearer security requirement
```

FastAPI also documents request-validation failures.

The OpenAPI contract does not expose invitation tokens as reusable
authentication credentials.

## Verification strategy

### Contract tests

Schema tests verify:

```text
tenant-name normalization
caller-controlled owner rejection
caller-controlled tenant status rejection
secret representation masking
optional password support
password length boundary
caller-controlled invitation identity rejection
credential exclusion from responses
```

### Route tests

Route tests verify:

```text
trusted command translation
request Session forwarding
route-owned commit after success
no commit after validation failure
no commit after application failure
bearer security for tenant creation
public invitation acceptance operation
absence of credential fields in responses
```

### Application integration tests

Existing PostgreSQL-backed application tests verify:

```text
tenant and initial owner persistence
owner validation
transaction rollback
existing-user acceptance
new-user creation
password requirements
invitation lifecycle failures
tenant and user status failures
existing membership rejection
acceptance rollback
```

### HTTP concurrency tests

PostgreSQL-backed HTTP tests verify:

```text
concurrent acceptance creates one user and membership
accepted-token replay returns a stable conflict
concurrent tenant creation preserves one owner per tenant
acceptance and revocation produce one terminal result
no membership exists after revocation wins
```

## Observability and sensitive data

Request and correlation identifiers remain available through the standard HTTP
middleware and Problem Details responses.

Application and HTTP logging must not include:

```text
plaintext invitation token
plaintext password
password hash
invitation token digest
```

The onboarding API returns stable public error codes without exposing internal
database or credential details.

## Intentional boundaries

This milestone intentionally does not implement:

```text
invitation email delivery
invitation resend
invitation inspection by plaintext token
automatic login after invitation acceptance
automatic tenant selection after acceptance
membership role changes
membership disable or enable
membership removal
ownership transfer
OAuth onboarding
billing subscription creation
browser onboarding UI
```

Email delivery is separated from invitation lifecycle persistence so provider
integration, retries, and delivery failures do not weaken the transactional
onboarding model.

Automatic login after acceptance is deferred because authentication-session
issuance is a separate security transition. The accepted user can authenticate
through the existing login flow.

Membership administration and ownership transfer require dedicated
authorization and ownership invariants. They are addressed in the next
milestone rather than being partially introduced into onboarding.