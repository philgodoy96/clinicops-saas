# Patients Domain

## Purpose

ClinicOps manages patients as tenant-scoped clinical records for small clinics and private practices.

A Patient is operational clinic data managed by tenant members. A Patient is not a User, is not a Membership, and does not authenticate. The platform is for tenant members; patients do not access it.

## Ownership Boundaries

The patients module owns:

- patient persistence;
- patient lifecycle state;
- tenant-scoped create, read, list, update, archive, and restore workflows;
- optimistic concurrency for mutations;
- patient validation and uniqueness rules;
- patient audit emission.

The patients module does not own:

- authentication or sessions;
- tenant membership administration;
- appointments or medical-record workflows;
- patient portal access;
- field-level encryption;
- bulk import or export;
- global or cross-tenant patient identity.

## Actors and Permissions

Tenant roles interact with patients through explicit `TenantPermission` values:

```text
patient:read
patient:create
patient:update
patient:archive
patient:restore
```

| Capability | OWNER | ADMIN | STAFF |
| --- | ---: | ---: | ---: |
| Create patient | Yes | Yes | Yes |
| Read patient | Yes | Yes | Yes |
| List patients | Yes | Yes | Yes |
| Update active patient | Yes | Yes | Yes |
| Archive patient | Yes | Yes | No |
| Restore patient | Yes | Yes | No |

Missing permission returns HTTP `403` through Problem Details.

## Patient Model

The `patients` table stores one tenant-owned clinical record.

Fields:

```text
id
tenant_id
full_name
date_of_birth
email
phone
external_reference
status
version
created_at
updated_at
```

Properties:

- `id` is a UUID;
- `tenant_id` references `tenants.id` with `ON DELETE RESTRICT`;
- `full_name` is required and non-blank;
- `date_of_birth`, `email`, `phone`, and `external_reference` are optional;
- `external_reference` is unique within a tenant when non-null;
- names, email, phone, and date of birth are not deduplication keys;
- `date_of_birth` cannot be in the future;
- `status` is `active` or `archived`;
- `version` starts at `1` and increments after each successful mutation;
- timestamps are timezone-aware.

There is no foreign key from Patient to User or Membership.

## Lifecycle States

```text
active
archived
```

Active patients may be updated, archived, or read.

Archived patients remain readable and listable when requested. They are read-only until restored. Restore returns the record to `active`.

No hard-delete endpoint exists.

## Invariants

- every patient belongs to exactly one tenant;
- repository and service boundaries require `tenant_id`;
- cross-tenant resource access returns not-found without disclosing existence;
- mutations require a positive `expected_version`;
- stale versions fail closed with conflict semantics;
- empty or no-op PATCH requests are rejected;
- archived patients reject field updates until restored;
- `external_reference` uniqueness is tenant-local and applies only when non-null.

## Persistence and Indexing

Migration `0010_add_patients` introduces:

```text
ck_patients_full_name_not_blank
ck_patients_version_positive
fk_patients_tenant_id_tenants
ix_patients_tenant_timeline
ix_patients_tenant_status_timeline
uq_patients_tenant_external_reference
```

`uq_patients_tenant_external_reference` is a partial unique index over `(tenant_id, external_reference)` when `external_reference` is non-null.

List ordering uses:

```text
created_at DESC, id DESC
```

## Create

```text
POST /api/v1/tenants/{tenant_id}/patients
```

Creates an active patient at version `1`.

Requires `patient:create`.

Request body:

```json
{
  "full_name": "Jordan Lee",
  "date_of_birth": "1992-08-14",
  "email": "jordan@example.com",
  "phone": "+1-202-555-0184",
  "external_reference": "LEGACY-100"
}
```

Only `full_name` is required.

## Read and List

```text
GET /api/v1/tenants/{tenant_id}/patients/{patient_id}
GET /api/v1/tenants/{tenant_id}/patients
```

Requires `patient:read`.

Listing uses keyset pagination with an opaque cursor.

```text
default limit: 50
maximum limit: 100
default status filter: active
status values: active, archived, all
order: created_at descending, then id descending
```

List response:

```json
{
  "items": [],
  "next_cursor": null
}
```

## Search

Optional `search` matches across:

```text
full_name
email
phone
external_reference
```

`%` and `_` are treated as literal characters rather than SQL wildcards.

## Partial Update Semantics

```text
PATCH /api/v1/tenants/{tenant_id}/patients/{patient_id}
```

Performs a partial update.

