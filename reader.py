#!/usr/bin/env python3
"""
Road Rash live telemetry reader.

Attaches to the running dosbox-x process, auto-detects the emulated guest RAM and
the game's page directory (by recognizing the player's bike struct), then follows
the struct pointer at virtual 0x4642D8 and streams its fields to a browser HUD.

Usage (needs sudo; dosbox-x isn't signed get-task-allow). Be in a loaded race:
    sudo python3 reader.py                 # auto-calibrate, serve HUD on :8723
    sudo python3 reader.py --print         # also print to terminal
    sudo python3 reader.py --calib-phys 0179E2D8   # optional: pin via debugger PHY
Then open http://localhost:8723/
"""
import ctypes, struct, sys, subprocess, re, time, json, argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PTR_GLOBAL = 0x4642D8          # virtual addr of the dword -> active bike struct
GUEST_RAM_SIZE = 128 * 1024 * 1024

# Every candidate field, read as signed int32. "Put everything up, then pare down."
FIELDS = {
    # --- motion ---
    'speed_fwd':   0xEC,    'vel_lat':     0xF0,    'vel_vert':    0xF4,
    'speed2':      0x108,   'vel_lat2':    0x104,
    # --- steering / lean ---
    'turn_steer':  0x128,
    'turnforce_5C':0x5C,    'turnforce_60':0x60,    'turnforce_68':0x68,   'turnforce_6C':0x6C,
    'lean_288':    0x288,   'lean_28C':    0x28C,
    # --- position (world coords; advance with forward motion) ---
    'pos_1C':      0x1C,    'pos_30':      0x30,    'pos_308':     0x308,  'pos_310':     0x310,
    # --- status ---
    'health':      0x2F0,   'crash':       0x2F4,   'rider':       0x328,
}

# ---------------------------- mach memory access ----------------------------
libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
mach_task_self_ = ctypes.c_uint.in_dll(libc, "mach_task_self_")
libc.task_for_pid.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.POINTER(ctypes.c_uint)]
libc.task_for_pid.restype = ctypes.c_int
libc.mach_vm_read_overwrite.argtypes = [ctypes.c_uint, ctypes.c_ulonglong, ctypes.c_ulonglong,
                                         ctypes.c_ulonglong, ctypes.POINTER(ctypes.c_ulonglong)]
libc.mach_vm_read_overwrite.restype = ctypes.c_int

class Mem:
    def __init__(self, pid):
        t = ctypes.c_uint(0)
        kr = libc.task_for_pid(mach_task_self_.value, pid, ctypes.byref(t))
        if kr != 0:
            sys.exit(f"task_for_pid failed (kr={kr}). Run with sudo; if it still fails we re-sign dosbox-x.")
        self.task = t.value
        self.membase = None

    def host_read(self, host_addr, size):
        buf = (ctypes.c_char * size)()
        out = ctypes.c_ulonglong(0)
        kr = libc.mach_vm_read_overwrite(self.task, host_addr, size,
                                         ctypes.addressof(buf), ctypes.byref(out))
        return bytes(buf[:out.value]) if kr == 0 else None

    def phys(self, p, n): return self.host_read(self.membase + p, n)
    def u32(self, p):
        b = self.phys(p, 4)
        return int.from_bytes(b, 'little') if b else None

# --------------------- page-table walk (32-bit paging) ---------------------
def _walk(rd, lin, cr3):
    pde = rd(cr3 + ((lin >> 22) << 2))
    if pde is None or not (pde & 1): return None
    if pde & 0x80: return (pde & 0xFFC00000) | (lin & 0x3FFFFF)   # 4MB page
    pte = rd((pde & 0xFFFFF000) + (((lin >> 12) & 0x3FF) << 2))
    if pte is None or not (pte & 1): return None
    return (pte & 0xFFFFF000) | (lin & 0xFFF)

# ----------------------- calibration: membase + cr3 ------------------------
def membase_candidates(pid):
    out = subprocess.run(['vmmap', str(pid)], capture_output=True, text=True).stdout
    va, other = [], []
    for line in out.splitlines():
        m = re.search(r'\b([0-9a-f]{6,})-([0-9a-f]{6,})\s+\[\s*([\d.]+)M', line)
        if not m: continue
        if 120 <= float(m.group(3)) <= 200:
            (va if 'VM_ALLOCATE' in line else other).append(int(m.group(1), 16))
    return va + other or [0x140000000]

