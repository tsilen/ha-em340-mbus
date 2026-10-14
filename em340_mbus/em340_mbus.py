#!/usr/bin/env python3
"""Read-only Carlo Gavazzi EM340 (M-Bus) -> Home Assistant MQTT bridge.
Only ever sends SND_NKE and REQ_UD2 short frames (enforced by a write guard).
Never sends SND_UD (record selection), reset, address or baud-rate changes.
Stdlib + pyserial only (tiny built-in MQTT 3.1.1 publisher).
Record map: Carlo Gavazzi 'EM330-EM340 M-Bus communication protocol' rev 4.3, Table 1/2."""
import os, sys, json, time, socket, struct, argparse

# ---------------------------------------------------------------- M-Bus link layer
def short_frame(c, a):
    return bytes([0x10, c, a, (c + a) & 0xFF, 0x16])

SND_NKE = 0x40
REQ_UD2 = (0x5B, 0x7B)          # FCV=1, FCB=0 / FCB=1
ALLOWED_C = {SND_NKE, *REQ_UD2}

def guarded_write(ser):
    raw = ser.write
    def write(data):
        d = bytes(data)
        if not (len(d) == 5 and d[0] == 0x10 and d[4] == 0x16 and d[1] in ALLOWED_C
                and d[3] == (d[1] + d[2]) & 0xFF):
            raise RuntimeError("refusing to send non read-only frame %s" % d.hex(" "))
        return raw(d)
    return write

def parse_long(buf):
    """Return (frame_bytes, rest) for the first valid long frame in buf, or (None, buf)."""
    i = buf.find(b"\x68")
    while i >= 0 and len(buf) - i >= 6:
        L = buf[i + 1]
        if buf[i + 2] == L and buf[i + 3] == 0x68 and L >= 3:
            end = i + 4 + L + 2
            if len(buf) < end: return None, buf
            body = buf[i + 4:i + 4 + L]
            if buf[end - 2] == sum(body) & 0xFF and buf[end - 1] == 0x16:
                return bytes(buf[i:end]), buf[end:]
        i = buf.find(b"\x68", i + 1)
    return None, buf

# ---------------------------------------------------------------- application layer (EN 13757-3)
DATA_LEN = {0: 0, 1: 1, 2: 2, 3: 3, 4: 4, 5: 4, 6: 6, 7: 8, 0xD: None, 0xE: None}

def bcd(b):
    v = 0
    for x in reversed(b): v = v * 100 + (x >> 4) * 10 + (x & 0xF)
    return v

def decode_frame(frame):
    """Returns (header dict, [records], more_follows)."""
    ud = frame[4:-2]                     # C A CI ...
    c, a, ci = ud[0], ud[1], ud[2]
    if ci != 0x72: raise ValueError("unsupported CI %02x" % ci)
    h = ud[3:15]
    hdr = {"address": a, "id": "%08d" % bcd(h[0:4]),
           "manufacturer": "".join(chr(((h[5] << 8 | h[4]) >> s & 0x1F) + 64) for s in (10, 5, 0)),
           "version": h[6], "medium": h[7], "access": h[8], "status": h[9]}
    p, d, recs, more = 15, ud, [], False
    while p < len(d):
        dif = d[p]; p += 1
        if dif == 0x2F: continue                       # idle filler
        if dif in (0x0F, 0x1F):
            more = dif == 0x1F; break
        storage, tariff, subunit = (dif >> 6) & 1, 0, 0
        n, ext = 0, dif & 0x80
        while ext:
            e = d[p]; p += 1
            storage |= (e & 0x0F) << (1 + 4 * n)
            tariff |= ((e >> 4) & 3) << (2 * n)
            subunit |= ((e >> 6) & 1) << n
            n += 1; ext = e & 0x80
        vifs = [d[p]]; p += 1
        while vifs[-1] & 0x80:
            vifs.append(d[p]); p += 1
        ln = DATA_LEN.get(dif & 0x0F)
        if ln is None: raise ValueError("unsupported data field %x" % (dif & 0xF))
        raw = d[p:p + ln]; p += ln
        if dif & 0x0F in (9, 10, 11, 12, 14): val = bcd(raw)
        else: val = int.from_bytes(raw, "little", signed=True)
        recs.append({"vif": tuple(vifs), "storage": storage, "tariff": tariff,
                     "subunit": subunit, "raw": val})
    return hdr, recs, more

