#!/usr/bin/env python3
"""
speckle.py -- rank byte ranges by how much they look like ART rather than NOISE.

Metric: for a w*8 x h bitmap, count set pixels whose 4-neighbours are ALL clear
("isolated"), as a fraction of set pixels.  Random noise at 50% density scores
~0.06 isolated but, more usefully, has ~0.5 density and a very high count of
1-pixel horizontal runs.  Real Spectrum art has long runs and low isolation.

We use a combined score:
    speckle = isolated_frac  +  singleton_run_frac
where singleton_run_frac is the fraction of horizontal set-runs of length 1.
Low = art, high = noise.
"""
import sys


def unpack(data, off, w, h):
    """rows of w*8 booleans, row-major, w bytes per row."""
    g = []
    for y in range(h):
        row = []
        for b in range(w):
            v = data[off + y * w + b]
            for k in range(8):
                row.append((v >> (7 - k)) & 1)
        g.append(row)
    return g


def score_grid(g):
    h = len(g)
    w = len(g[0])
    setpx = 0
    iso = 0
    runs = 0
    singles = 0
    for y in range(h):
        x = 0
        while x < w:
            if g[y][x]:
                setpx += 1
                n = 0
                if x > 0 and g[y][x - 1]:
                    n += 1
                if x < w - 1 and g[y][x + 1]:
                    n += 1
                if y > 0 and g[y - 1][x]:
                    n += 1
                if y < h - 1 and g[y + 1][x]:
                    n += 1
                if n == 0:
                    iso += 1
                x += 1
            else:
                x += 1
    for y in range(h):
        x = 0
        while x < w:
            if g[y][x]:
                s = x
                while x < w and g[y][x]:
                    x += 1
                runs += 1
                if x - s == 1:
                    singles += 1
            else:
                x += 1
    if setpx == 0:
        return None
    density = setpx / (w * h)
    return {'density': density,
            'iso': iso / setpx,
            'single_runs': singles / runs if runs else 0,
            'setpx': setpx,
            'speckle': iso / setpx + (singles / runs if runs else 0)}


def score(data, off, w, h):
    if off + w * h > len(data):
        return None
    return score_grid(unpack(data, off, w, h))


def main():
    path = sys.argv[1]
    data = open(path, 'rb').read()
    w = h = None
    base, ln, step = 0, len(data), None
    a = sys.argv[2:]
    while a:
        if a[0] == '--w':
            w = int(a[1], 0); del a[:2]
        elif a[0] == '--h':
            h = int(a[1], 0); del a[:2]
        elif a[0] == '--base':
            base = int(a[1], 0); del a[:2]
        elif a[0] == '--len':
            ln = int(a[1], 0); del a[:2]
        elif a[0] == '--step':
            step = int(a[1], 0); del a[:2]
        else:
            del a[:1]
    w = w or 2
    h = h or 16
    step = step or w * h
    rows = []
    for off in range(base, min(base + ln, len(data)) - w * h, step):
        s = score(data, off, w, h)
        if s and 0.10 < s['density'] < 0.75 and s['setpx'] > 20:
            rows.append((s['speckle'], off, s))
    rows.sort()
    print(f'{len(rows)} candidate blocks, best (lowest speckle) first:')
    for sp, off, s in rows[:40]:
        print(f'  ${off:04X} speckle={sp:.3f} iso={s["iso"]:.3f} '
              f'single={s["single_runs"]:.3f} dens={s["density"]:.2f}')


if __name__ == '__main__':
    main()
