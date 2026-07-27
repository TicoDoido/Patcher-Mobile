# cython: language_level=3, boundscheck=False, wraparound=False, initializedcheck=False, cdivision=True
import bz2
import struct
from libc.stdint cimport int64_t

cdef inline int64_t _match_len(const unsigned char[:] old, int64_t old_pos, 
                               const unsigned char[:] new_data, int64_t new_pos) nogil:
    """Compara bytes diretamente em nível C sem overhead de Python."""
    cdef int64_t i = 0
    cdef int64_t limit = min(old.shape[0] - old_pos, new_data.shape[0] - new_pos)
    while i < limit:
        if old[old_pos + i] != new_data[new_pos + i]:
            break
        i += 1
    return i

cdef (int64_t, int64_t) _search(const int64_t[:] sa, const unsigned char[:] old, 
                                 const unsigned char[:] new_part) nogil:
    """Busca binária iterativa (mais rápida que recursiva em Cython)."""
    cdef int64_t st = 0
    cdef int64_t en = sa.shape[0] - 1
    cdef int64_t pivot, m_len, i
    cdef int64_t old_len = old.shape[0]
    cdef int64_t part_len = new_part.shape[0]
    
    while en - st >= 2:
        pivot = st + (en - st) // 2
        # Comparação manual de fatias (slices) para evitar criação de objetos Python
        if _compare_slices(old, sa[pivot], new_part) < 0:
            st = pivot
        else:
            en = pivot
            
    cdef int64_t l1 = _match_len(old, sa[st], new_part, 0)
    cdef int64_t l2 = _match_len(old, sa[en], new_part, 0)
    
    if l1 > l2:
        return l1, sa[st]
    return l2, sa[en]

cdef int _compare_slices(const unsigned char[:] old, int64_t old_start, 
                         const unsigned char[:] new_part) nogil:
    """Simula old[old_start:] < new_part sem criar objetos."""
    cdef int64_t i = 0
    cdef int64_t len_old = old.shape[0] - old_start
    cdef int64_t len_new = new_part.shape[0]
    cdef int64_t n = min(len_old, len_new)
    
    while i < n:
        if old[old_start + i] < new_part[i]: return -1
        if old[old_start + i] > new_part[i]: return 1
        i += 1
    if len_old < len_new: return -1
    if len_old > len_new: return 1
    return 0

def diff(bytes old_bytes, bytes new_bytes):
    cdef const unsigned char[:] old = old_bytes
    cdef const unsigned char[:] new_data = new_bytes
    cdef int64_t old_len = len(old_bytes)
    cdef int64_t new_len = len(new_bytes)
    
    # Geração do Suffix Array ainda usa o sorted do Python (que é C), 
    # pois implementar um SA-IS eficiente em Cython puro é muito complexo.
    cdef list sa_list = sorted(range(old_len), key=lambda i: old_bytes[i:])
    import array
    cdef int64_t[:] sa = array.array('q', sa_list) # 'q' para int64_t
    
    cdef int64_t last_scan = 0, last_pos = 0, scan = 0
    cdef int64_t match_len = 0, pos = 0, old_match_len = 0
    cdef int64_t lenf, lenb, s, sf, i, len_extra
    
    ctrl = []
    diff_data = bytearray()
    extra_data = bytearray()
    
    while scan < new_len:
        old_match_len = 0
        scan += match_len
        
        for i in range(scan, new_len):
            match_len, pos = _search(sa, old, new_data[i:])
            if match_len > old_match_len + 8:
                old_match_len = match_len
                break
            if i + match_len < old_len and old[i + match_len] == new_data[i + match_len]:
                old_match_len += 1
        
        if match_len != old_match_len or scan == new_len:
            s = 0; sf = 0; lenf = 0; lenb = 0
            while last_scan + lenf < scan and last_pos + lenf < old_len:
                if old[last_pos + lenf] == new_data[last_scan + lenf]: s += 1
                lenf += 1
                if s * 2 - lenf > sf * 2 - lenf:
                    sf = s; lenb = lenf
            
            lenf = lenb
            for i in range(lenf):
                diff_data.append((new_data[last_scan + i] - old[last_pos + i]) & 0xFF)
            
            len_extra = (scan - lenf) - last_scan
            for i in range(len_extra):
                extra_data.append(new_data[last_scan + lenf + i])
            
            ctrl.append((lenf, len_extra, (pos - lenf) - last_pos))
            last_scan = scan
            last_pos = pos

    # Empacotamento final
    def _offtout(x):
        y = ((-x) | (1 << 63)) if x < 0 else x
        return struct.pack('<Q', y)

    ctrl_bytes = b"".join([_offtout(c[0]) + _offtout(c[1]) + _offtout(c[2]) for c in ctrl])
    cb = bz2.compress(ctrl_bytes)
    db = bz2.compress(bytes(diff_data))
    eb = bz2.compress(bytes(extra_data))
    
    return b'BSDIFF40' + struct.pack('<q', len(cb)) + struct.pack('<q', len(db)) + struct.pack('<q', new_len) + cb + db + eb