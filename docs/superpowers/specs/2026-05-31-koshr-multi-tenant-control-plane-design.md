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

## Execution locality — DECIDED: cloud-hosted

The Home Assistant instance (the brain/compute) **runs in the cloud**, not in any
customer's home. The only in-home equipment is **dumb, minimal hardware**: a
Z-Wave radio bridge (Arie's track) that exposes the local Z-Wave network to the
cloud HA over the internet. The HA instance, koshr, accounts, and billing all live
in the cloud.

**Accepted risk — internet dependency at trigger time.** Because execution is
cloud-hosted, an automation fires only if the home's internet is up at the trigger
moment (cloud → home bridge). This is **distinct from a power outage** (no power =
no device anyway, so power loss is irrelevant). The live risk is: power up, bridge
up, but **home internet down or filtered** at fire-time → the automation misses
its window. In filtered-internet Haredi homes this is plausible. **Accepted
consciously** for now. The strongest mitigation is the local fallback below.

**Halachic flag (open, requires rabbinic review).** The plan includes the cloud
**re-pushing commands during Shabbat** when power/internet returns mid-Shabbat.
That is a *live, on-Shabbat trigger from a cloud service*, which is not the same as
a *pre-set, locally-run* שעון שבת. CLAUDE.md is explicit that live on-Shabbat
triggers open halachic surface beyond the clean pre-Shabbat-scheduling wedge. This
needs a psak before we rely on it. Recorded here; not decided.

**Highest-leverage open option — local fallback on the bridge.** A thin local
executor on the in-home bridge that fires the **already-loaded** schedule even when
the internet is down resolves *both* risks above at once: it restores fire-time
reliability **and** removes the halachic problem (a pre-loaded schedule firing
locally behaves like a שעון שבת — no live cloud trigger on Shabbat, so no re-push).
Cloud re-push *worsens* the halachic side; the local fallback *removes* it. This
shifts the design toward "cloud brain + thin local executor," which is a partial
return to the original golden-rule architecture. It is **flagged as an open
decision**, not adopted here, and it interacts with the locality decision — so it
should be resolved (alongside the psak) **before** committing to
`ContainerProvisioner`. It does **not** block the control-plane core, which is
indifferent to where the executor runs.

**Design consequence:** the control-plane **core** addresses an HA by URL + token
and enforces isolation. This is **not** a claim that locality stays open — it is a
deliberate **seam**: keeping the core's only coupling to the executor a `(url,
token)` pair makes it simple to test and lets `ManualProvisioner` register a
hand-started cloud container today, and would also accommodate the local-fallback
option above without core rework. The cloud-hosted commitment means container
orchestration and `/config` persistent volumes are the **committed direction**
(phased into build order, see Scope), not an open fork. Hibernation remains
rejected (see Rejected C).

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
moment the automation **must** fire. **Deferred**, not adopted — chiefly to avoid
the wake-controller engineering and boot-latency-misses-window failure mode, and to
keep v1 simple. Honest caveat: the original "reliability beats cost" framing is
**partly self-undercutting**, since we already accept internet-dependency-at-
trigger-time (Execution locality) — that is the same failure class, so hibernation's
*marginal* reliability cost is smaller than first stated. The deciding factors are
therefore simplicity + the cost strategy below, not a clean reliability win.
Hibernation stays revisitable if dense packing proves insufficient.

## The isolation invariant (the heart of the design)

> Every operation that touches an HA is parameterized by **exactly one tenant's
> credentials, resolved fresh per request**. There is no global/singleton HA
> client. No code path takes data from tenant A and reaches tenant B's HA. A
> contract test enforces this.

Every component below exists to uphold this one sentence.

**Scope of the guarantee — control plane only (read this).** This invariant and
its contract test cover the **control plane**: they prove koshr's *code* cannot
construct or route through a cross-tenant `HAClient`. They say **nothing** about
the **data plane** — whether deployed container A can reach container B's HA API,
or whether one tenant's bridge↔cloud tunnel can be hit by another. Network-level
isolation (per-tenant network policy, per-tenant tunnel auth) lives in the deferred
`ContainerProvisioner` work. **Therefore the product-level "hard isolation"
requirement is NOT fully met by this spec** — it is met for the control plane and
remains open for the data plane until the network-isolation work ships. A green
contract test must not be read as "isolation: done."

## Architecture — the control-plane core (a thin seam over the executor)

| File | Status | Role |
|---|---|---|
| `tenant.py` | new | `Tenant` dataclass (`tenant_id`, `name`, `ha_url`, `status`, `contact`). No deployment fields — provisioner-specific data (e.g. cloud `volume_ref`) lives in the provisioner's own metadata, not the core model. `TenantStore` interface (`get` / `put` / `list`). v1 impl = encrypted local store: HA token encrypted at rest (Fernet, master key from `KOSHR_MASTER_KEY`), decrypted only in memory at point of use. Interface allows later swap to AWS Secrets Manager / KMS. |
| `provisioner.py` | new | `Provisioner` interface (`provision` / `suspend` / `destroy`). v1 impl = `ManualProvisioner`: register any already-running HA by URL + token (a hand-started cloud container today; the local-fallback executor later, with no core change). `ContainerProvisioner` (ECS/Fargate, volume attach, per-tenant network policy) is the **committed, deferred** impl behind the same interface — it also owns data-plane network isolation. |
| `control_plane.py` | new | `handle(tenant_id, command, opts) -> result`. The v0 koshr loop lifted to be tenant-parameterized. Resolves tenant, builds a scoped `HAClient`, runs brain → draft → confirm → post, records cost against the tenant. |
| `ha_client.py` | refactor | `HAClient(base_url, token)` — pure constructor args, **no env reads**. One client instance = one tenant. |
| `ledger.py` | extend | add `tenant_id` to each record; `summarize(tenant_id=None)` filters by tenant → per-customer billing basis. |
| `koshr.py` | refactor | CLI gains `--tenant <id>`; resolves via `TenantStore`, calls `control_plane.handle`. Back-compat: env-only single tenant still runs (wrapped as a default tenant). |
| `admin_cli.py` | new | Operator commands over the control plane: `list` / `provision` / `suspend` / `summary` (per-tenant cost). Stand-in for the future portal. |

`brain.py` and `cost.py` are unchanged.

## Data flow

```
command + tenant_id
  → TenantStore.get(tenant_id)          # {ha_url, ha_token (decrypted), status}
  → guard: tenant active?               # else refuse — never fall back to another tenant
  → HAClient(ha_url, ha_token)          # SCOPED to this tenant only
  → sensors = ha.jewish_calendar_sensors()
  → draft = brain.select(sensors).draft(command)
  → confirm → ha.post_automation(...)
  → ledger.record(cost, command, brain, tenant_id)   # billed to this tenant
