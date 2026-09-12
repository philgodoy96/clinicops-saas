# ADR 0001: Application-Enforced Tenant Isolation vs PostgreSQL Row-Level Security

## Status

Accepted

## Context

ClinicOps is a multi-tenant SaaS modular monolith. Authenticated API and worker
processes own normal database access through controlled application services and
repositories.

Tenant-owned resources—patients, professionals, memberships, invitations,
billing customers and subscriptions, provider operations, and audit log
entries—are reached through tenant-aware service and repository boundaries.
Server-resolved membership and authorization establish the active tenant
context for human HTTP paths. Machine paths such as signed billing webhooks and
internal workers resolve tenant ownership from local durable records (for
example a subscription keyed by provider identifiers), not from untrusted
client or provider `tenant_id` claims.

The current threat model assumes that untrusted tenants do not hold arbitrary
direct SQL access to the shared PostgreSQL database. Isolation is therefore a
property of the authenticated application surface and its persistence
boundaries, not of database session policies.

## Decision

Enforce tenant isolation through:

- server-resolved tenant membership and authorization;
- tenant-qualified repository and query predicates for tenant-owned resources;
- database constraints for integrity (foreign keys, uniqueness, partial unique
  indexes), not for cross-tenant row visibility.

Do not add PostgreSQL Row-Level Security (RLS) under the current architecture
and threat model.

## Alternatives Considered

### PostgreSQL RLS as mandatory defense-in-depth

RLS would push tenant predicates into the database session. It remains a valid
stronger isolation option when the threat model or access surface changes. It
is not adopted now because the controlled query surface already applies tenant
qualification under the demonstrated operating model, and RLS would duplicate
policy while adding migration, session-role, and debugging complexity.

### Schema-per-tenant

Separate schemas increase operational and migration cost without matching the
current single-application, single-database deployment model.

### Database-per-tenant

Strong physical isolation with high operational overhead. Deferred until
tenancy density, compliance, or blast-radius requirements justify it.

### Authorization checks alone without tenant-qualified persistence access

Permission checks without `tenant_id` predicates on reads and mutations are
insufficient: a caller who passes a foreign resource id could still mutate
another tenant's row. Tenant-qualified repository access remains mandatory.

## Why Not RLS Now

- The authenticated application owns the query surface and already enforces
  tenant scope at repository boundaries for tenant-owned resources.
- Under the current threat model, RLS would duplicate policy already expressed
  in services and repositories.
- RLS introduces policy, role, and migration complexity that must stay in sync
  with application predicates.
- The stronger database-enforced visibility model is understood and
  intentionally deferred until revisit triggers below are met.

## Consequences

- The application and repository layer is part of the trusted computing base
  for tenant isolation.
- Direct SQL with credentials that can read or write arbitrary rows is outside
  this isolation guarantee.
- New repository methods and queries for tenant-owned resources must preserve
  tenant qualification.
- Cross-tenant access attempts must continue to be covered by automated tests
  on important paths.
- Database constraints continue to protect integrity (including at-most-one
  active owner) without implying database-enforced row visibility.

## Revisit Triggers

Reconsider PostgreSQL RLS or stronger physical isolation when any of the
following becomes true:

- additional services independently query the same database outside the
  controlled ClinicOps repositories;
- analysts, reporting tools, or operators gain direct SQL access to tenant
  tables;
- plugins or ad hoc query surfaces bypass controlled repositories;
- regulatory or contractual requirements demand defense-in-depth at the
  database visibility layer;
- the threat model requires the database to contain an application-layer
  compromise (for example stolen application credentials used for arbitrary
  SQL).

## Related Evidence

- Tenant authorization trust chain:
  `docs/architecture/tenant-authorization.md`
- Tenant-qualified repository rules:
  `ENGINEERING_GUIDE.md` (Repository Rules, Multi-Tenancy Rules)
- Partial unique index for active owners:
  `migrations/versions/0003_add_tenants_and_memberships.py`
  (`uq_memberships_one_active_owner_per_tenant`)
- Representative cross-tenant coverage:
  `tests/integration/api/test_tenant_api_isolation.py`,
  `tests/integration/patients/test_patient_lifecycle.py`,
  `tests/integration/professionals/test_professional_authorization_and_isolation.py`,
  `tests/integration/audit/test_audit_log_queries.py`,
  `tests/integration/tenancy/test_transfer_ownership.py`
