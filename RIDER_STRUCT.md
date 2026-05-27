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

So all three user targets are found: **position** = +0x1C/+0x30 cluster, **turning** = +0x128
(+ lean forces +0x05C/+0x060), **slide** = +0xF0 lateral velocity. Plus speed (+0xEC) and
crash (+0x2F4) for free.

## How these map into the running game
Win95 loads ROADRASH.EXE at 0x400000, so guest-virtual addresses == `0x400000 + RVA`.
e.g. player health = guest-virtual 0x463CB4; struct-ptr at 0x4642D8.
To read from outside: translate guest-virtual → DOSBox-X linear via page tables (DOSBox-X
debugger does this), or scan the emulated RAM. See [[roadrash-introspection-project]].

## Next steps
1. Disassemble the input/steering routine to find turning + slide fields (offline, no GUI).
2. Get game running in Win95, live-watch candidate offsets while turning/sliding to confirm.
3. Build the overlay reading position(+0x20), speed(+0x16C), turn(?), slide(?), crash(+0x2F4).
