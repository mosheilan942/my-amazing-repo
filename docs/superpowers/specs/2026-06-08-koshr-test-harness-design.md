# koshr — Two-Ended Test Harness (Design Spec)

> Date: 2026-06-08
> Status: approved, ready for implementation plan
> Predecessors: `2026-05-31-koshr-multi-tenant-control-plane-design.md` (control plane),
> MVP v0 (the verified REST automation-push loop)

## Problem / Goal

We need to prove the **full koshr topology end-to-end, for multiple tenants at
once**, on a developer machine — before renting real hardware or installing a
single Raspberry Pi in a home.

Concretely, stand up **both ends of the system** locally:

- **Home side:** a **Pi-simulator** — a real Home Assistant instance in Docker,
  standing in for the Raspberry Pi that will live in each customer's home.
- **Server side:** a **multi-container system** — one real HA container per
  tenant, simulating many customers packed densely on one rented server (VPS,
  e.g. Hetzner/DigitalOcean — **not** AWS, per cost decision).

The harness must demonstrate the architecture is real and **isolated**, and must
exercise **both data channels** the production system depends on.

## Locked architecture (recap — context for this harness)

Decided across prior sessions and confirmed against Arie's integration plan:

- **Hybrid local + cloud.** Each home runs a **full local HA** on a Raspberry Pi.
  Automations and the Shabbat schedule live and **fire locally** — internet loss
  at home does not stop the house from working (offline-safe; halachically a
  pre-set שעון שבת).
- **One isolated Docker container per tenant** on a central server (HA Core on
  Alpine). Container isolation = the privacy/blast-radius boundary. Dense per-VM
  packing keeps cost predictable.
