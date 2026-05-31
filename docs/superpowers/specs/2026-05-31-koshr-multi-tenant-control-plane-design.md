# koshr Multi-Tenant Control Plane — Design

- **Date:** 2026-05-31
- **Status:** Approved (brainstorming) — ready for implementation plan
- **Supersedes scope of:** single-tenant koshr v0 (`koshr.py` CLI, one `.env`, one HA)

## Problem

koshr v0 is single-tenant: one `.env` (one `HA_URL` + `HA_TOKEN`), one ledger, one
Home Assistant. To make a product we need **many customers**, each controlling
**only their own devices**, with **hard isolation** — no customer can see,
manipulate, or reach another customer's devices. We also need per-customer
billing and a way for koshr staff to manage customers.

## Foundational assumption / open fork — execution locality (UNDECIDED)

Where the Home Assistant instance physically runs is **not decided** and is owned
by a separate track (Arie's hardware work: Z-Wave, minimal in-home gear, network
isolation). Two futures exist:

- **Home-hosted (the original golden rule):** HA runs on a small box in the
  customer's home. Automations fire **locally, offline**, surviving loss of
  internet. This is the load-bearing promise of the whole product (Shabbat-safe).
- **Cloud-hosted variant:** HA runs as a container koshr hosts. Convenient to
  provision, but every automation then depends on the **home internet being up at
  trigger time** (cloud → home bridge for local Z-Wave devices). **This trades
  away the offline/Shabbat-safety guarantee** — the core property of the product.
  That cost must be a conscious choice, not a default.

**Design consequence:** the control-plane **core** in this spec is deliberately
**locality-agnostic**. It addresses an HA by URL + token and enforces isolation —
that works whether the HA sits in a cloud container or a home box. Items that only
make sense for the cloud-hosted variant (container orchestration, `/config`
persistent volumes, hibernation) are **explicitly marked as cloud-variant-specific
and deferred**, not baked into the core. We do not re-litigate locality here; we
build the part that survives either answer.

## Rejected alternatives (on the record)

### A. Build multi-tenancy / per-account isolation into Home Assistant
HA is single-tenant to the core: state machine, event bus, service registry,
entity/device/area registries, recorder DB, frontend, and **every integration**
are global within one instance. The permission framework that exists is partial
and unenforced (regressed in 2025.12; true per-user ACL is a long-open, unshipped
request). Adding real account isolation means threading an owner context through
the entire codebase **and every integration**, then:

1. **Build:** many engineer-months to a fragile v1.
2. **Fork tax (forever):** HA ships monthly; this is a permanent hard fork with
   merge conflicts in the exact internals we rewrote.
3. **Security ownership (forever):** we'd guarantee zero cross-tenant leak across
   hundreds of integrations we didn't write. One leak = customer A controls
   customer B's devices.

A **container per tenant** gives the *same or stronger* isolation (the container
is the boundary HA was designed around) for ~days of work, upgraded by bumping an
image tag — no fork, no security ownership. **Rejected:** building accounts into
HA costs orders of magnitude more and yields a weaker guarantee.

### B. One shared HA instance, logical tenancy above it
Same root cause as (A): HA cannot enforce backend isolation between tenants. One
bug → cross-tenant control. **Rejected** for the hard-isolation requirement.

### C. Hibernate executors + wake on schedule (Lambda/EventBridge)
Mechanically real, but adds failure surface (timer fire, wake, boot) at the exact
moment the automation **must** fire, for a marginal RAM saving. Reliability beats
cost for a Shabbat scheduler. **Deferred**, not adopted. (Note: if execution is
home-hosted, this question disappears entirely.)

## The isolation invariant (the heart of the design)

> Every operation that touches an HA is parameterized by **exactly one tenant's
> credentials, resolved fresh per request**. There is no global/singleton HA
> client. No code path takes data from tenant A and reaches tenant B's HA. A
> contract test enforces this.

Every component below exists to uphold this one sentence.

## Architecture — locality-agnostic core

| File | Status | Role |
|---|---|---|
| `tenant.py` | new | `Tenant` dataclass (`tenant_id`, `name`, `ha_url`, `status`, `contact`, `volume_ref`). `TenantStore` interface (`get` / `put` / `list`). v1 impl = encrypted local store: HA token encrypted at rest (Fernet, master key from `KOSHR_MASTER_KEY`), decrypted only in memory at point of use. Interface allows later swap to AWS Secrets Manager / KMS. |
| `provisioner.py` | new | `Provisioner` interface (`provision` / `suspend` / `destroy`). v1 impl = `ManualProvisioner`: register an already-running HA by URL + token (works for a home box **or** a hand-started cloud container — locality-agnostic). `ContainerProvisioner` (ECS/Fargate, volume attach) is a **cloud-variant, deferred** impl behind the same interface. |
| `control_plane.py` | new | `handle(tenant_id, command, opts) -> result`. The v0 koshr loop lifted to be tenant-parameterized. Resolves tenant, builds a scoped `HAClient`, runs brain → draft → confirm → post, records cost against the tenant. |
| `ha_client.py` | refactor | `HAClient(base_url, token)` — pure constructor args, **no env reads**. One client instance = one tenant. |
| `ledger.py` | extend | add `tenant_id` to each record; `summarize(tenant_id=None)` filters by tenant → per-customer billing basis. |
| `koshr.py` | refactor | CLI gains `--tenant <id>`; resolves via `TenantStore`, calls `control_plane.handle`. Back-compat: env-only single tenant still runs (wrapped as a default tenant). |
| `admin_cli.py` | new | Operator commands over the control plane: `list` / `provision` / `suspend` / `summary` (per-tenant cost). Stand-in for the future portal. |