def prefetch(mem, base):
    M = bytearray(GUEST_RAM_SIZE)
    step = 2 * 1024 * 1024
    for off in range(0, GUEST_RAM_SIZE, step):
        chunk = mem.host_read(base + off, step)
        if chunk: M[off:off + len(chunk)] = chunk
    return M

def calibrate(mem, pid, forced_membase, calib_phys):
    cands = [forced_membase] if forced_membase else membase_candidates(pid)
    for mb in cands:
        M = prefetch(mem, mb)
        def rd(p): return int.from_bytes(M[p:p+4], 'little') if 0 <= p <= len(M)-4 else None
        for cr3 in range(0, GUEST_RAM_SIZE, 0x1000):
            pp = _walk(rd, PTR_GLOBAL, cr3)
            if pp is None: continue
            if calib_phys is not None and pp != calib_phys: continue
            sl = rd(pp)
            if not sl or not (0x100000 <= sl <= 0x7FFFFFF): continue
            sp = _walk(rd, sl, cr3)
            if sp is None: continue
            # validate: player struct -> rider index 0, sane leading pointers, plausible state
            if rd(sp + 0x328) != 0: continue
            if any(not (0x100000 <= (rd(sp + o) or 0) <= 0x7FFFFFF) for o in (0, 4, 8)): continue
            crash = rd(sp + 0x2F4)
            if crash is None or crash > 8: continue
            return mb, cr3
    return None, None

# -------------------------------- sampling ---------------------------------
def make_sampler(mem, cr3):
    rd = mem.u32
    def sample():
        pp = _walk(rd, PTR_GLOBAL, cr3)
        if pp is None: return {'error': 'pointer page not resident'}
        sl = rd(pp)
        sp = _walk(rd, sl, cr3) if sl else None
        if sp is None: return {'error': 'struct page not resident', 'struct_lin': sl}
        data = mem.phys(sp, 0x400)
        if not data or len(data) < 0x340: return {'error': 'struct read failed'}
        out = {name: struct.unpack_from('<i', data, off)[0] for name, off in FIELDS.items()}
        out['struct_lin'] = sl
        return out
    return sample

# -------------------------------- web server -------------------------------
HUD = Path(__file__).with_name('hud.html')
def make_handler(sample, do_print):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            if self.path in ('/', '/hud.html'):
                body = HUD.read_bytes()
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers(); self.wfile.write(body)
            elif self.path == '/mtime':                      # live-reload signal
                body = str(HUD.stat().st_mtime).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'text/plain')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers(); self.wfile.write(body)
            elif self.path == '/stream':
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Cache-Control', 'no-cache')
                self.end_headers()
                try:
                    while True:
                        v = sample()
                        self.wfile.write(b'data: ' + json.dumps(v).encode() + b'\n\n')
                        self.wfile.flush()
                        if do_print and 'error' not in v:
                            sys.stdout.write(f"\rspeed {v['speed_fwd']:7d}  slide {v['vel_lat']:+7d}"
                                             f"  turn {v['turn_steer']:+7d}  hp {v['health']:4d}"
                                             f"  crash {v['crash']}   ")
                            sys.stdout.flush()
                        time.sleep(1/30)
                except (BrokenPipeError, ConnectionResetError):
                    pass
            else:
                self.send_error(404)
    return H

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pid', type=int, default=None)
    ap.add_argument('--membase', default=None)
    ap.add_argument('--calib-phys', default=None, help='optional hex PHY for 0x4642D8')
    ap.add_argument('--port', type=int, default=8723)
    ap.add_argument('--print', dest='do_print', action='store_true')
    a = ap.parse_args()

    pid = a.pid or int(subprocess.check_output(['pgrep', '-n', '-f', 'dosbox-x']).split()[0])
    mem = Mem(pid)
    print(f"attached to dosbox-x pid {pid}; auto-calibrating (be in a loaded race)...")
    mb, cr3 = calibrate(mem, pid,
                        int(a.membase, 16) if a.membase else None,
                        int(a.calib_phys, 16) if a.calib_phys else None)
    if cr3 is None:
        sys.exit("calibration failed: couldn't find the player struct. Make sure a race is running, then retry.")
    mem.membase = mb
    print(f"MemBase=0x{mb:X}  CR3=0x{cr3:X}  ->  http://localhost:{a.port}/")
    ThreadingHTTPServer(('127.0.0.1', a.port), make_handler(make_sampler(mem, cr3), a.do_print)).serve_forever()

if __name__ == '__main__':
    main()
