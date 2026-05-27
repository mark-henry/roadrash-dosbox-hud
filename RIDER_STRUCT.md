# Road Rash (PC v1.0, ROADRASH.EXE) — rider/bike struct map

Binary: `ROADRASH.EXE`, 541184 bytes, 1996-08-23, PE32 i386, **ImageBase 0x400000**.
Verified to match the FearLess Cheat Engine table (`ROADRASH.CT`, author "Tomson", 2021).
File offset = RVA − 0xC00 for the .text region (e.g. RVA 0x4024 → file 0x3424).

## Anchors
- `0x4642D8` = **pointer to the active player's rider struct** (CT "Bike Health" = `[0x4642D8]+0x2F0`).
- `0x463CB4` = player health (static); rivals follow as a **0x40-byte-stride array**:
  Mike `+63CF4`, Cydney `+63D34`, … each rider +0x40. (Player is effectively rider[0].)
- `0x4B8A18` = Cash, `0x465A34` = Nitro counter.

## Rider struct fields (offset from struct base; base in ebp/esi in physics code)
Confirmed by disassembly of the per-frame integrator at VA 0x403FA0–0x404060:

| Offset | Meaning | Evidence |
|--------|---------|----------|
| `+0x20`  | **Track position / distance** | `[ebp+0x20] += [ebp+0x16C]` each frame |
| `+0x16C` | **Forward speed (scalar)** | added into `+0x20` |
| `+0xEC`  | Velocity vector X (accel target) | accel cheat writes here; `[ebp+0xEC] += [eax+0]` |
| `+0xF0`  | Velocity vector Y | `[ebp+0xF0] += [eax+4]` |
| `+0xF4`  | Velocity vector Z | `[ebp+0xF4] += [eax+8]` |
| `+0x2F0` | Bike health | CT pointer path |
| `+0x2F4` | **Crash / fall state** (1=normal) | "never fall off" forces it; `mov [ebp+0x2F4],1` |
| `+0x2F8` | Input-derived state | `mov [ebp+0x2F8], [esp+0x20]` |
| `+0x328` | Rider index / discriminator (0=player) | `mov eax,[esi+0x328]; test;` in accel + crash checks |
| `+0xBC,+0x288,+0x244,+0x174/178,+0xC0` | crash reset cluster | written in crash branch |

## LIVE-CONFIRMED (differential dumps: straight vs hard-left vs hard-right+slide)
Struct base = dword at 0x4642D8 (was 0x00E96B58 this race). Values are 16.16 fixed-point.

