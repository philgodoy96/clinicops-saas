# Professionals Domain Architecture

## Purpose

The Professionals Domain represents clinical providers managed by a tenant.

It is responsible for:

* tenant-owned provider profiles;
* profile lifecycle;
* tenant-safe lookup and listing;
* semantic partial updates;
* optional Membership association;
* optimistic concurrency;
* durable audit records;
* safe behavior when a linked Membership is removed.

It is not responsible for platform authentication, tenant authorization,
appointments, payroll, or external credential verification.

## Domain boundary

`Professional` and `Membership` solve different problems:

```text
Professional
    -> clinical and operational provider profile

Membership
    -> platform access, tenant role, and authorization state
```

They are deliberately separate.

This supports:

* a provider record before platform onboarding;
* a staff account that is not a clinical provider;
* removal of platform access without deleting operational history;
* archive and restore independent from authentication lifecycle.

## Core entity

```text
Professional
├── id
├── tenant_id
├── membership_id?
├── full_name
├── specialty?
├── registration_number?
├── registration_region?
├── email?
├── phone?
├── external_reference?
├── status
├── version
├── created_at
└── updated_at
```

Lifecycle:

```text
active
    -> archive
    -> archived
    -> restore
    -> active
```

There is no public hard-delete operation.

## Invariants

### Tenant ownership

Every Professional belongs to exactly one tenant.

Every repository operation that targets an existing Professional includes the
tenant identifier in its predicate.

A resource owned by another tenant is not distinguishable from a missing
resource at the public item boundary.

### Optional one-to-one Membership association

```text
Professional.membership_id
    -> nullable
    -> unique when present
```

Rules:

* a Professional may have zero or one Membership;
* a Membership may have zero or one Professional;
* the Membership must belong to the same tenant;
* explicit linking requires an active Membership;
* linking does not modify Membership authorization state;
* explicit unlinking does not modify or remove the Membership.

The database enforces tenant consistency with a composite foreign key:

```text
(professionals.tenant_id, professionals.membership_id)
    -> memberships(tenant_id, id)
```

The Membership table exposes the corresponding unique key required by the
composite reference.

### External reference uniqueness

`external_reference` is optional and unique within a tenant when present.

It is not globally unique because external systems may reuse identifiers across
clinics.

### Registration fields

`registration_region` is normalized to uppercase.

`registration_number` preserves its original case and is not declared globally
or tenant-locally unique. Uniqueness cannot be safely asserted without a
credential authority namespace and credential-type model.

### Active-only mutations

Archived Professionals are read-only.

They may be:

* retrieved;
* included in archived or all listings;
* restored.

They may not be:

* patched;
* explicitly linked;
* explicitly unlinked;
* archived again.

Membership removal is an exception to the explicit unlink lifecycle rule
because referential integrity must be preserved for active and archived
Professionals.

### Versioning

Every successful mutation increments `version` exactly once.

Mutations require the version observed by the caller and use atomic SQL
predicates:

```text
tenant_id
professional_id
expected_version
expected lifecycle state
expected association state
```

A stale mutation produces a conflict and writes no audit event.

## Application workflows

### Create

```text
validate and normalize input
    -> construct unlinked active Professional
    -> add
    -> flush
    -> record professional.created
    -> caller commits
```

Creation does not accept `membership_id`.

### Read

```text
tenant_id + professional_id
    -> ProfessionalRecord or not found
```

Active and archived Professionals are readable.

### List

```text
tenant scope
    -> lifecycle filter
    -> escaped search
    -> keyset position
    -> limit + 1 fetch
    -> page and next cursor
```

Ordering:

```text
created_at DESC
id DESC
```

The service converts between the opaque external cursor and the structured
repository cursor.

### Update

```text
load current record
    -> validate version
    -> require active
    -> validate explicit field set
    -> normalize requested values
    -> remove normalized no-op fields
    -> atomic update
    -> classify failure
    -> record professional.updated
```

Omitted fields and explicit `null` values have distinct meanings.

### Archive and restore

Archive and restore are explicit commands, not generic PATCH operations.

Both are:

* tenant-scoped;
* version-guarded;
* lifecycle-guarded;
* audited;
* transactionally committed by the route.

Neither operation changes the linked Membership.

### Explicit Membership link

```text
load Professional
    -> validate version
    -> require active and unlinked
    -> lock same-tenant Membership
    -> require active Membership
    -> atomic link
    -> record professional.membership_linked
```

The row lock prevents a concurrent Membership deactivation or removal from
invalidating a previously checked association.

Database uniqueness remains the final authority for concurrent attempts to
link the same Membership.

### Explicit Membership unlink

```text
load Professional
    -> validate version
    -> require active and linked
    -> capture previous Membership id
    -> atomic unlink
    -> record professional.membership_unlinked
```