`brain.py` and `cost.py` are unchanged.

## Data flow

```
command + tenant_id
  → TenantStore.get(tenant_id)          # {ha_url, ha_token (decrypted), status, volume_ref}
  → guard: tenant active?               # else refuse — never fall back to another tenant
  → HAClient(ha_url, ha_token)          # SCOPED to this tenant only
  → sensors = ha.jewish_calendar_sensors()
  → draft = brain.select(sensors).draft(command)
  → confirm → ha.post_automation(...)
  → ledger.record(cost, command, brain, tenant_id)   # billed to this tenant
```

## Persistence (CLOUD-VARIANT-SPECIFIC — deferred)

> Applies **only** if execution is cloud-hosted. If HA is home-hosted, the home
> box owns its own `/config` and this section is moot.

HA keeps all state in one directory — `/config`: recorder DB
(`home-assistant_v2.db`), `.storage/` (entity/device/auth registries), and
`automations.yaml` (where POSTed automations land). For the cloud-hosted variant:

> **Container = cattle; the `/config` volume = pet.** Each tenant gets a
> persistent volume mounted at `/config`, referenced by `Tenant.volume_ref`.
> Restart / upgrade / crash → **re-attach the same volume by `tenant_id`**, never
> recreate. Backups = volume snapshots.

`ContainerProvisioner` (deferred) owns volume attach/reattach. The `volume_ref`
field exists in the core `Tenant` model now so the data model is ready, but no
core logic depends on it.

## Security

- **Isolation invariant** (above), enforced by a contract test: a mock store with
  two tenants; assert `handle(A, …)` can only ever construct an `HAClient` with
  A's URL — never B's.
- **Secrets at rest:** HA tokens encrypted (Fernet; `KOSHR_MASTER_KEY` from env),
  decrypted only in memory at use, **never logged**. `HAClient` never prints its
  token.
- **Token scope risk (recorded):** HA long-lived tokens are **not scoped** — our
  per-tenant operational token is effectively admin on that HA. Accepted for now;
  noted as a risk to revisit (HA offers no finer grant today).
- **Interactive admin into a customer's HA** (support/debug): **break-glass only**
  — consent-based, time-boxed, audited (who / when / why). **Not** standing
  silent access (privacy-sensitive market). Mechanism deferred to the portal spec.
- **Client HA-UI access:** **none** in v1. koshr mediates everything (WhatsApp /
  CLI). A read-only client view may come much later.
- **Tenant resolution is an explicit parameter** — no ambient default that could
  leak across tenants.

## Error handling

- Unknown tenant → clear error; no fallback.
- Suspended / unpaid tenant → refuse with message.
- Secret decrypt failure → refuse (never run against wrong creds).
- HA unreachable → existing per-tenant `HTTPError` path (status + body).
- Cost is still recorded on a `draft()` failure path (API tokens may have been
  spent) — preserved from v0.

## Testing (TDD)

- `TenantStore`: CRUD + encryption round-trip + missing/invalid `KOSHR_MASTER_KEY`
  behavior.
- **Isolation contract test:** two tenants, assert no cross-tenant `HAClient`
  construction and no cross-tenant ledger write.
- `ledger`: tenant-scoped records + per-tenant `summarize`.
- `control_plane.handle`: happy path with mocked store / HA / brain; unknown and
  suspended tenant paths.
- `admin_cli`: list / provision / suspend / summary over a mocked store.
- **Back-compat:** env-only single tenant still works end-to-end.

## Scope

### Build now (locality-agnostic core)
`TenantStore` (encrypted local) · pure `HAClient(base_url, token)` ·
`control_plane.handle` · ledger `tenant_id` + per-tenant summary · CLI `--tenant`
+ back-compat · `ManualProvisioner` · `admin_cli` (list/provision/suspend/summary)
· isolation contract test.

### Defer
- `ContainerProvisioner` (ECS/Fargate orchestration) — **cloud-variant**.
- `/config` persistent volumes + backups — **cloud-variant**.
- Hibernation + Lambda/EventBridge wake — rejected for now (see C).
- **Operator portal UI** — its own next spec; sits on top of `admin_cli`
  operations.
- WhatsApp / SMS channel → tenant mapping — its own later spec.
- KMS / AWS Secrets Manager `TenantStore` impl.
- Break-glass admin mechanism — with the portal.

### Pricing note (informs business model, not built here)
Per-customer cost basis depends on the **locality fork**. Home-hosted ≈ near-zero
infra cost to koshr (customer hardware). Cloud-hosted makes **infra** (container
RAM + persistent volume) the dominant line item, on top of the Anthropic API cost
the ledger tracks today. Whichever way locality resolves, billing must fold infra
cost in — the current ledger captures only the API cost.

## Decomposition (this spec is one piece)

1. **This spec:** control-plane core + `admin_cli` (now).
2. **Next spec:** operator portal UI over the core operations.
3. **Later spec:** WhatsApp/SMS channel → tenant mapping.
4. **Separate track (Arie):** in-home hardware + execution locality decision,
   which retro-selects the deferred cloud-variant items above.