| Offset | Field | Evidence (straight / left / right+slide) |
|--------|-------|------------------------------------------|
| `+0xEC` | **Forward speed** (vel X) | 4780 / 7390 / 10227 — rises, never flips sign (also mirrored at +0x108, +0x2E8) |
| `+0xF0` | **Slide = lateral velocity** (vel Y) | 0 / −1680 / +3960 — sign = slide direction, magnitude = slip (copy at +0x104) |
| `+0xF4` | vertical velocity (vel Z) | 522 / 0 / 1092 |
| `+0x128` | **Turning / steer direction** | 0 / +131072 / −131072 = clean ±2.0 mirror — the tidiest turn signal |
| `+0x05C,+0x068` | lean/turn force | 0 / −10.0 / +30.0 (fixed) — scales with turn, sign-flips |
| `+0x060,+0x06C` | lean/turn force 2 | 0 / −6.0 / +16.0 |
| `+0x288,+0x28C` | lean angle | 0 / negative / positive |
| `+0x1C,+0x30,+0x308,+0x310` | **world position** | all advance ~+54000 with forward motion (don't flip on turn) |
| `+0x2F0` | bike health | 142 (confirmed) |
| `+0x2F4` | crash/fall state | 0 when upright |
| `+0x328` | rider index | 0 = player (our anchor check) |

## Hard-turn force fields (the tire-screech / slip trigger is NOT yet identified)
Logging the full struct during gentle vs hard turns refined these. NOTE: +0x5C/+0x164
engage on HARD TURNING but do NOT correlate with the tire-screech sound — the actual
screech/slip trigger is still unknown (needs a ground-truth-labeled run to find).

| Offset | Field | Evidence |
|--------|-------|----------|
| `+0xF0` | lateral velocity (L/R) | present in ALL cornering, gentle→hard (mislabeled "slide") |
| `+0x128` | road bend (track curvature pushing the rider) | ±50.0 max, scales with speed, 0 at standstill |
| `+0x5C` (copy `+0x68`) | hard-turn force | 0 in normal cornering; engages (stepped, signed, ±50–63) on HARD turns — NOT the screech |
| `+0x164` | hard-turn intensity (0–103) | fires in hard-turn episodes, magnitude scales with severity — NOT the screech |
| `+0x118` | slip angle (signed, ±~43) | active across corners, grows with slide |
| `+0x60`/`+0x6C` | turning state | L/R turn input/state |
| `+0x288`/`+0x28C` | lean (lead / 1-frame-lagged copy) | |

Key distinction: lateral velocity (`+0xF0`) ≠ slip. There are hard-corner frames with high
`+0xF0` where `+0x5C`/`+0x164` stay 0 (cornering without breaking traction). Slip = `+0x5C`
engaged + `+0x164` ramping = the screech.

Targets so far: **position** = +0x1C/+0x30/+0x308/+0x310, **turning** = bend +0x128 + turn
state +0x60, **lateral motion** = +0xF0, **hard-turn force** = +0x5C/+0x164. Plus speed
+0xEC, vertical vel +0xF4, crash +0x2F4.

**Screech / slip — leading candidate `+0x20` (and `+0x34`):** using "turning AND losing
speed" as a data-derived screech proxy (user's clue: screeching bleeds speed, turning
doesn't), `+0x20`/`+0x34` are ~0 ~98% of the time and fire ONLY when decelerating — strongly
in turn+decel, exactly 0 in turn+accel, and also on straight braking. Looks like the
**speed-scrub / slip force** (range 0..~72k). `+0x48` is a torn-read copy of `+0x20`.
To confirm: screech should coincide with `+0x20`/`+0x34` spiking, on both hard braking and
slides. (`+0x164` slip-intensity only ×4.5 by this measure — secondary.)

Copy pairs (differ in <0.3% of frames = torn reads, treat as identical): +0x108≈speed,
+0x68≈+0x5C, +0x6C≈+0x60, +0x48≈+0x20. Genuinely distinct: +0x104 vs +0xF0 (11%),
+0x288 vs +0x28C (lean lead/lag, 66%).

Marked-run findings:
- **Slip is slide-specific, not braking.** During a "dismount-key spam" run the bike braked
  hard (speed 6860→1084) in a straight line, yet +0x20/+0x34/+0x164/+0xF0/+0x5C were all
  ZERO. So those fields engage only on sideways sliding, not on deceleration per se. User's
  ear: screech ↔ high +0x164. So slip/screech = +0x164 intensity (+0x20/+0x34 = the
  speed-scrub it causes), distinct from the dismount/brake decel mechanism.
- **Dismount key is NOT in the rider struct** — no per-press field toggles; only the speed
  drop is visible. Input/key state lives elsewhere (global input buffer). Can't be used as an
  in-struct event marker.
- **Rough terrain / off-road = +0x158** (0 on road, 20–30 on the shoulder). Companions:
  +0x114 (→30 off-road), +0x130 (oscillates ±600 = bumpiness).

## How these map into the running game
Win95 loads ROADRASH.EXE at 0x400000, so guest-virtual addresses == `0x400000 + RVA`.
e.g. player health = guest-virtual 0x463CB4; struct-ptr at 0x4642D8.
To read from outside: translate guest-virtual → DOSBox-X linear via page tables (DOSBox-X
debugger does this), or scan the emulated RAM. See [[roadrash-introspection-project]].

## Next steps
1. Disassemble the input/steering routine to find turning + slide fields (offline, no GUI).
2. Get game running in Win95, live-watch candidate offsets while turning/sliding to confirm.
3. Build the overlay reading position(+0x20), speed(+0x16C), turn(?), slide(?), crash(+0x2F4).
