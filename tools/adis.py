#!/usr/bin/env python3
"""
adis.py -- annotated Z80 disassembler (phase 4 of PORTING-ZX-TO-JS.txt).

  - decodes with z80dis, not a hand-rolled table;
  - substitutes symbolic names into operands by regex over hex addresses, so
    you read "CALL load_block" and not "CALL 0xFF3B";
  - prints the raw hex bytes beside each instruction (needed for
    false-positive checks and for verifying instruction boundaries);
  - prints the region description above each region;
  - keeps a growable dictionary of names in tools/symbols.json.

Usage:
    python tools/adis.py <addr> [count]         disassemble from the 64K image
    python tools/adis.py <addr> [count] --db    also emit DEFB for data regions
    python tools/adis.py --sym NAME=ADDR        add a symbol and exit
"""

import json
import os
import re
import sys

import z80dis.z80 as z

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SYMFILE = os.path.join(HERE, 'symbols.json')
IMAGE = os.path.join(ROOT, 'build', 'image.bin')
REGIONS = os.path.join(HERE, 'regions.json')

# ROM entry points worth naming wherever they appear (3.7).
ROM_SYMS = {
    0x0000: 'ROM_START', 0x0008: 'ROM_ERROR_1', 0x0010: 'ROM_PRINT_A',
    0x0018: 'ROM_GET_CHAR', 0x0020: 'ROM_NEXT_CHAR', 0x0028: 'ROM_CALC',
    0x0038: 'ROM_MASK_INT', 0x0066: 'ROM_NMI',
    0x028E: 'ROM_KEY_SCAN', 0x02BF: 'ROM_KEYBOARD', 0x0333: 'ROM_K_DECODE',
    0x03B5: 'ROM_BEEPER', 0x03F8: 'ROM_BEEP', 0x04C2: 'ROM_SA_BYTES',
    0x0556: 'ROM_LD_BYTES', 0x05E7: 'ROM_LD_EDGE_1', 0x0605: 'ROM_LD_EDGE_2',
    0x0D6B: 'ROM_CLS', 0x0DAF: 'ROM_CL_ALL', 0x0E44: 'ROM_CL_CHAN',
    0x1601: 'ROM_CHAN_OPEN', 0x203C: 'ROM_INT_TO_FP', 0x2D28: 'ROM_STACK_A',
    0x2DA2: 'ROM_FP_TO_BC', 0x3C00: 'ROM_CHARSET_BASE',
}

SYSVARS = {
    0x5C00: 'KSTATE', 0x5C08: 'LAST_K', 0x5C0B: 'MODE', 0x5C36: 'CHARS',
    0x5C3A: 'ERR_NR', 0x5C3B: 'FLAGS', 0x5C3D: 'ERR_SP', 0x5C3F: 'LIST_SP',
    0x5C48: 'BORDCR', 0x5C4B: 'VARS', 0x5C51: 'CHANS', 0x5C53: 'PROG',
    0x5C5D: 'CH_ADD', 0x5C63: 'STKBOT', 0x5C65: 'STKEND', 0x5C6B: 'DF_SZ',
    0x5C78: 'FRAMES', 0x5C7B: 'UDG', 0x5C8D: 'ATTR_P', 0x5C8F: 'ATTR_T',
    0x5C91: 'P_FLAG', 0x5CB2: 'RAMTOP', 0x5CB4: 'P_RAMT',
    0x4000: 'SCREEN', 0x5800: 'ATTRS',
}


def load_syms():
    s = dict(ROM_SYMS)
    s.update(SYSVARS)
    if os.path.exists(SYMFILE):
        for k, v in json.load(open(SYMFILE)).items():
            s[int(k, 0)] = v
    return s


def save_sym(name, addr):
    d = json.load(open(SYMFILE)) if os.path.exists(SYMFILE) else {}
    d[f'0x{addr:04X}'] = name
    json.dump(d, open(SYMFILE, 'w'), indent=1, sort_keys=True)


def load_regions():
    if os.path.exists(REGIONS):
        return json.load(open(REGIONS))
    return []


def region_for(addr, regions):
    for r in regions:
        if r['start'] <= addr <= r['end']:
            return r
    return None


HEXRE = re.compile(r'0x([0-9A-Fa-f]{4})')


def substitute(text, syms):
    def rep(m):
        a = int(m.group(1), 16)
        if a in syms:
            return syms[a]
        return m.group(0)
    return HEXRE.sub(rep, text)


def disasm(mem, start, count, syms=None, regions=None, show_data=True,
           out=sys.stdout):
    syms = syms if syms is not None else load_syms()
    regions = regions if regions is not None else load_regions()
    addr = start
    shown_region = None
    n = 0
    while n < count and addr < 0x10000:
        r = region_for(addr, regions)
        if r is not None and r is not shown_region:
            print(f'\n; ===== {r["start"]:04X}-{r["end"]:04X}  {r.get("desc","")} '
                  f'({r.get("kind","code")}) =====', file=out)
            shown_region = r
        label = syms.get(addr)
        if label:
            print(f'{label}:', file=out)
        if r is not None and r.get('kind') == 'data':
            if not show_data:
                break
            row = mem[addr:addr + 8]
            hx = ' '.join(f'{b:02X}' for b in row)
            tx = ''.join(chr(b) if 32 <= b < 127 else '.' for b in row)
            print(f'{addr:04X}  {hx:<24}  DEFB {",".join("$%02X" % b for b in row)}'
                  f'   ; |{tx}|', file=out)
            addr += 8
            n += 1
            continue
        try:
            d = z.decode(mem[addr:addr + 4], addr)
            text = z.decoded2str(d)
            ln = d.len
        except Exception as e:                      # noqa: BLE001
            text = f'??? ({e})'
            ln = 1
        raw = ' '.join(f'{b:02X}' for b in mem[addr:addr + ln])
        print(f'{addr:04X}  {raw:<12}  {substitute(text, syms)}', file=out)
        addr += ln
        n += 1
    return addr


def main():
    args = sys.argv[1:]
    if args and args[0] == '--sym':
        name, _, a = args[1].partition('=')
        save_sym(name, int(a, 0))
        print(f'{name} = 0x{int(a,0):04X}')
        return
    show_data = True
    img = IMAGE
    if '--image' in args:
        i = args.index('--image'); img = args[i + 1]; del args[i:i + 2]
    if not os.path.exists(img):
        sys.exit(f'no memory image at {img} -- run tools/mkimage.py first')
    mem = bytearray(open(img, 'rb').read())
    if len(mem) < 0x10000:
        mem += bytes(0x10000 - len(mem))
    start = int(args[0], 0)
    count = int(args[1], 0) if len(args) > 1 else 32
    disasm(mem, start, count, show_data=show_data)


if __name__ == '__main__':
    main()
