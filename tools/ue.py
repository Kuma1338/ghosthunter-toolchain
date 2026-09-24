r"""UE4 memory analysis helpers on top of the CE bridge.

Verified facts for this game (GhostHunterClientSteam, modified UE 4.27):
  - FNamePool entry header: (len << 6) | (hash << 1) | wide   [custom, anti-dump]
  - FName index -> (block << 16) | (byte_offset >> 1), blocks 0x20000 bytes
  - UObject: vtable@0, ObjectFlags(int32)@8, InternalIndex(int32)@C,
             ClassPrivate@0x10, NamePrivate(FName cmp idx int32)@0x18,
             OuterPrivate@0x20
  - GNames  = game.exe + 0xAFC9F40  (FNamePool: +8 CurrentBlock, +10 Blocks[])
  - GWorld  = game.exe + 0xB117F20  (UWorld*)
  - GEngine = game.exe + 0xB11DB40  (UEngine*)
"""
import struct
import sys

sys.path.insert(0, r"D:\gh_tools\tools")
import ce  # noqa: E402

MOD_BASE = None          # filled by attach()
MODULE_SIZE = 199303168


class UEReader:
    def __init__(self):
        self.proc = ce.call("get_process_info")
        self.mod_base = int(next(m["address"] for m in self.proc["modules"]
                                 if m["name"].startswith("GhostHunter")), 16)
        self.gnames = self.mod_base + 0xAFC9F40
        self.gworld = self.mod_base + 0xB117F20
        self.gengine = self.mod_base + 0xB11DB40
        # cached block pointers: block index -> base
        self._blocks = {}

    # ---------------- raw memory ----------------
    def read(self, addr, size, retries=3):
        for attempt in range(retries):
            r = ce.call("read_memory", {"address": hex(addr), "size": size})
            h = r.get("hex") or r.get("data") or ""
            if h:
                data = bytes.fromhex(h)
                if len(data) >= size:
                    return data[:size]
            import time
            time.sleep(0.1)
        raise RuntimeError(f"read failed at {addr:#x} size={size:#x}")

    def qword(self, addr):
        return struct.unpack("<Q", self.read(addr, 8))[0]

    def dword(self, addr):
        return struct.unpack("<I", self.read(addr, 4))[0]

    def float(self, addr):
        return struct.unpack("<f", self.read(addr, 4))[0]

    def fvector(self, addr):
        return struct.unpack("<fff", self.read(addr, 12))

    # ---------------- names ----------------
    def _block(self, block):
        b = self._blocks.get(block)
        if b is None:
            b = self.qword(self.gnames + 0x10 + block * 8)
            self._blocks[block] = b
        return b

    def fname(self, index):
        if index is None or index < 0:
            return None
        block = index >> 16
        offset = (index & 0xFFFF) * 2
        blk = self._block(block)
        if not blk:
            return None
        hdr = struct.unpack("<H", self.read(blk + offset, 2))[0]
        wide = hdr & 1
        ln = hdr >> 6
        if ln == 0 or ln > 250:
            return None
        raw = self.read(blk + offset + 2, ln)
        if wide:
            return raw.decode("utf-16-le", errors="replace")
        try:
            return raw.decode("ascii")
        except UnicodeDecodeError:
            return None

    # ---------------- objects ----------------
    def obj_info(self, addr, with_class_name=True):
        d = self.read(addr, 0x28)
        vtable, flags, idx, cls = struct.unpack("<QIIQ", d[:24])
        name_idx = struct.unpack("<I", d[0x18:0x1C])[0]
        outer = struct.unpack("<Q", d[0x20:0x28])[0]
        info = {
            "addr": addr, "vtable": vtable, "flags": flags,
            "internal_index": idx, "class": cls,
            "name": self.fname(name_idx), "outer": outer,
        }
        if with_class_name and 0x10000 < cls < 0x7FFFFFFFFFFF:
            try:
                info["class_name"] = self.obj_info(cls, with_class_name=False)["name"]
            except Exception:
                info["class_name"] = None
        return info

    # ---------------- tarray ----------------
    def tarray(self, addr):
        """TArray at addr -> (data_ptr, num, max)"""
        d = self.read(addr, 0x18)
        ptr, num, mx = struct.unpack("<QQQ", d[:24])
        return ptr, num, mx


if __name__ == "__main__":
    ue = UEReader()
    print(f"module base: {ue.mod_base:#x}")
    print(f"GNames: {ue.gnames:#x}  GWorld: {ue.gworld:#x}  GEngine: {ue.gengine:#x}")
    w = ue.qword(ue.gworld)
    print(f"World: {w:#x}", ue.obj_info(w))
    e = ue.qword(ue.gengine)
    print(f"Engine: {e:#x}", ue.obj_info(e))
    # sanity: name of index 0
    print("fname(0) =", repr(ue.fname(0)), " fname(4) =", repr(ue.fname(4)))
