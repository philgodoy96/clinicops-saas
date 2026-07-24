# Professionals API

## Overview

The Professionals API manages tenant-owned clinical provider profiles.

A Professional is an operational clinic record. It is intentionally separate
from platform identity and tenant authorization:

* a Professional may exist without a Membership;
* a Membership may exist without a Professional;
* a Professional may be linked to at most one Membership;
* a Membership may be linked to at most one Professional;
* linking a Membership does not change its role, status, or access;
* unlinking a Membership does not disable or remove it.

All endpoints are tenant-scoped and require bearer authentication.

Base path:

```text
/api/v1/tenants/{tenant_id}/professionals
```

## Authorization

| Operation         | OWNER | ADMIN | STAFF |
| ----------------- | ----: | ----: | ----: |
| Create            |   Yes |   Yes |    No |
| List              |   Yes |   Yes |   Yes |
| Get               |   Yes |   Yes |   Yes |
| Update            |   Yes |   Yes |    No |
| Archive           |   Yes |   Yes |    No |
| Restore           |   Yes |   Yes |    No |
| Link Membership   |   Yes |   Yes |    No |
| Unlink Membership |   Yes |   Yes |    No |

Stable permissions:

```text
professional:read
professional:create
professional:update
professional:archive
professional:restore
professional:link_membership
professional:unlink_membership
```

## Resource representation

```json
{
  "id": "0adf730d-6077-4876-9fb1-79e67a822ef5",
  "tenant_id": "4f68aba6-b6fd-407d-ac12-f29acf815bdc",
  "membership_id": null,
  "full_name": "Morgan Reed",
  "specialty": "Dentistry",
  "registration_number": "DDS-48291",
  "registration_region": "CA",
  "email": "morgan@example.com",
  "phone": "+1-202-555-0130",
  "external_reference": "PROVIDER-100",
  "status": "active",
  "version": 1,
  "created_at": "2026-07-24T18:00:00Z",
  "updated_at": "2026-07-24T18:00:00Z"
}
```

`membership_id` is nullable because platform access and clinical identity are
modeled independently.

## Create a Professional

```http
POST /api/v1/tenants/{tenant_id}/professionals
Authorization: Bearer <access-token>
Content-Type: application/json
```

Request:

```json
{
  "full_name": "Morgan Reed",
  "specialty": "Dentistry",
  "registration_number": "DDS-48291",
  "registration_region": "CA",
  "email": "morgan@example.com",
  "phone": "+1-202-555-0130",
  "external_reference": "PROVIDER-100"
}
```

Response:

```text
201 Created
```

Creation does not link a Membership. Association is an explicit, separately
authorized workflow.

## List Professionals

```http
GET /api/v1/tenants/{tenant_id}/professionals
Authorization: Bearer <access-token>
```

Query parameters:

| Parameter | Default  | Rules                          |
| --------- | -------- | ------------------------------ |
| `limit`   | `50`     | Minimum `1`, maximum `100`     |
| `status`  | `active` | `active`, `archived`, or `all` |
| `search`  | none     | Maximum 100 characters         |
| `cursor`  | none     | Opaque keyset cursor           |

Example:

```http
GET /api/v1/tenants/{tenant_id}/professionals?status=all&search=morgan&limit=25
```

Response:

```json
{
  "items": [
    {
      "id": "0adf730d-6077-4876-9fb1-79e67a822ef5",
      "tenant_id": "4f68aba6-b6fd-407d-ac12-f29acf815bdc",
      "membership_id": null,
      "full_name": "Morgan Reed",
      "specialty": "Dentistry",
      "registration_number": "DDS-48291",
      "registration_region": "CA",
      "email": "morgan@example.com",
      "phone": "+1-202-555-0130",
      "external_reference": "PROVIDER-100",
      "status": "active",
      "version": 1,
      "created_at": "2026-07-24T18:00:00Z",
      "updated_at": "2026-07-24T18:00:00Z"
    }
  ],
  "next_cursor": null
}
```

The ordering is stable:

```text
created_at DESC
id DESC
```

The cursor is an opaque transport value. Clients must return it unchanged.

Search covers:

```text
full_name
specialty
registration_number
email
phone
external_reference
```

Search is case-insensitive. Literal `%` and `_` characters are escaped and do
not become SQL wildcard operators.

## Get a Professional

```http
GET /api/v1/tenants/{tenant_id}/professionals/{professional_id}
Authorization: Bearer <access-token>
```

Response:

```text
200 OK
```

Archived Professionals remain readable.

A missing Professional and a Professional owned by another tenant both produce
the same public not-found response.

## Update a Professional

```http
PATCH /api/v1/tenants/{tenant_id}/professionals/{professional_id}
Authorization: Bearer <access-token>
Content-Type: application/json
```

Request:

