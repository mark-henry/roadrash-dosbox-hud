#!/usr/bin/env python3
"""
Road Rash live telemetry reader.

Attaches to the running dosbox-x process, finds the emulated guest RAM + the
game's page directory (by recognizing the player struct), follows the bike-struct
pointer at virtual 0x4642D8, and streams the ENTIRE struct (every dword) to a
browser HUD over SSE. Also logs everything to telemetry.csv with a "mark" flag you
toggle from the HUD -- hold it while the tires screech to hunt for the slip field.

Usage (needs sudo; dosbox-x isn't signed get-task-allow). Be in a loaded race:
    sudo python3 reader.py                      # auto-calibrate
    sudo python3 reader.py --membase 0x140000000   # fast path (skip region search)
    sudo python3 reader.py --print --no-log
Then open http://localhost:8723/
"""
import ctypes, struct, sys, subprocess, re, time, json, argparse, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

PTR_GLOBAL = 0x4642D8           # virtual addr of the dword -> active bike struct
STRUCT_LEN = 0x340              # bytes of struct to stream (208 dwords)
NDW = STRUCT_LEN // 4
RAM = 128 * 1024 * 1024

# ---------------------------- mach memory access ----------------------------
libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
mach_task_self_ = ctypes.c_uint.in_dll(libc, "mach_task_self_")
libc.task_for_pid.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.POINTER(ctypes.c_uint)]
libc.mach_vm_read_overwrite.argtypes = [ctypes.c_uint, ctypes.c_ulonglong, ctypes.c_ulonglong,
                                        ctypes.c_ulonglong, ctypes.POINTER(ctypes.c_ulonglong)]

class Mem:
    def __init__(self, pid):
        t = ctypes.c_uint(0)
        if libc.task_for_pid(mach_task_self_.value, pid, ctypes.byref(t)) != 0:
            sys.exit("task_for_pid failed. Run with sudo; if it still fails we re-sign dosbox-x.")
        self.task = t.value
        self.membase = None

    def host_read(self, addr, size):
        buf = (ctypes.c_char * size)(); out = ctypes.c_ulonglong(0)
        kr = libc.mach_vm_read_overwrite(self.task, addr, size, ctypes.addressof(buf), ctypes.byref(out))
        return bytes(buf[:out.value]) if kr == 0 else None

    def phys(self, p, n): return self.host_read(self.membase + p, n)
    def u32(self, p):
        b = self.phys(p, 4)
        return int.from_bytes(b, 'little') if b else None

def walk(rd, lin, cr3):
    pde = rd(cr3 + ((lin >> 22) << 2))
    if pde is None or not pde & 1: return None
    if pde & 0x80: return (pde & 0xFFC00000) | (lin & 0x3FFFFF)
    pte = rd((pde & 0xFFFFF000) + (((lin >> 12) & 0x3FF) << 2))
    if pte is None or not pte & 1: return None
    return (pte & 0xFFFFF000) | (lin & 0xFFF)

# ----------------------- calibration: membase + cr3 ------------------------
def _sizeM(tok):
    u, v = tok[-1], float(tok[:-1])
    return v * 1024 if u == 'G' else v / 1024 if u == 'K' else v

def membase_candidates(pid):
    vm = subprocess.run(['vmmap', '-w', str(pid)], capture_output=True, text=True).stdout
    regs = []
    for line in vm.splitlines():
        m = re.search(r'\b([0-9a-f]{6,})-([0-9a-f]{6,})\s+\[([^\]]+)\]', line)
        if not m: continue
        nums = re.findall(r'[\d.]+[KMG]', m.group(3))
        if not nums or _sizeM(nums[0]) < 100: continue
        regs.append((_sizeM(nums[-1]), int(m.group(1), 16)))    # (dirty, base)
    regs.sort(reverse=True)                                     # most-dirty first = the real RAM
    bases = [b for _, b in regs]
    if 0x140000000 not in bases: bases.append(0x140000000)
    return bases

def calibrate(mem, pid, forced):
    for base in ([forced] if forced else membase_candidates(pid)):
        mem.membase = base
        M = bytearray(RAM)
        for off in range(0, RAM, 1 << 20):
            c = mem.host_read(base + off, 1 << 20)
            if c: M[off:off + len(c)] = c
        rd = lambda p: int.from_bytes(M[p:p+4], 'little') if 0 <= p <= len(M) - 4 else None
        for cr3 in range(0, RAM, 0x1000):
            pp = walk(rd, PTR_GLOBAL, cr3)
            if pp is None: continue
            sl = rd(pp)
            if not sl or not (0x100000 <= sl <= 0x7FFFFFF): continue
            sp = walk(rd, sl, cr3)
            if sp is None or rd(sp + 0x328) != 0: continue
            if any(not (0x100000 <= (rd(sp + o) or 0) <= 0x7FFFFFF) for o in (0, 4, 8)): continue
            c = rd(sp + 0x2F4)
            if c is None or c > 8: continue           # crash state 0..8 (0 is valid: upright!)
            return base, cr3
    return None, None

