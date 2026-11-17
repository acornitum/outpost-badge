"""Minimal ST25DV04K (NFC Type 5 tag with I2C) driver.

Two I2C addresses, both 16-bit memory addressing:
  0x53  user memory (0x0000..0x01FF, 512 bytes) and dynamic registers (0x2000..)
  0x57  system configuration (UID, IC_REF, memory size, GPO config, ...)

Only user memory and dynamic registers are written here. System config is
read-only on purpose: a wrong write there can lock the tag from the RF side.
"""
import time

USER = 0x53
SYS = 0x57

# system area (read via 0x57)
REG_MEM_SIZE = 0x0014   # 3 bytes: (blocks-1) as LE16, then (block bytes-1)
REG_IC_REF = 0x0017
REG_UID = 0x0018        # 8 bytes, LSB first

# dynamic registers (via 0x53)
DYN_GPO_CTRL = 0x2000
DYN_EH_CTRL = 0x2002
DYN_RF_MNGT = 0x2003
DYN_I2C_SSO = 0x2004
DYN_IT_STS = 0x2005
DYN_MB_CTRL = 0x2006


class ST25DV:
    def __init__(self, i2c):
        self.i2c = i2c

    def read(self, addr, n, dev=USER):
        buf = bytearray(n)
        while not self.i2c.try_lock():
            pass
        try:
            self.i2c.writeto_then_readfrom(dev, bytes([addr >> 8, addr & 0xFF]), buf)
        finally:
            self.i2c.unlock()
        return bytes(buf)

    def write(self, addr, data, dev=USER):
        """Write in small chunks that never cross a 16-byte page, then wait
        for the internal write cycle (typ 5 ms) by ack-polling."""
        data = bytes(data)
        i = 0
        while i < len(data):
            room = 16 - ((addr + i) & 0x0F)
            chunk = data[i:i + room]
            a = addr + i
            self._locked_write(dev, bytes([a >> 8, a & 0xFF]) + chunk)
            i += len(chunk)

    def _locked_write(self, dev, payload):
        while not self.i2c.try_lock():
            pass
        try:
            self.i2c.writeto(dev, payload)
            # ack polling: device NACKs while its write cycle is running
            deadline = time.monotonic() + 0.05
            while time.monotonic() < deadline:
                try:
                    self.i2c.writeto(dev, b"")
                    return
                except OSError:
                    time.sleep(0.001)
            raise OSError("ST25DV write timeout")
        finally:
            self.i2c.unlock()

    def write_changed(self, addr, data):
        """Write `data`, touching only the 4-byte blocks that differ from what
        is already stored. Saves EEPROM wear and time on a normal boot, when the
        content has not changed. Returns the number of blocks written."""
        data = bytes(data) + b"\x00" * (-len(data) % 4)
        cur = self.read(addr, len(data))
        n = 0
        for off in range(0, len(data), 4):
            if cur[off:off + 4] != data[off:off + 4]:
                self.write(addr + off, data[off:off + 4])
                n += 1
        return n

    # -- identity -----------------------------------------------------------
    def uid(self):
        return bytes(reversed(self.read(REG_UID, 8, SYS)))   # MSB first (E0 02 ...)

    def ic_ref(self):
        return self.read(REG_IC_REF, 1, SYS)[0]

    def mem_bytes(self):
        b = self.read(REG_MEM_SIZE, 3, SYS)
        return ((b[0] | (b[1] << 8)) + 1) * (b[2] + 1)

    # -- NDEF (Type 5 tag) --------------------------------------------------
    def write_ndef(self, message):
        """Write a Capability Container and one NDEF TLV holding `message`."""
        size = self.mem_bytes()
        n = len(message)
        if n > 254 or 4 + 2 + n + 1 > size:
            raise ValueError("NDEF message too long")
        cc = bytes([0xE1, 0x40, min(size // 8, 255), 0x00])
        tlv = bytes([0x03, n]) + bytes(message) + b"\xFE"
        # body first, CC last: a reader never sees a CC pointing at half a message
        self.write(4, tlv)
        self.write(0, cc)

    def read_ndef(self):
        """Return the raw NDEF message bytes, b"" for an empty tag, or None if
        the tag has no valid Capability Container / NDEF TLV."""
        return scan_ndef(self.read(0, self.mem_bytes()))


def scan_ndef(mem):
    """Find the NDEF TLV in a Type 5 tag image. Phones may put other TLVs
    (NULL, lock/memory control) before it and use the 3-byte length form."""
    if len(mem) < 8 or mem[0] not in (0xE1, 0xE2):
        return None
    i = 4
    while i + 1 < len(mem):
        t = mem[i]
        if t == 0x00:
            i += 1
            continue
        if t == 0xFE:
            return None
        n, hdr = mem[i + 1], 2
        if n == 0xFF:
            if i + 3 >= len(mem):
                return None
            n, hdr = (mem[i + 2] << 8) | mem[i + 3], 4
        if t == 0x03:
            if i + hdr + n > len(mem):
                return None
            return bytes(mem[i + hdr:i + hdr + n])
        i += hdr + n
    return None


def ndef_records(msg):
    """Split an NDEF message into [(tnf, type, payload), ...]. Returns None if
    it is malformed. Chunked records are not supported."""
    recs, i = [], 0
    try:
        while i < len(msg):
            h = msg[i]
            i += 1
            tl = msg[i]
            i += 1
            if h & 0x10:
                pl = msg[i]
                i += 1
            else:
                pl = (msg[i] << 24) | (msg[i + 1] << 16) | (msg[i + 2] << 8) | msg[i + 3]
                i += 4
            idl = 0
            if h & 0x08:
                idl = msg[i]
                i += 1
            typ = bytes(msg[i:i + tl])
            i += tl + idl
            payload = bytes(msg[i:i + pl])
            if len(payload) != pl:
                return None
            i += pl
            recs.append((h & 0x07, typ, payload))
            if h & 0x40:
                break
    except IndexError:
        return None
    return recs


_URI_PREFIX = ("", "http://www.", "https://www.", "http://", "https://",
               "tel:", "mailto:")


def _ascii(b):
    try:
        return b.decode("utf-8")
    except Exception:
        return "".join(chr(c) if 32 <= c < 127 else "?" for c in b)


def describe(msg):
    """Human-readable text for the first record of an NDEF message."""
    recs = ndef_records(msg) if msg else []
    if not recs:
        return None
    tnf, typ, p = recs[0]
    if tnf == 1 and typ == b"T" and p:
        return _ascii(p[1 + (p[0] & 0x3F):])
    if tnf == 1 and typ == b"U" and p:
        pre = _URI_PREFIX[p[0]] if p[0] < len(_URI_PREFIX) else ""
        return pre + _ascii(p[1:])
    if tnf == 2 and typ in (b"text/vcard", b"text/x-vcard"):
        keep = []
        for line in _ascii(p).replace("\r", "").split("\n"):
            for key in ("FN", "TEL", "EMAIL", "ORG"):
                if line.startswith(key) and ":" in line:
                    keep.append(line.split(":", 1)[1])
                    break
        return "\n".join(keep) or "(vCard)"
    return "(" + _ascii(typ) + ")"


def uri_record(uri):
    """Short NDEF URI record. Handles https:// and http:// prefix codes."""
    code, rest = 0x00, uri
    for c, p in ((0x04, "https://"), (0x03, "http://"),
                 (0x02, "https://www."), (0x01, "http://www.")):
        if uri.startswith(p):
            code, rest = c, uri[len(p):]
            break
    payload = bytes([code]) + rest.encode()
    return bytes([0xD1, 0x01, len(payload), 0x55]) + payload


def text_record(text, lang="en"):
    payload = bytes([len(lang)]) + lang.encode() + text.encode()
    return bytes([0xD1, 0x01, len(payload), 0x54]) + payload
