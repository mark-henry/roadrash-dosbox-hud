# Augmenting Road Rash (Win95) with live telemetry — build log

**Goal:** overlay live visualizations of in-game scalars (track position, turning, slide,
speed, crash state) on the 1996 PC version of *Road Rash*, by reading them out of the
running game's memory.

---

## 1. Where does the game actually run?

Started from the dos.zone web build (https://dos.zone/road-rash/). It runs via **js-dos v8**,
which is **DOSBox-X compiled to WebAssembly**, running inside a **Web Worker**, booting a
**Windows 95** guest, loading the game over **sockdrive** (a streaming remote disk; the
`.jsdos` bundle is a tiny encrypted stub, not the game).

Two blockers for modding the hosted page: js-dos's public `CommandInterface` exposes **no
guest-memory read/write**, and the emulator lives behind a Worker boundary. Conclusion:
**run it locally in native DOSBox-X** (which ships a full debugger) instead of fighting the
browser.

## 2. Local rig

- **Win95 disk:** reused dos.zone's own `system-win95-v2.qcow2` (2 GiB virtual, clean Win95),
  converted to a raw working image `win95-c.img`.
- **Game:** installed from `ROADRASH.ISO` (the PC CD-ROM) inside Win95, with DirectX.
  The FMV intros even play — something the dos.zone build doesn't manage.
- **Emulator:** `/Applications/dosbox-x.app`, config in `dosbox-rr.conf` mirroring dos.zone's
  settings (`pentium_mmx`, 128 MB, SB16 Vibra, `ver=7.1`, the `int13fake*` IDE/FDC flags).

### Gotchas worth a blog callout
- **CD mount:** a guest-OS CD must be mounted to a *drive letter* with `-ide`
  (`imgmount d "ROADRASH.ISO" -t iso -ide 2m`). Using a numbered slot silently fails to give
  Win95 an ATAPI CD-ROM — symptom: "no CD inserted."
- **macOS launch:** launching the binary from a detached/background shell dies with
  *"Unable to obtain graphics context for NSWindow."* Use `open -a` for a normal GUI window —
  **but** the built-in debugger only works when DOSBox-X is started **from a Terminal**.
- **Graphics:** pick **software rendering**, not 3Dfx/Direct3D (DOSBox-X has no 3D accel).
- Keep a **GOLDEN snapshot** of the disk image (APFS `cp -c` clone is instant) before poking.

## 3. Prior art (saved a ton of work)

A community **Cheat Engine table** for `ROADRASH.EXE` (v1.0) gave us the game's internal layout
for free. Key discoveries:
- A pointer at `0x4642D8` → the **active bike's struct**.
- Per-rider health is a **0x40-byte-stride array**; the player is rider[0].
- ImageBase is `0x400000`, so the table's addresses map 1:1 onto our binary (verified by
  matching the cheats' byte signatures), and onto the live process.

## 4. Offline reverse engineering

Disassembled `ROADRASH.EXE` (`objdump`) around the cheat injection points and mapped the
per-frame physics integrator. Rider-struct fields (see `RIDER_STRUCT.md`):

| Offset | Field |
|--------|-------|
| `+0x20`  | track position (integrated by speed each frame) |
| `+0x16C` | forward speed |
| `+0xEC/F0/F4` | velocity vector (lateral component = slide) |
| `+0x2F0` | bike health |
| `+0x2F4` | crash / fall state |
| `+0x328` | rider index (0 = player) |

Still to pin down live: the **turning** field (and confirm slide).

## 5. Live introspection (DOSBox-X heavy debugger)

Launched from Terminal, started a race, opened **Debug → Start DOSBox-X debugger**. Confirmed
we halt *inside* `ROADRASH.EXE` (EIP in the `0x400000+` range, flat ring-3 selectors).

- Read the master pointer: `DV 4642D8` → bike struct at `0x00E96B58`.
- Dumped the struct: `MEMDUMPBIN 017F:00E96B58 400` → `MEMDUMP.BIN` (lands in `~/`).
- **Baseline confirms the anchor:** `+0x328` = 0 (player), `+0x2F0` = 142 (health). Values are
  32-bit fixed-point integers.

Next: **differential dumps** across states (straight / left / right / sliding) to isolate the
turning and slide fields, then build the live overlay.

---

## Sources
- Game (PC CD-ROM): https://archive.org/details/roadrash_202407
- Trainer + No-CD (confirms v1.0): https://gamecopyworld.com/games/pc_road_rash.shtml#Road%20Rash%20v1.0%20+2%20TRAINER
- Cheat Engine table (struct/pointer crib): https://fearlessrevolution.com/viewtopic.php?t=11667