def vif_unit(vifs):
    """Generic VIF/VIFE -> (quantity, unit, scale). Covers the codes EM340 uses."""
    v0 = vifs[0] & 0x7F
    rest = [x & 0x7F for x in vifs[1:]]
    if vifs[0] in (0xFD, 0xFB):
        code, rest = rest[0], rest[1:]
        if vifs[0] == 0xFD:
            if 0x40 <= code <= 0x4F: q, u, s = "voltage", "V", 10 ** ((code & 0xF) - 9)
            elif 0x50 <= code <= 0x5F: q, u, s = "current", "A", 10 ** ((code & 0xF) - 12)
            elif code == 0x3A: q, u, s = "dimensionless", "", 1
            else: return None
        else:  # 0xFB: per EM340 protocol Table 2
            if 0x2C <= code <= 0x2F: q, u, s = "frequency", "Hz", 10 ** ((code & 3) - 3)
            elif code == 0x02: q, u, s = "reactive_energy", "kvarh", 1
            elif code == 0x17: q, u, s = "reactive_power", "kvar", 1
            elif code == 0x37: q, u, s = "apparent_power", "kVA", 1
            else: return None
    elif v0 <= 0x07: q, u, s = "energy", "Wh", 10 ** (v0 - 3)
    elif 0x28 <= v0 <= 0x2F: q, u, s = "power", "W", 10 ** ((v0 & 7) - 3)
    elif 0x24 <= v0 <= 0x27: q, u, s = "operating_time", "h", 1
    else: return None
    for e in rest:                                   # multiplicative correction 10^(nnn-6)
        if 0x70 <= e <= 0x77: s *= 10 ** ((e & 7) - 6)
    return q, u, s

# subunit -> entity suffix, per Table 1
PHASE = {1: "_l1", 2: "_l2", 3: "_l3"}
LL = {4: "voltage_ll", 5: "voltage_l1_l2", 6: "voltage_l2_l3", 7: "voltage_l3_l1"}

def map_record(r):
    """-> (key, value) in HA units, or None if not a known EM340 record."""
    vu = vif_unit(r["vif"])
    if not vu or r["storage"] or r["tariff"]: return None
    q, u, s = vu
    x, su = r["raw"] * s, r["subunit"]
    if q == "energy":
        k = {0: "energy_import", 1: "energy_import_l1", 2: "energy_import_l2", 3: "energy_import_l3",
             4: "energy_import_partial", 5: "energy_export", 6: "energy_import_t1",
             7: "energy_import_t2", 8: "energy_import_t3", 9: "energy_import_t4"}.get(su)
        return k and (k, round(x / 1000, 1))                            # Wh -> kWh
    if q == "reactive_energy":
        k = {0: "reactive_energy_import", 4: "reactive_energy_import_partial",
             5: "reactive_energy_export"}.get(su)
        return k and (k, round(x, 1))
    if q == "power":
        k = {0: "power", 4: "power_demand", 5: "power_demand_max"}.get(su) or \
            (su in PHASE and "power" + PHASE[su])
        return k and (k, round(x, 1))
    if q in ("reactive_power", "apparent_power"):
        base = q
        k = base if su == 0 else (su in PHASE and base + PHASE[su])
        return k and (k, round(x * 1000, 1))                            # kvar/kVA -> var/VA
    if q == "dimensionless":
        k = "power_factor" if su == 0 else (su in PHASE and "power_factor" + PHASE[su])
        return k and (k, round(x, 3))
    if q == "voltage":
        k = "voltage_ln" if su == 0 else LL.get(su) or (su in PHASE and "voltage" + PHASE[su] + "_n")
        return k and (k, round(x, 1))
    if q == "current":
        k = su in PHASE and "current" + PHASE[su]
        return k and (k, round(x, 3))
    if q == "frequency" and su == 0:
        return ("frequency", round(x, 2))
    return None

E, P, RP, AP, V, A = ("energy", "kWh", "total_increasing"), ("power", "W", "measurement"), \
    ("reactive_power", "var", "measurement"), ("apparent_power", "VA", "measurement"), \
    ("voltage", "V", "measurement"), ("current", "A", "measurement")
