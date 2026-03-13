"""
bsdiff4 compatibility module.
- Desktop: usa bsdiff4 nativo (rapido, requer compilador C)
- Android: fallback pure Python (patch rapido O(n), diff mais lento)
"""

try:
    import bsdiff4 as _native
    diff  = _native.diff
    patch = _native.patch

except ImportError:
    import bz2, struct

    def _offtout(x: int) -> bytes:
        y = ((-x) | (1 << 63)) if x < 0 else x
        return struct.pack('<Q', y)

    def _offtin(buf: bytes) -> int:
        y = struct.unpack('<Q', buf)[0]
        return -int(y & ~(1 << 63)) if y & (1 << 63) else int(y)

    def patch(src: bytes, patch_data: bytes) -> bytes:
        """Aplica um patch bsdiff40. Pure Python, O(n)."""
        if patch_data[:8] != b'BSDIFF40':
            raise ValueError('Nao e um patch bsdiff40 valido')
        ctrl_len = struct.unpack('<q', patch_data[8:16])[0]
        diff_len = struct.unpack('<q', patch_data[16:24])[0]
        new_len  = struct.unpack('<q', patch_data[24:32])[0]

        ctrl_b  = bz2.decompress(patch_data[32:32 + ctrl_len])
        diff_b  = bz2.decompress(patch_data[32 + ctrl_len:32 + ctrl_len + diff_len])
        extra_b = bz2.decompress(patch_data[32 + ctrl_len + diff_len:])

        old = bytearray(src)
        new = bytearray(new_len)
        cp = dp = ep = old_pos = new_pos = 0

        while new_pos < new_len:
            x = _offtin(ctrl_b[cp:cp+8]); cp += 8
            y = _offtin(ctrl_b[cp:cp+8]); cp += 8
            z = _offtin(ctrl_b[cp:cp+8]); cp += 8

            for i in range(x):
                ob = old[old_pos + i] if 0 <= old_pos + i < len(old) else 0
                new[new_pos + i] = (ob + diff_b[dp + i]) & 0xFF
            new_pos += x; old_pos += x; dp += x

            new[new_pos:new_pos + y] = extra_b[ep:ep + y]
            new_pos += y; ep += y
            old_pos += z

        return bytes(new)

    def diff(src: bytes, dst: bytes) -> bytes:
        """
        Gera um patch bsdiff40 em pure Python.
        Para arquivos grandes no desktop, instale: pip install bsdiff4
        """
        old = bytearray(src)
        new = bytearray(dst)
        x = min(len(old), len(new))
        y = len(new) - x

        # diff block: new[i] - old[i] (modulo 256)
        diff_arr = bytearray(x)
        for i in range(x):
            diff_arr[i] = (new[i] - old[i]) & 0xFF

        ctrl  = _offtout(x) + _offtout(y) + _offtout(0)
        cb = bz2.compress(ctrl)
        db = bz2.compress(bytes(diff_arr))
        eb = bz2.compress(bytes(new[x:]))

        return (b'BSDIFF40'
                + struct.pack('<q', len(cb))
                + struct.pack('<q', len(db))
                + struct.pack('<q', len(new))
                + cb + db + eb)