The Membership remains unchanged.

### Membership removal integration

The tenancy removal workflow delegates to a narrow Professionals service:

```text
validate and lock target Membership
    -> unlink matching Professional by tenant and Membership
    -> increment Professional version
    -> record Professional unlink audit
    -> delete Membership
    -> record Membership removal audit
    -> caller commits
```

The Professionals service receives the caller-owned Session. This preserves a
single transaction without allowing the tenancy service to mutate Professional
ORM fields directly.

No linked Professional is a valid no-op.

## Persistence design

The repository:

* owns SQL construction and constraint translation;
* applies tenant predicates;
* exposes no commit or rollback;
* returns immutable domain records;
* uses `UPDATE ... RETURNING` for guarded mutations;
* escapes `%`, `_`, and the escape character for literal search;
* translates known constraint failures into application errors.

Services:

* validate business invariants;
* coordinate repositories;
* classify mutation failures;
* record audit facts;
* do not commit or roll back.

Routes:

* enforce authentication and authorization;
* build commands and trusted audit attribution;
* commit exactly once after successful mutations;
* do not commit reads.

## Failure classification

A guarded mutation that affects no row is reclassified using a fresh
tenant-scoped read:

```text
record missing
    -> not found

version changed
    -> version conflict

lifecycle changed
    -> lifecycle conflict

association state changed
    -> association conflict
```

Database constraint translation covers:

* tenant-local external reference conflicts;
* Membership uniqueness conflicts;
* Membership foreign-key disappearance.

## Authorization

Permissions are capability-based:

```text
professional:read
professional:create
professional:update
professional:archive
professional:restore
professional:link_membership
professional:unlink_membership
```

Role grants:

```text
OWNER
    -> all capabilities

ADMIN
    -> all capabilities

STAFF
    -> read only
```

Authorization executes before the Professional workflow.

## Audit model

Actions:

```text
professional.created
professional.updated
professional.archived
professional.restored
professional.membership_linked
professional.membership_unlinked
```

Audit entries include:

* tenant;
* actor;
* source;
* action;
* Professional resource identifier;
* request identifier;
* correlation identifier;
* metadata version;
* safe operational metadata;
* idempotency key.

Profile values are intentionally excluded from audit metadata.

Examples of safe metadata:

```json
{
  "version": 4,
  "changed_fields": [
    "specialty",
    "email"
  ]
}
```

```json
{
  "membership_id": "745e8497-a4c1-40ba-8ee1-ad274d66363d",
  "reason": "membership_removal",
  "version": 6
}
```

An audit failure aborts the mutation transaction.

## Security considerations

### Tenant isolation

Tenant ownership is enforced in authorization, repository predicates, foreign
keys, uniqueness scopes, tests, and public error behavior.

### Information disclosure

Foreign-tenant Professionals are exposed as not found.

Audit metadata avoids provider profile values.

Constraint errors are translated to stable public errors without exposing SQL,
constraint internals, or foreign tenant details.

### Authentication versus clinical identity

Linking a Professional to a Membership does not grant access. Access is still
controlled exclusively by Membership status and role.

### Input boundaries

The API:

* forbids unknown JSON fields;
* bounds string and pagination sizes;
* validates UUIDs and expected versions;
* uses semantic PATCH field presence;
* rejects malformed cursors;
* uses parameterized SQLAlchemy statements.

## Scaling considerations

The current design uses a shared-schema multi-tenant model with tenant-scoped
indexes and predicates.

This is appropriate for the current modular monolith because it provides:

* simple transactional joins;
* consistent migrations;
* efficient tenant-local queries;
* atomic Membership integration;
* lower operational complexity than schema-per-tenant provisioning.

Keyset pagination avoids large offset scans.

Search currently uses bounded `ILIKE` predicates. As data volume grows, search
can move to purpose-built indexes or a dedicated search system without changing
the external resource model.

## Testing strategy

Coverage includes:

* persistence constraints;
* normalization and contracts;
* tenant-scoped repository behavior;
* service invariants;
* optimistic concurrency;
* lifecycle transitions;
* Membership linking and removal;
* audit safety and atomicity;
* permission matrix;
* cross-tenant non-disclosure;
* escaped search;
* multi-page keyset traversal;
* Problem Details responses;
* authenticated API workflows;
* rollback through fresh verification Sessions.

## Intentional deferrals

The following are intentionally outside the current domain:

* availability calendars;
* appointment assignment;
* licensing authority verification;
* external credential synchronization;
* payroll;
* provider self-service portals;
* global identity deduplication;
* bulk operations;
* field-level encryption;
* frontend workflows.

These decisions keep the domain focused on reliable tenant ownership,
authorization, lifecycle, concurrency, association integrity, and auditability.