RE = ("reactive_energy", "kvarh", "total_increasing")
ENTITIES = {  # key: (device_class, unit, state_class, enabled_by_default)
    "energy_import": E + (True,), "energy_export": E + (True,),
    "energy_import_partial": ("energy", "kWh", "total", False),
    "energy_import_l1": E + (False,), "energy_import_l2": E + (False,), "energy_import_l3": E + (False,),
    "energy_import_t1": E + (False,), "energy_import_t2": E + (False,),
    "energy_import_t3": E + (False,), "energy_import_t4": E + (False,),
    "reactive_energy_import": RE + (False,), "reactive_energy_export": RE + (False,),
    "reactive_energy_import_partial": ("reactive_energy", "kvarh", "total", False),
    "power": P + (True,), "power_l1": P + (True,), "power_l2": P + (True,), "power_l3": P + (True,),
    "power_demand": P + (False,), "power_demand_max": P + (False,),
    "reactive_power": RP + (False,), "reactive_power_l1": RP + (False,),
    "reactive_power_l2": RP + (False,), "reactive_power_l3": RP + (False,),
    "apparent_power": AP + (False,), "apparent_power_l1": AP + (False,),
    "apparent_power_l2": AP + (False,), "apparent_power_l3": AP + (False,),
    "power_factor": ("power_factor", None, "measurement", True),
    "power_factor_l1": ("power_factor", None, "measurement", False),
    "power_factor_l2": ("power_factor", None, "measurement", False),
    "power_factor_l3": ("power_factor", None, "measurement", False),
    "voltage_ln": V + (False,), "voltage_ll": V + (False,),
    "voltage_l1_n": V + (True,), "voltage_l2_n": V + (True,), "voltage_l3_n": V + (True,),
    "voltage_l1_l2": V + (False,), "voltage_l2_l3": V + (False,), "voltage_l3_l1": V + (False,),
    "current_l1": A + (True,), "current_l2": A + (True,), "current_l3": A + (True,),
    "frequency": ("frequency", "Hz", "measurement", True),
}

def decode_states(frames):
    hdr, states, unknown = None, {}, []
    for f in frames:
        h, recs, _ = decode_frame(f); hdr = hdr or h
        for r in recs:
            m = map_record(r)
            if m and m[0] in ENTITIES: states[m[0]] = m[1]
            else: unknown.append(r)
    return hdr, states, unknown

# ---------------------------------------------------------------- serial polling
class Meter:
    def __init__(self, ser, addr, max_frames=8):
        self.ser, self.addr, self.max = ser, addr, max_frames
    def _xfer(self, c, expect_long):
        self.ser.reset_input_buffer()
        self.ser.write(short_frame(c, self.addr))
        buf, t0 = b"", time.time()
        while time.time() - t0 < 2.0:
            buf += self.ser.read(256)
            if not expect_long:
                if b"\xe5" in buf: return True
            else:
                f, _ = parse_long(buf)
                if f: return f
        return None
    def read_all(self):
        if not self._xfer(SND_NKE, False): raise TimeoutError("no E5 ack to SND_NKE")
        frames, fcb = [], 1                       # first REQ_UD2 after SND_NKE: FCB=1
        for _ in range(self.max):
            f = None
            for _try in range(2):                 # retry with same FCB -> slave repeats frame
                f = self._xfer(REQ_UD2[fcb], True)
                if f: break
            if not f: raise TimeoutError("no RSP_UD after %d frames" % len(frames))
            if f[5] != self.addr: raise ValueError("reply from address %d" % f[5])
            frames.append(f)
            _, _, more = decode_frame(f)
            if not more: break
            fcb ^= 1
        return frames

# ---------------------------------------------------------------- MQTT (same as uponor_bridge)
BASE = "em340_mbus"
class MQTT:
    def __init__(self, host, port, user, pw, cid=BASE):
        self.a = (host, port, user, pw, cid); self.s = None; self.last = 0
    def _str(self, x): x = x.encode(); return struct.pack("!H", len(x)) + x
    def _len(self, n):
        o = b""
        while True:
            b = n % 128; n //= 128
            o += bytes([b | (0x80 if n else 0)])
            if not n: return o
    def connect(self):
        host, port, user, pw, cid = self.a
        self.s = socket.create_connection((host, port), timeout=10)
        flags = 0x02 | (0x80 if user else 0) | (0x40 if pw else 0) | 0x04 | 0x20
        p = self._str("MQTT") + bytes([4, flags]) + struct.pack("!H", 60) + self._str(cid)
        p += self._str(BASE + "/status") + self._str("offline")
        if user: p += self._str(user)
        if pw: p += self._str(pw)
        self.s.sendall(b"\x10" + self._len(len(p)) + p)
        ack = self.s.recv(4)
        if len(ack) < 4 or ack[3] != 0: raise ConnectionError("MQTT CONNACK %r" % ack)
        self.last = time.time()
        self.pub(BASE + "/status", "online", True)
    def pub(self, topic, payload, retain=False):
        if isinstance(payload, (dict, list)): payload = json.dumps(payload)
        p = self._str(topic) + str(payload).encode()
        self.s.sendall(bytes([0x30 | (1 if retain else 0)]) + self._len(len(p)) + p); self.last = time.time()
    def ping(self):
        if time.time() - self.last > 30:
            self.s.sendall(b"\xc0\x00"); self.last = time.time()
            self.s.setblocking(False)
            try: self.s.recv(64)
            except BlockingIOError: pass
            finally: self.s.settimeout(10)