```json
{
  "expected_version": 1,
  "specialty": "Orthodontics",
  "email": "updated@example.com"
}
```

Response:

```text
200 OK
```

PATCH is semantic:

* omitted fields are preserved;
* an explicitly supplied `null` clears a nullable field;
* `full_name` cannot be cleared;
* normalized no-op updates are rejected;
* only active Professionals may be updated.

The request must include the version observed by the client. A stale version
returns `409 Conflict`.

## Archive a Professional

```http
POST /api/v1/tenants/{tenant_id}/professionals/{professional_id}/archive
Authorization: Bearer <access-token>
Content-Type: application/json
```

Request:

```json
{
  "expected_version": 2
}
```

Response:

```text
200 OK
```

Archiving:

* changes status from `active` to `archived`;
* increments the version;
* preserves `membership_id`;
* does not disable or remove the linked Membership.

## Restore a Professional

```http
POST /api/v1/tenants/{tenant_id}/professionals/{professional_id}/restore
Authorization: Bearer <access-token>
Content-Type: application/json
```

Request:

```json
{
  "expected_version": 3
}
```

Response:

```text
200 OK
```

Restoring:

* changes status from `archived` to `active`;
* increments the version;
* preserves `membership_id`;
* does not reactivate a disabled Membership.

## Link a Membership

```http
POST /api/v1/tenants/{tenant_id}/professionals/{professional_id}/link-membership
Authorization: Bearer <access-token>
Content-Type: application/json
```

Request:

```json
{
  "membership_id": "745e8497-a4c1-40ba-8ee1-ad274d66363d",
  "expected_version": 4
}
```

Response:

```text
200 OK
```

The link succeeds only when:

* the Professional is active;
* the Professional is currently unlinked;
* the Membership belongs to the same tenant;
* the Membership is active;
* the Membership is not linked to another Professional;
* the expected Professional version is current.

The Membership row is locked while its eligibility is validated.

## Unlink a Membership

```http
POST /api/v1/tenants/{tenant_id}/professionals/{professional_id}/unlink-membership
Authorization: Bearer <access-token>
Content-Type: application/json
```

Request:

```json
{
  "expected_version": 5
}
```

Response:

```text
200 OK
```

Explicit unlink:

* requires an active Professional;
* clears `membership_id`;
* increments the Professional version;
* does not mutate or remove the Membership.

## Membership removal behavior

Removing a linked Membership through the tenancy workflow preserves the
Professional:

```text
linked Professional
    -> Membership removal starts
    -> Professional membership_id is cleared
    -> Professional version increments
    -> Membership is removed
```

The unlink and Membership removal occur in the same database transaction.

This rule applies to both active and archived Professionals.

## Optimistic concurrency

All Professional mutations use atomic predicates containing the expected
version. A successful mutation increments the version exactly once.

Example:

```text
client A observes version 4
client B observes version 4

client A updates with expected_version=4
    -> succeeds
    -> version becomes 5

client B updates with expected_version=4
    -> 409 Conflict
    -> no state change
    -> no audit entry
```

Clients should refresh the resource after a version conflict.

## Error format

Errors use RFC 9457-style Problem Details with:

```text
Content-Type: application/problem+json
```

Example:

```json
{
  "type": "about:blank",
  "title": "Resource conflict",
  "status": 409,
  "detail": "The Professional version is no longer current.",
  "code": "professional_version_conflict",
  "request_id": "req-01J2YX8NCY1B2YHTWDQM84VZQH",
  "correlation_id": "corr-01J2YX8NCY1B2YHTWDQM84VZQH"
}
```

Common statuses:

| Status | Meaning                                                 |
| ------ | ------------------------------------------------------- |
| `400`  | Invalid cursor, registration data, or semantic update   |
| `401`  | Authentication required                                 |
| `403`  | Tenant permission denied                                |
| `404`  | Professional or eligible Membership not visible         |
| `409`  | Lifecycle, uniqueness, association, or version conflict |
| `422`  | Request schema or query validation failed               |

## Audit events

Successful mutations record:

```text
professional.created
professional.updated
professional.archived
professional.restored
professional.membership_linked
professional.membership_unlinked
```

Audit writes share the same database transaction as the Professional mutation.

Audit metadata intentionally excludes:

```text
full_name
specialty values
registration data
email
phone
external_reference
Membership user identity
Membership role
```

Association events may include the Membership UUID, resulting Professional
version, and unlink reason.

## Intentional scope

The API intentionally focuses on tenant-owned provider records, lifecycle,
authorization, auditability, and identity association.

The following capabilities are deferred:

* appointment availability;
* scheduling;
* credential verification;
* licensing authority integrations;
* payroll and compensation;
* provider portals;
* global provider identity resolution;
* bulk import and export;
* frontend workflows.

The domain boundaries preserve room for these capabilities without coupling
them to the current core model.
