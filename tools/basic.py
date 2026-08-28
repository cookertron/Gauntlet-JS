#!/usr/bin/env python3
"""
basic.py -- ZX Spectrum BASIC detokeniser.

Phase 1 of the porting pipeline: for a substantially-BASIC loader the
detokenised listing IS the disassembly, and it is what tells you where the
machine-code loader starts (RANDOMIZE USR nnnnn / LOAD ""CODE addresses).

Line format in a saved BASIC program:
    [line number : 2 bytes BIG-endian][line length : 2 bytes little-endian]
    [tokenised text ... 0x0D]

Numbers in the text carry a 5-byte binary form introduced by 0x0E after the
ASCII digits; those five bytes are skipped for display but decoded here so we
can report the exact value the interpreter will use (that is the one the ROM
actually acts on -- the digits are only what is printed).

Usage:  python tools/basic.py <file.bin> [--offset N] [--len N]
"""

import sys

TOKENS = {
    0xA3: 'SPECTRUM ', 0xA4: 'PLAY ',
    0xA5: 'RND', 0xA6: 'INKEY$', 0xA7: 'PI', 0xA8: 'FN ', 0xA9: 'POINT ',
    0xAA: 'SCREEN$ ', 0xAB: 'ATTR ', 0xAC: 'AT ', 0xAD: 'TAB ', 0xAE: 'VAL$ ',
    0xAF: 'CODE ', 0xB0: 'VAL ', 0xB1: 'LEN ', 0xB2: 'SIN ', 0xB3: 'COS ',
    0xB4: 'TAN ', 0xB5: 'ASN ', 0xB6: 'ACS ', 0xB7: 'ATN ', 0xB8: 'LN ',
    0xB9: 'EXP ', 0xBA: 'INT ', 0xBB: 'SQR ', 0xBC: 'SGN ', 0xBD: 'ABS ',
    0xBE: 'PEEK ', 0xBF: 'IN ', 0xC0: 'USR ', 0xC1: 'STR$ ', 0xC2: 'CHR$ ',
    0xC3: 'NOT ', 0xC4: 'BIN ', 0xC5: 'OR ', 0xC6: 'AND ', 0xC7: '<=',
    0xC8: '>=', 0xC9: '<>', 0xCA: 'LINE ', 0xCB: 'THEN ', 0xCC: 'TO ',
    0xCD: 'STEP ', 0xCE: 'DEF FN ', 0xCF: 'CAT ', 0xD0: 'FORMAT ',
    0xD1: 'MOVE ', 0xD2: 'ERASE ', 0xD3: 'OPEN #', 0xD4: 'CLOSE #',
    0xD5: 'MERGE ', 0xD6: 'VERIFY ', 0xD7: 'BEEP ', 0xD8: 'CIRCLE ',
    0xD9: 'INK ', 0xDA: 'PAPER ', 0xDB: 'FLASH ', 0xDC: 'BRIGHT ',
    0xDD: 'INVERSE ', 0xDE: 'OVER ', 0xDF: 'OUT ', 0xE0: 'LPRINT ',
    0xE1: 'LLIST ', 0xE2: 'STOP ', 0xE3: 'READ ', 0xE4: 'DATA ',
    0xE5: 'RESTORE ', 0xE6: 'NEW ', 0xE7: 'BORDER ', 0xE8: 'CONTINUE ',
    0xE9: 'DIM ', 0xEA: 'REM ', 0xEB: 'FOR ', 0xEC: 'GO TO ', 0xED: 'GO SUB ',
    0xEE: 'INPUT ', 0xEF: 'LOAD ', 0xF0: 'LIST ', 0xF1: 'LET ',
    0xF2: 'PAUSE ', 0xF3: 'NEXT ', 0xF4: 'POKE ', 0xF5: 'PRINT ',
    0xF6: 'PLOT ', 0xF7: 'RUN ', 0xF8: 'SAVE ', 0xF9: 'RANDOMIZE ',
    0xFA: 'IF ', 0xFB: 'CLS ', 0xFC: 'DRAW ', 0xFD: 'CLEAR ',
    0xFE: 'RETURN ', 0xFF: 'COPY ',
}

CONTROL = {
    0x06: '{PRINT-COMMA}', 0x08: '{LEFT}', 0x09: '{RIGHT}', 0x0A: '{DOWN}',
    0x0B: '{UP}', 0x0C: '{DELETE}', 0x0D: '\n', 0x10: '{INK}', 0x11: '{PAPER}',
    0x12: '{FLASH}', 0x13: '{BRIGHT}', 0x14: '{INVERSE}', 0x15: '{OVER}',
    0x16: '{AT}', 0x17: '{TAB}',
}


def float5(b):
    """Decode the 5-byte Spectrum number format."""
    if b[0] == 0:                        # small integer form
        sign = b[1]
        v = b[2] | (b[3] << 8)
        return -(65536 - v) if sign == 0xFF else v
    exp = b[0] - 128
    mant = ((b[1] | 0x80) << 24) | (b[2] << 16) | (b[3] << 8) | b[4]
    val = mant * (2.0 ** (exp - 32))
    return -val if (b[1] & 0x80) else val


def detokenise(data):
    out = []
    p = 0
    while p + 4 <= len(data):
        line = (data[p] << 8) | data[p + 1]
        if line > 9999:                  # variables area begins
            break
        ln = data[p + 2] | (data[p + 3] << 8)
        p += 4
        body = data[p:p + ln]
        p += ln
        s = []
        q = 0
        while q < len(body):
            c = body[q]
            if c == 0x0E:                # binary form of preceding number
                if q + 5 < len(body) + 1:
                    val = float5(body[q + 1:q + 6])
                    s.append(f'<={val}>')
                q += 6
                continue
            if c == 0x0D:
                q += 1
                continue
            if c in (0x10, 0x11, 0x12, 0x13, 0x14, 0x15):
                s.append(f'{{{CONTROL.get(c, "?")[1:-1]} {body[q+1]}}}')
                q += 2
                continue
            if c in (0x16, 0x17):
                s.append(f'{{{CONTROL.get(c, "?")[1:-1]} {body[q+1]},{body[q+2]}}}')
                q += 3
                continue
            if c in TOKENS:
                s.append(TOKENS[c])
            elif c in CONTROL:
                s.append(CONTROL[c])
            elif 32 <= c < 127:
                s.append(chr(c))
            else:
                s.append(f'\\x{c:02X}')
            q += 1
        out.append((line, ''.join(s)))
    return out, p


if __name__ == '__main__':
    args = sys.argv[1:]
    off = 0
    ln = None
    if '--offset' in args:
        i = args.index('--offset'); off = int(args[i + 1], 0); del args[i:i + 2]
    if '--len' in args:
        i = args.index('--len'); ln = int(args[i + 1], 0); del args[i:i + 2]
    data = open(args[0], 'rb').read()
    data = data[off:off + ln] if ln else data[off:]
    lines, consumed = detokenise(data)
    for n, t in lines:
        print(f'{n:5d} {t}')
    print(f'\n-- {consumed} of {len(data)} bytes consumed as BASIC; '
          f'{len(data) - consumed} bytes trailing (variables area or smuggled binary)')