- omitted fields remain unchanged;
- explicit `null` clears nullable fields;
- `full_name` cannot be cleared;
- `expected_version` is required and must be at least `1`;
- empty or normalized no-op updates are rejected;
- archived patients cannot be edited.

Requires `patient:update`.

Example:

```json
{
  "expected_version": 3,
  "email": null
}
```

This explicitly clears `email`. Omitting `email` would preserve its current value.

## Optimistic Concurrency

Mutations use atomic SQL updates guarded by:

```text
tenant_id
patient id
required lifecycle status
expected version
```

Successful mutations increment `version`. Stale commands return HTTP `409`.

The domain does not use silent last-write-wins behavior.

## Archive and Restore

```text
POST /api/v1/tenants/{tenant_id}/patients/{patient_id}/archive
POST /api/v1/tenants/{tenant_id}/patients/{patient_id}/restore
```

Request body:

```json
{
  "expected_version": 1
}
```

Archive requires `patient:archive`. Restore requires `patient:restore`.

OWNER and ADMIN receive both permissions. STAFF receives neither.

## Tenant Isolation

Every repository query includes tenant predicates.

Authorization and repository scoping are complementary boundaries. A valid patient identifier from another tenant resolves as not found and does not disclose the foreign record.

## Transaction Ownership

```text
repository
    -> persistence only
    -> never commits

application service
    -> validates
    -> mutates
    -> records audit
    -> flushes

HTTP route
    -> commits once
```

Audit recording uses the same SQLAlchemy Session as the patient mutation. Audit failure prevents the mutation from committing, and rollback removes both pending records.

## Audit Events and Safe Metadata

Actions:

```text
patient.created
patient.updated
patient.archived
patient.restored
```

Resource type:

```text
patient
```

Approved metadata contains operational facts only:

- status;
- resulting version;
- changed field names;
- previous and resulting status.

Audit metadata intentionally excludes:

- full name;
- email;
- phone;
- date of birth;
- external-reference value.

Idempotency keys:

```text
patient-created:{patient_id}
patient-updated:{patient_id}:{version}
patient-archived:{patient_id}:{version}
patient-restored:{patient_id}:{version}
```

Request observability uses `X-Request-ID` and `X-Correlation-ID`, propagated into audit attribution for authenticated HTTP mutations.

## HTTP Transport

All patient routes are tenant-scoped under `/api/v1`.

Requests require:

```text
Authorization: Bearer <access-token>
```

The authenticated user must have an active membership in the path tenant and the permission required by the operation.

## Error Semantics

Patient errors use:

```text
application/problem+json
```

| Status | Meaning |
| ---: | --- |
| `400` | Invalid cursor, invalid date of birth, invalid or no-op update |
| `403` | Missing tenant permission |
| `404` | Patient not found or cross-tenant record hidden |
| `409` | Stale version, invalid lifecycle transition, or external-reference conflict |
| `422` | Request-schema validation failure |

Problem Details include request and correlation identifiers.

## Verification Strategy

Coverage includes:

- persistence constraints;
- repository tenant isolation;
- keyset pagination;
- search across approved fields;
- literal `%` and `_` handling;
- optimistic concurrency;
- archive and restore lifecycle;
- audit transactionality and rollback coupling;
- authenticated HTTP flows;
- OWNER, ADMIN, and STAFF role behavior;
- Problem Details mapping.

Tests validate persisted outcomes against PostgreSQL rather than checking status codes alone.

## Scaling Considerations

Tenant timeline indexes support keyset pagination without offset scans.

The partial unique index protects tenant-local external references.

Horizontal API scaling remains safe because authorization, lifecycle guards, uniqueness, and version checks depend on PostgreSQL state rather than process-local coordination.

## Security and Privacy

- patients do not authenticate;
- patient data is tenant-scoped;
- audit metadata excludes patient PII;
- application logs must not copy unnecessary patient data;
- cross-tenant resource identifiers do not reveal existence;
- field-level encryption is intentionally outside this project phase.

## Intentionally Deferred

```text
patient login and patient portal
hard delete
global patient identity or cross-tenant patient sharing
automatic deduplication by PII
field-level encryption
bulk import and export
frontend surfaces
medical-record workflows
appointment workflows
```

OAuth and other identity-provider work remain separate platform concerns and are outside this domain boundary.

## Future Evolution

Appointments and richer clinical workflows can reference tenant-scoped patient identifiers without changing the Patient-versus-User separation established here.