# ------------------------ shared state + sampler ---------------------------
LATEST = {'error': 'starting'}
MARK = 0
LOGFH = None
FIELDS_PRINT = {'speed': 0xEC, 'latL/R': 0xF0, 'bend': 0x128, 'health': 0x2F0, 'crash': 0x2F4}

def read_struct(mem, cr3):
    pp = walk(mem.u32, PTR_GLOBAL, cr3)
    if pp is None: return None
    sl = mem.u32(pp)
    sp = walk(mem.u32, sl, cr3) if sl else None
    if sp is None: return None
    data = mem.phys(sp, STRUCT_LEN)
    if not data or len(data) < STRUCT_LEN: return None
    return list(struct.unpack_from('<%di' % NDW, data))

def sampler(mem, cr3, hz, do_print):
    global LATEST
    period = 1 / hz
    while True:
        v = read_struct(mem, cr3)
        if v is None:
            LATEST = {'error': 'struct page not resident (are you in a race?)'}
        else:
            LATEST = {'v': v, 'mark': MARK, 'n': NDW}
            if LOGFH:
                LOGFH.write(('%.3f,%d,' % (time.time(), MARK)) + ','.join(map(str, v)) + '\n')
            if do_print:
                g = lambda o: v[o // 4]
                sys.stdout.write('\r' + '  '.join(f'{k} {g(o):+8d}' for k, o in FIELDS_PRINT.items())
                                 + ('  [MARK]' if MARK else '        '))
                sys.stdout.flush()
        time.sleep(period)

# -------------------------------- web server -------------------------------
HUD = Path(__file__).with_name('hud.html')
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, body, ctype='text/plain'):
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        global MARK
        u = urlparse(self.path)
        if u.path in ('/', '/hud.html'):
            self._send(HUD.read_bytes(), 'text/html; charset=utf-8')
        elif u.path == '/mtime':
            self._send(str(HUD.stat().st_mtime).encode())
        elif u.path == '/mark':
            MARK = 1 if parse_qs(u.query).get('v', ['0'])[0] == '1' else 0
            self._send(b'ok')
        elif u.path == '/stream':
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            try:
                while True:
                    self.wfile.write(b'data: ' + json.dumps(LATEST).encode() + b'\n\n')
                    self.wfile.flush()
                    time.sleep(1 / 30)
            except (BrokenPipeError, ConnectionResetError):
                pass
        else:
            self.send_error(404)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pid', type=int, default=None)
    ap.add_argument('--membase', default=None)
    ap.add_argument('--port', type=int, default=8723)
    ap.add_argument('--hz', type=int, default=60)
    ap.add_argument('--print', dest='do_print', action='store_true')
    ap.add_argument('--no-log', dest='log', action='store_false')
    ap.add_argument('--logfile', default='telemetry.csv')
    a = ap.parse_args()

    pid = a.pid or int(subprocess.check_output(['pgrep', '-f', 'MacOS/dosbox-x']).split()[0])
    mem = Mem(pid)
    forced = int(a.membase, 16) if a.membase else None
    print(f"attached to dosbox-x pid {pid}; calibrating... (get on the track and start moving)")
    mb = cr3 = None
    for attempt in range(120):
        mb, cr3 = calibrate(mem, pid, forced)
        if cr3 is not None:
            break
        sys.stdout.write(f"\r  waiting for an active race... (try {attempt + 1}) ")
        sys.stdout.flush()
        time.sleep(1.5)
    if cr3 is None:
        sys.exit("\ncalibration failed: never found the player struct.")
    mem.membase = mb
    print()
    print(f"MemBase=0x{mb:X}  CR3=0x{cr3:X}  ->  http://localhost:{a.port}/")

    global LOGFH
    if a.log:
        LOGFH = open(a.logfile, 'w', buffering=1)
        LOGFH.write('t,mark,' + ','.join('0x%X' % (i * 4) for i in range(NDW)) + '\n')
        print(f"logging every dword to {a.logfile} (toggle the mark flag from the HUD)")

    threading.Thread(target=sampler, args=(mem, cr3, a.hz, a.do_print), daemon=True).start()
    ThreadingHTTPServer(('127.0.0.1', a.port), Handler).serve_forever()

if __name__ == '__main__':
    main()
