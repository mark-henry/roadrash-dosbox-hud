#!/usr/bin/env python3
"""Verbose MemBase/CR3 finder. Run WHILE ACTIVELY RACING: sudo python3 diag.py
Dumps large regions, then brute-forces calibration over every big region by
recognizing the player struct (rider index 0, sane pointers)."""
import ctypes, struct, sys, subprocess, re

PTR_GLOBAL = 0x4642D8
RAM = 128 * 1024 * 1024

libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
mts = ctypes.c_uint.in_dll(libc, "mach_task_self_")
libc.task_for_pid.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.POINTER(ctypes.c_uint)]
libc.mach_vm_read_overwrite.argtypes = [ctypes.c_uint, ctypes.c_ulonglong, ctypes.c_ulonglong,
                                        ctypes.c_ulonglong, ctypes.POINTER(ctypes.c_ulonglong)]

def sizeM(tok):  # "128.0M" / "2.0G" / "512K" -> MB float
    u = tok[-1]; v = float(tok[:-1])
    return v * 1024 if u == 'G' else v / 1024 if u == 'K' else v

def main():
    pids = subprocess.run(['pgrep', '-f', 'MacOS/dosbox-x'], capture_output=True, text=True).stdout.split()
    pid = int(pids[0]); print(f"pid {pid}")
    t = ctypes.c_uint(0)
    if libc.task_for_pid(mts.value, pid, ctypes.byref(t)) != 0:
        sys.exit("task_for_pid failed (need sudo)")
    task = t.value

    def hread(addr, size):
        buf = (ctypes.c_char * size)(); out = ctypes.c_ulonglong(0)
        kr = libc.mach_vm_read_overwrite(task, addr, size, ctypes.addressof(buf), ctypes.byref(out))
        return bytes(buf[:out.value]) if kr == 0 else None

    # --- dump every region >=20MB with its dirty footprint; rank candidates by dirty ---
    vm = subprocess.run(['vmmap', '-w', str(pid)], capture_output=True, text=True).stdout
    print("\nlarge regions (tag  base  size  dirty):")
    cands = []
    for line in vm.splitlines():
        m = re.search(r'^(\S[\w ]*?\S)\s+([0-9a-f]{6,})-([0-9a-f]{6,})\s+\[([^\]]+)\]', line)
        if not m: continue
        tag, base = m.group(1).strip(), int(m.group(2), 16)
        nums = re.findall(r'[\d.]+[KMG]', m.group(4))
        if not nums: continue
        size = sizeM(nums[0]); dirty = sizeM(nums[-1])
        if size >= 20:
            print(f"  {tag:20} 0x{base:X}  {size:7.1f}M  dirty={dirty:7.1f}M")
            if size >= 100:
                cands.append((dirty, base, tag, size))
    # try highest-dirty first; also force-include classic VM_ALLOCATE base
    cands.sort(reverse=True)
    bases = [b for _, b, _, _ in cands]
    for extra in (0x140000000,):
        if extra not in bases: bases.append(extra)
    print(f"\ncandidate MemBases (dirty-ranked): {[hex(b) for b in bases]}")

    def walk(rd, lin, cr3):
        pde = rd(cr3 + ((lin >> 22) << 2))
        if pde is None or not pde & 1: return None
        if pde & 0x80: return (pde & 0xFFC00000) | (lin & 0x3FFFFF)
        pte = rd((pde & 0xFFFFF000) + (((lin >> 12) & 0x3FF) << 2))
        if pte is None or not pte & 1: return None
        return (pte & 0xFFFFF000) | (lin & 0xFFF)

    for base in bases:
        sys.stdout.write(f"\ntrying MemBase 0x{base:X}: prefetch..."); sys.stdout.flush()
        M = bytearray(RAM)
        ok = 0
        for off in range(0, RAM, 1 << 20):
            c = hread(base + off, 1 << 20)
            if c: M[off:off + len(c)] = c; ok += 1
        print(f" {ok}/{RAM>>20} MB chunks readable; scanning CR3...")
        rd = lambda p: int.from_bytes(M[p:p+4], 'little') if 0 <= p <= len(M) - 4 else None
        ptr_at_known = rd(0x179E2D8)
        print(f"    [phys 0x179E2D8] = 0x{ptr_at_known:X}")
        found = None
        for cr3 in range(0, RAM, 0x1000):
            pp = walk(rd, PTR_GLOBAL, cr3)
            if pp is None: continue
            sl = rd(pp)
            if not sl or not (0x100000 <= sl <= 0x7FFFFFF): continue
            sp = walk(rd, sl, cr3)
            if sp is None: continue
            if rd(sp + 0x328) != 0: continue
            if any(not (0x100000 <= (rd(sp + o) or 0) <= 0x7FFFFFF) for o in (0, 4, 8)): continue
            found = (cr3, sl, sp, pp); break
        if not found:
            print("    no valid player struct under this MemBase.")
            continue
        cr3, sl, sp, pp = found
        print(f"\n*** SUCCESS ***  MemBase=0x{base:X}  CR3=0x{cr3:X}")
        print(f"    0x4642D8 -> phys 0x{pp:X} -> struct vptr 0x{sl:X} -> phys 0x{sp:X}")
        g = lambda o: struct.unpack_from('<i', M, sp + o)[0]
        for nm, o in [('rider', 0x328), ('health', 0x2F0), ('crash', 0x2F4),
                      ('speed', 0xEC), ('slide', 0xF0), ('turn', 0x128)]:
            print(f"    {nm:8} +0x{o:03X} = {g(o)}")
        print(f"\nRun the HUD with:\n    sudo python3 reader.py --membase {hex(base)}")
        return
    print("\nNo MemBase worked. Are you ACTIVELY in a race (bike on track, moving)? If at a menu, the struct may not exist yet.")

if __name__ == '__main__':
    main()