```

## Persistence (committed — cloud-hosted)

HA keeps all state in one directory — `/config`: recorder DB
(`home-assistant_v2.db`), `.storage/` (entity/device/auth registries), and
`automations.yaml` (where POSTed automations land). For the cloud-hosted variant:

> **Container = cattle; the `/config` volume = pet.** Each tenant gets a
> persistent volume mounted at `/config`. Restart / upgrade / crash → **re-attach
> the same volume by `tenant_id`**, never recreate. Backups = volume snapshots.

`ContainerProvisioner` (deferred) owns volume attach/reattach and the volume
reference. This is **provisioner-specific metadata**, kept out of the core
`Tenant` model — the control-plane core never reads it.

## Security

- **Isolation invariant** (above), enforced by a contract test: a mock store with
  two tenants; assert `handle(A, …)` can only ever construct an `HAClient` with
  A's URL — never B's.
- **Secrets at rest:** HA tokens encrypted (Fernet; `KOSHR_MASTER_KEY` from env),
  decrypted only in memory at use, **never logged**. `HAClient` never prints its
  token. **Honest limit:** Fernet-at-rest mainly defends *disk-theft-without-the-
  process* — in practice whoever obtains the disk usually also has the env holding
  `KOSHR_MASTER_KEY`, so this buys less than it appears until the key sits in KMS.
  A single shared master key is also a single point of compromise. Fine for v1; do
  not overstate it.
- **Token scope risk (recorded):** HA long-lived tokens are **not scoped** — our
  per-tenant operational token is effectively admin on that HA, so a compromised
  token = full control of *that* tenant's environment (isolated to one tenant, but
  total within it). Accepted for now; as we move toward real compliance, add a
  **token rotation policy** and adopt finer-grained access if HA ever supports it.
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

**Known gap — suspend ≠ stop execution.** Control-plane `suspend` only refuses
*new* commands. The tenant's running container keeps firing **already-installed**
automations indefinitely until `Provisioner.suspend` (deferred) can actually stop
or quarantine the container. So a non-paying tenant's existing automations continue
to run after suspension. Named here; closing it depends on `ContainerProvisioner`.

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

### Build now (control-plane core)
`TenantStore` (encrypted local) · pure `HAClient(base_url, token)` ·
`control_plane.handle` · ledger `tenant_id` + per-tenant summary · CLI `--tenant`
+ back-compat · `ManualProvisioner` · `admin_cli` (list/provision/suspend/summary)
· isolation contract test.

### Defer (committed cloud-hosted direction, later build phases)
- `ContainerProvisioner` (ECS/Fargate orchestration) — committed, next phase.
- **Data-plane network isolation** (per-tenant network policy, bridge↔cloud tunnel
  auth) — required to actually meet "hard isolation"; ships with
  `ContainerProvisioner`.
- `/config` persistent volumes + backups — committed, next phase.
- Hibernation + Lambda/EventBridge wake — rejected for now (see C).
- **Operator portal UI** — its own next spec; sits on top of `admin_cli`
  operations.
- WhatsApp / SMS channel → tenant mapping — its own later spec.
- KMS / AWS Secrets Manager `TenantStore` impl.
- Break-glass admin mechanism — with the portal.

### Open decisions to resolve before `ContainerProvisioner`
- **Psak** on cloud on-Shabbat re-push (Execution locality).
- **Local-fallback-on-the-bridge** (Execution locality) — resolves the reliability
  + halachic risks together and may adjust the locality decision. The core does not
  depend on the outcome; `ContainerProvisioner` does.

### Pricing note + cost strategy (informs business model, not built here)
The cloud-hosted decision **inverts the unit of economics**. v0's ledger measures a
*variable, per-automation* Anthropic API cost (~fractions of a cent/request). An
always-on container per tenant is a *fixed, per-tenant-month* cost that is paid
**whether or not** the tenant issues any command — and that **idle container is now
the dominant cost line** (RAM + persistent volume + bandwidth), not the API.

**Explicit cost strategy:** since hibernation is rejected (C), the lever on idle
cost is **dense per-VM packing** — many always-on containers per host, amortizing
VM/OS overhead and over-committing RAM that HA mostly leaves idle. This must be
**measured** (real RAM/tenant) to know how many tenants fit per VM, which sets the
true per-tenant cost floor. The triangle — *"reliability beats cost" + "infra is
dominant" + "hibernation rejected"* — is squared by dense packing, not left
implicit. Billing must fold this infra cost in; the current ledger captures only
the API cost, so the cost basis is **incomplete** until infra is accounted for.

## Decomposition (this spec is one piece)

1. **This spec:** control-plane core + `admin_cli` (now).
2. **Next spec:** operator portal UI over the core operations.
3. **Later spec:** WhatsApp/SMS channel → tenant mapping.
4. **Separate track (Arie):** in-home Z-Wave radio bridge — the only in-home
   hardware. Cloud-hosted execution is already decided here.