- **Link mechanism:** [`remote_homeassistant`](https://github.com/custom-components/remote_homeassistant)
  (HACS integration). The **cloud container is the master**; it **initiates** the
  secure WebSocket (`wss://`) connection **down to the Pi (slave)**, mirrors the
  Pi's entities upward (giving the cloud/Claude device-awareness), and forwards
  service calls downward.
- **Control plane** owns the tenant boundary (per-tenant `ha_url` + token); HA is
  never asked to be multi-tenant internally.

### The two-channel model (load-bearing — do not collapse)

| Channel | Mechanism | Used for | Offline at trigger? | Halacha |
|---|---|---|---|---|
| **Config-push** | REST `POST /api/config/automation/config/<id>` to the Pi's HA | recurring **Shabbat schedule** (שעון שבת); any pre-set automation | **Safe** — config lives on Pi, fires with no internet | Clean — pre-set local clock |
| **Live link** | `remote_homeassistant` master→slave service calls | ad-hoc **immediate** actions ("turn light on now"); entity mirroring; manual override | Not safe — needs internet at the moment | On-Shabbat live trigger → needs psak; keep off the Shabbat path |

**Rule the harness enforces by demonstration:** recurring schedules go through
config-push so they survive offline; only ad-hoc/immediate actions use the live
link. Conflating the two reintroduces the internet-dependency failure and the
on-Shabbat halachic surface we already decided to avoid.

## What the harness proves

1. **Multi-tenant provisioning** — N tenant HA containers come up on one host.
2. **Per-tenant link** — each container (master) establishes a live
   `remote_homeassistant` link to its own Pi-sim (slave); the Pi's demo entities
   mirror up into the container.
3. **Config-push local fire** — the koshr control plane pushes an automation to a
   tenant's Pi via REST; the Pi **stores and fires it locally**, toggling a demo
   switch — observed on the Pi, not the container.
4. **Hard isolation (data plane)** — tenant A's path **cannot** reach tenant B's
   container or Pi. This is the data-plane isolation the control-plane spec
   explicitly deferred; the harness is where we start proving it. Asserted at the
   network level (per-tenant Docker networks), not assumed.

## Topology

```
        koshr control-plane  (control_plane.handle / admin_cli)
              │  per-tenant: ha_url + token   ← isolation boundary
              ▼
   ┌──── tenant-A net ────┐        ┌──── tenant-B net ────┐
   │  container-A (master)│        │  container-B (master)│   "server" — dense pack
   │   ├ REST config-push─┐│       │   ├ REST config-push─┐│
   │   └ remote_ha  wss ──┤│       │   └ remote_ha  wss ──┤│
   │            ▼         ││       │            ▼         ││
   │  pi-sim-A (HA, slave)│        │  pi-sim-B (HA, slave)│   "home" Pi — fires local
   │   input_boolean demo │        │   input_boolean demo │
   └──────────────────────┘        └──────────────────────┘
          A net   ⊥   B net     ← first-class isolation assertion
```

## Components to build

| Component | Responsibility |
|---|---|
| **Compose / provisioning script** | Bring up N tenant containers + N pi-sims; one dedicated Docker network per tenant (`tenant-<id>-net`) so A and B share no network. |
| **pi-sim image** | Real `homeassistant/home-assistant` image; pre-seeded `/config` with a demo switch (`input_boolean.demo_switch`), the `remote_homeassistant` slave side enabled, and a long-lived token. |
| **tenant container image** | Real HA image; `remote_homeassistant` master configured to dial its own pi-sim; long-lived token for the control plane's REST pushes. |
| **harness driver** | Orchestration test: register tenants in the control plane → for each, push a Shabbat-style automation via REST → assert it fires on the **correct** Pi → exercise one live-link service call → run the isolation assertions. |
| **isolation assertions** | (a) control-plane routing only ever talks to the addressed tenant's HA (reuse/extend the control-plane contract test); (b) network-level: container-A / pi-A cannot reach container-B / pi-B. |

## Reuse from existing code

- The **REST automation-push loop** is already verified (MVP v0) and lives in
  `ha_client.py` (`post_automation` / `get_automation`). The harness drives it
  per-tenant via the control plane.
- The **control-plane contract test** (`test_handle_routes_only_to_this_tenants_ha`)
  already guards control-plane routing; extend its spirit to the live (Docker)
  data plane.
- Demo automation shape can reuse `DemoBrain`'s rehearsed `input_boolean.demo_switch`
  target so the harness needs no Anthropic key.

## Known limits (state explicitly; do not let the harness over-claim)

- **No real NAT traversal.** All containers run on one host and can reach each
  other for free, so the harness proves the link + REST loop + network isolation,
  **not** that a real home Pi behind a router is reachable. The harness defers
  this — but production does **not** treat it as optional: residential networks
  block inbound traffic, so a **Pi-dials-out tunnel** (WireGuard: Pi joins as a
  peer with a stable VPN IP, the container reaches it back — required because the
  master dials the slave) is a **hard production prerequisite**, not a later
  nice-to-have. Its validation is a separate spec; its necessity is not in doubt.
- **Config-push direction matters — pull may decouple the Shabbat path.** In a
  push model the master must reach inbound to the Pi (needs the tunnel). A **pull
  model** (the Pi periodically fetches its own schedule config) makes the
  **recurring Shabbat path NAT-free and independent of the tunnel/live link** —
  the offline-safe clock keeps working even if the live link is down. The live
  link still needs inbound→Pi for ad-hoc actions, so the tunnel stays for that;
  but decoupling the Shabbat path from it is a real reliability win. To be decided
  in the networking spec, flagged here as a leading option.
- **Isolation is network + control-plane only.** Full prod isolation (per-tenant
  secrets/KMS, tunnel auth, container escape hardening) is out of scope.

## Out of scope (own specs later)

- **Control channels — email + website + app + WhatsApp (all planned).** End
  users without smartphones use email; others use web/app/WhatsApp. An LLM router
  reads inbound messages, identifies the sender against an approved customer list,
  parses free-text schedule requests, updates that tenant's config, and sends a
  confirmation / Friday-morning weekly schedule summary. The harness drives
  `control_plane.handle` directly and does **not** implement any channel.
  **Seam readiness (forward-compat):** `control_plane.handle(tenant_id, command,
  ...)` *is* the boundary the future LLM router plugs into — the router's only job
  is (sender → tenant_id) + (free text → command/automation JSON), then it calls
  this same entry point. The harness must therefore treat that signature as a
  stable contract, not an internal detail, so the channel layer can be added later
  without reshaping the control plane.
- **Real server/VPS deployment, tunnel/WireGuard, NAT.**
- **Secret management / KMS, token rotation.**
- **Operator portal UI.**
- **Hardware: OEM pre-flashed Wi-Fi devices (Tuya/Sonoff), plug-and-play pairing,
  pricing model.**

## Testing strategy

- TDD throughout. The harness is itself a test artifact, but its building blocks
  (provisioning helpers, isolation assertions, push-and-verify) get unit/contract
  tests first.
- **Pin the HA image version** (the config-automation endpoint is functional but
  not a formally stable public API) and pin the `remote_homeassistant` version.
  Pinning is only a stopgap — building the core push on an undocumented internal
  endpoint is real upstream-maintenance risk. The actual guard is a **contract
  test that exercises the live endpoint against the pinned image and fails loudly
  the moment an HA upgrade changes its shape**, so breakage surfaces on a
  deliberate version bump, never silently in production. Known fallback if the
  endpoint is ever removed: write `automations.yaml` + call the
  `automation.reload` service (also semi-internal, but a documented service).
- **Per-tenant network isolation** is asserted as a positive test
  (A→A works) **and** a negative test (A→B refused / unreachable).
- Use `validate_config` (WebSocket) before each REST push, as in MVP v0.

## Decomposition (implementation order, high level)

1. Compose/provisioning for one tenant (container + pi-sim + dedicated network).
2. Bring up the `remote_homeassistant` master→slave link; assert Pi entities
   mirror into the container.
3. REST config-push → assert local fire on the Pi.
4. Generalize to N tenants; add the negative isolation assertions.
5. One live-link service-call demonstration (ad-hoc action path).

(Detailed step-by-step plan follows in `writing-plans`.)