class Bridge:
    def __init__(self, mqtt, prefix):
        self.m, self.prefix, self.announced, self.cache = mqtt, prefix, set(), {}
    def publish(self, hdr, states):
        dev = "em340_%s" % hdr["id"]
        for k, v in states.items():
            if (dev, k) not in self.announced: self.discover(dev, hdr, k)
            if self.cache.get((dev, k)) != v:
                self.m.pub(f"{BASE}/{dev}/{k}", v, True); self.cache[(dev, k)] = v
    def discover(self, dev, hdr, key):
        dc, unit, sc, en = ENTITIES[key]
        cfg = {"unique_id": f"{dev}_{key}", "object_id": f"{dev}_{key}",
               "translation_key": key, "has_entity_name": True, "name": key.replace("_", " ").capitalize(),
               "state_topic": f"{BASE}/{dev}/{key}", "availability_topic": f"{BASE}/status",
               "enabled_by_default": en,
               "device": {"identifiers": [dev], "name": "EM340 %s" % hdr["id"],
                          "manufacturer": "Carlo Gavazzi", "model": "EM340 (M-Bus)",
                          "serial_number": hdr["id"], "sw_version": str(hdr["version"])}}
        if dc: cfg["device_class"] = dc
        if unit: cfg["unit_of_measurement"] = unit
        if sc: cfg["state_class"] = sc
        self.m.pub(f"{self.prefix}/sensor/{dev}/{key}/config", cfg, True)
        self.announced.add((dev, key))

# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default=os.environ.get("MBUS_PORT", ""))
    ap.add_argument("--baud", type=int, default=int(os.environ.get("MBUS_BAUD", 2400)))
    ap.add_argument("--address", type=int, default=int(os.environ.get("MBUS_ADDRESS", 1)))
    ap.add_argument("--interval", type=float, default=float(os.environ.get("POLL_INTERVAL", 10)))
    ap.add_argument("--mqtt-host", default=os.environ.get("MQTT_HOST", "core-mosquitto"))
    ap.add_argument("--mqtt-port", type=int, default=int(os.environ.get("MQTT_PORT", 1883)))
    ap.add_argument("--mqtt-user", default=os.environ.get("MQTT_USER", ""))
    ap.add_argument("--mqtt-pass", default=os.environ.get("MQTT_PASS", ""))
    ap.add_argument("--prefix", default="homeassistant")
    ap.add_argument("--dry-run", action="store_true", help="poll and print decoded values, no MQTT")
    ap.add_argument("--raw", action="store_true", help="with --dry-run: also print raw frames")
    a = ap.parse_args()
    if not a.port: sys.exit("serial port not set (--port or MBUS_PORT)")
    if not 1 <= a.address <= 250: sys.exit("address must be 1..250")
    import serial
    try:
        ser = serial.Serial(a.port, a.baud, bytesize=8, parity="E", stopbits=1, timeout=0.1)
    except serial.SerialException as e:
        print("cannot open serial port", a.port, ":", e, flush=True); sys.exit(2)
    ser.write = guarded_write(ser)
    meter = Meter(ser, a.address)
    mq = br = None; fails = 0
    while True:
        t0 = time.time()
        try:
            if not a.dry_run and mq is None:
                mq = MQTT(a.mqtt_host, a.mqtt_port, a.mqtt_user, a.mqtt_pass); mq.connect()
                br = Bridge(mq, a.prefix); print("mqtt connected", flush=True)
            try:
                frames = meter.read_all(); fails = 0
            except serial.SerialException as e:
                print("serial error:", e, "- exiting", flush=True); sys.exit(2)
            except (TimeoutError, ValueError) as e:
                fails += 1; print("meter read failed:", e, flush=True)
                if fails >= 30: print("meter unreachable, exiting", flush=True); sys.exit(3)
                frames = []
            if frames:
                hdr, st, unk = decode_states(frames)
                if a.dry_run:
                    if a.raw:
                        for f in frames: print(" ", f.hex(" "))
                    print(time.strftime("%H:%M:%S"), hdr["id"], "frames=%d" % len(frames), st, flush=True)
                    for r in unk: print("  unmapped record", r, flush=True)
                else: br.publish(hdr, st)
            if mq: mq.ping()
        except (OSError, ConnectionError) as e:
            if isinstance(e, serial.SerialException):
                print("serial error:", e, "- exiting", flush=True); sys.exit(2)
            print("mqtt error:", e, "- retrying in 10s", flush=True)
            try: mq and mq.s.close()
            except Exception: pass
            mq = None; time.sleep(10)
        time.sleep(max(0.5, a.interval - (time.time() - t0)))

if __name__ == "__main__":
    main()
