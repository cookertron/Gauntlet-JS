# Chuckie Egg (ZX Spectrum) — tape & loader analysis

Source: `Chuckie Egg.tzx` — TZX v1.10, 20,611 bytes, "Created with Ramsoft MakeTZX".
All four data blocks are standard-speed (ID 0x10) and **checksum-clean** — no turbo
loader, no custom pilot tones, nothing to defeat.

## Tape block structure

| # | Block | Payload | Notes |
|---|-------|---------|-------|
| 0 | 0x30 text | — | "Created with Ramsoft MakeTZX" |
| 1 | 0x10 header | 17 B | type=BASIC, name=`CHUCKIE   `, len=1624, autostart line 10, vars@18 |
| 2 | 0x10 data | 1624 B | BASIC line 10 + loader code + title-screen assets |
| 3 | 0x10 data | 2 B | headerless, `01 00` → loaded into ERR_SP |
| 4 | 0x10 data | 18900 B | headerless, **the game** |

Extracted by `tools/tzx_parse.py` into `extracted/`.

## Block 2 layout (loads to BASIC area, org 0x5CCB / 23755)

Only the first 18 bytes are real BASIC. The header's "vars@18" means everything
after that is raw binary masquerading as the variables area.

| Range | Size | Contents |
|-------|------|----------|
| 0x5CCB–0x5CDC | 18 | BASIC line 10: `RANDOMIZE USR 24307` |
| 0x5CDD–0x5CE2 | 6 | padding |
| **0x5CE3–0x5EF2** | **528** | **66 custom 8×8 character definitions** (title screen tiles) |
| 0x5EF3–0x601B | 297 | loader machine code (entry = 24307) |
| **0x601C–0x631B** | **768** | **24×32 title-screen character map** |
| 0x631C–0x6322 | 7 | trailing |

Rendered tile sheet: `extracted/loader_font.png`.

## Loader flow (0x5EF3)

1. `LD A,7 / LD (0x5C48),A` — set BORDCR.
2. Clear 0x4000–0x5AFF (display + attributes), 6912 bytes.
3. `OUT (0xFE),0` — black border.
4. Draw title screen: 24 rows × 32 cols, reading codes from **0x601C**,
   plotting via the routine at **0x5FC9**.
5. Paint attributes in horizontal bands via the fill helper at **0x5FC4**
   (`LD (HL),A / INC HL / DJNZ`) — colours 7, 5, 6, 0x85, 0x10, 0x17, 0x02…
6. `LD IX,0x5C3D / LD DE,2 / SCF / LD A,0xFF / CALL 0x0556` — load the 2-byte block into ERR_SP.
7. `LD IX,0x8214 / LD DE,0x49D4 / SCF / LD A,0xFF / CALL 0x0556` — **load 18900 bytes to 0x8214**.
8. `LD HL,0x5DC0 / LD (0x5C63),HL / LD (0x5C65),HL` — point STKBOT/STKEND at 0x5DC0.
9. `JP 0xA410` — **game entry point**.

`CALL 0x0556` is the stock ROM LD-BYTES routine, so loading is entirely standard.

### Character plot routine (0x5FC9)

Takes char code in A, row in D, column in E.

- Screen address: classic third/row/col calculation from 0x4000, `+0x800` per third,
  `(row AND 7) XOR 7` then `<<5` for the pixel row, then `+ column`.
- **Font source is selected by code value:**
  - code `< 0x5F` → ROM font at **0x3C00** (i.e. `0x3C00 + code*8`)
  - code `>= 0x5F` → custom set at **0x5CE3**, index = `code - 0x60`
- Copies 8 bytes with `INC D` between rows (the classic within-a-third increment).

Of the 768 title-screen cells: 568 use the ROM font, 200 use custom tiles.
Codes range 32–161, so custom indices 0–65 → exactly the 66 tiles at 0x5CE3,
which is why the tile data ends precisely where the code begins at 0x5EF3.

## Game memory map (block 4)

- **Load address: 0x8214 (33300)**
- **Length: 0x49D4 (18900)**
- **Occupies 0x8214–0xCBE7 (33300–52199)**
- **Entry point: 0xA410 (42000)**

Note the entry point is ~8700 bytes into the block, so 0x8214–0xA40F is very
likely data (graphics, level maps, tables) with code from 0xA410 onward.
That is the first hypothesis to test.

## Tools in this repo

- `tools/tzx_parse.py <tape.tzx> [outdir]` — list blocks, verify checksums, extract payloads
- `tools/dis.py <bin> <org> [start] [count]` — linear Z80 disassembly (needs `pip install z80dis`)
- `tools/tiles.py <bin> <org> <start> <count> <out.png>` — render 8×8 cells to a PNG sheet
