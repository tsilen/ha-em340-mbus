#!/usr/bin/env python3
"""Offline test: frame #1 captured from an EM340 (anonymised) + synthetic frames #2..#5 built from the protocol table."""
import em340_mbus as m

REAL1 = bytes.fromhex("68 65 65 68 08 01 72 78 56 34 12 36 1c c7 02 c5 00 00 00 04 05 40 e2 01 00 04 fb 82 75 14 1c 00 00 04 2a e7 64 00 00 04 fb 97 72 5c f0 ff ff 04 fb b7 72 5f 68 00 00 02 fd ba 73 dc 03 84 80 80 40 fd 48 c0 0f 00 00 04 fd 48 18 09 00 00 84 40 fd 59 b4 29 00 00 84 80 40 fd 59 68 00 00 00 84 c0 40 fd 59 f2 03 00 00 1f af 16")

def dife(su):  # subunit -> DIFE chain (bit6 of each DIFE)
    out, n = [], 0
    while su >> n:
        out.append(0x40 if (su >> n) & 1 else 0); n += 1
    return [x | 0x80 for x in out[:-1]] + out[-1:] if out else []

def rec(su, vif, val, ln=4):
    dif = (0x04 if ln == 4 else 0x02) | (0x80 if su else 0)
    return bytes([dif] + dife(su) + list(vif)) + val.to_bytes(ln, "little", signed=True)

def frame(recs, more, acc=0xc6):
    ud = bytes([0x08, 0x01, 0x72]) + REAL1[7:19] + b"".join(recs) + (b"\x1f" if more else b"")
    return bytes([0x68, len(ud), len(ud), 0x68]) + ud + bytes([sum(ud) & 0xFF, 0x16])

W, KVAR, KVA, PF, V, A, WH, KVARH, HZ = (0x2A,), (0xFB, 0x97, 0x72), (0xFB, 0xB7, 0x72), \
    (0xFD, 0xBA, 0x73), (0xFD, 0x48), (0xFD, 0x59), (0x05,), (0xFB, 0x82, 0x75), (0xFB, 0x2E)
SYN = [
    frame([rec(1, W, 24800), rec(2, W, 230), rec(3, W, 800), rec(1, KVAR, -3800), rec(2, KVAR, 10),
           rec(3, KVAR, -210), rec(1, KVA, 25100), rec(2, KVA, 240), rec(3, KVA, 2330),
           rec(1, PF, 988, 2), rec(2, PF, 958, 2), rec(3, PF, -343, 2)], True),
    frame([rec(5, V, 4030), rec(6, V, 4035), rec(7, V, 4028), rec(1, V, 2328), rec(2, V, 2331),
           rec(3, V, 2325), rec(4, WH, 12345), rec(4, KVARH, 678), rec(5, WH, 42), rec(5, KVARH, 3),
           rec(0, HZ, 500, 2)], True),
    frame([rec(1, WH, 150000), rec(2, WH, 40000), rec(3, WH, 42532), rec(4, W, 25000), rec(5, W, 98000)], True),
    frame([rec(6, WH, 200000), rec(7, WH, 32532)], False),
]

def run():
    for f in [REAL1] + SYN:
        assert m.parse_long(b"\x00\xe5" + f + b"\x00")[0] == f, "framing/checksum"
    hdr, recs, more = m.decode_frame(REAL1)
    assert more and hdr["id"] == "12345678" and hdr["manufacturer"] == "GAV" and hdr["medium"] == 2
    hdr, st, unk = m.decode_states([REAL1])
    exp = {"energy_import": 12345.6, "reactive_energy_import": 718.8, "power": 2583.1,
           "reactive_power": -400.4, "apparent_power": 2671.9, "power_factor": 0.988,
           "voltage_ll": 403.2, "voltage_ln": 232.8, "current_l1": 10.676, "current_l2": 0.104,
           "current_l3": 1.01}
    assert st == exp, st
    assert not unk, unk
    print("frame #1:", hdr, st)
    hdr, st, unk = m.decode_states([REAL1] + SYN)
    assert not unk, unk
    assert len(st) == 41 and st["frequency"] == 50.0 and st["energy_export"] == 4.2 and \
        st["voltage_l3_l1"] == 402.8 and st["power_factor_l3"] == -0.343 and st["energy_import_t2"] == 3253.2, st
    print("frames #1-#5:", len(st), "entities OK")
    # write guard
    class S: write = lambda self, d: len(d)
    s = S(); w = m.guarded_write(s)
    assert w(m.short_frame(0x40, 1)) == 5 and w(m.short_frame(0x7B, 1)) == 5
    for bad in (bytes.fromhex("68 03 03 68 53 01 50 a4 16"), m.short_frame(0x53, 1), b"\x10\x7b\x01\x00\x16"):
        try: w(bad); raise AssertionError("guard let through %s" % bad.hex())
        except RuntimeError: pass
    print("write guard OK")

if __name__ == "__main__":
    run()
