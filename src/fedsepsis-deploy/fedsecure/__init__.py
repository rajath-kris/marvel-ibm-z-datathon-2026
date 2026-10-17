"""fedsecure - pairwise-mask secure aggregation for FedAvg (standard library only).

Each pair of sites (i, j) shares a secret seed. For every message tag (e.g. "round-3") both derive the same
mask vector m_ij = SHAKE-256(seed_ij | tag). The site with the smaller id ADDS m_ij, the other SUBTRACTS it,
so the masks cancel exactly in the sum over all sites and the aggregator learns only the total.

Values are encoded as fixed-point integers modulo 2**128 (resolution 2**-40) so cancellation is exact:
a masked value is uniform on [0, 2**128) and says nothing about the site's value on its own.

What a site sends per round: masked n*w (vector), masked n*b, and n in the clear. The aggregator sums the
masked vectors mod 2**128, decodes, and divides by the total n -> exactly the n-weighted FedAvg.

Limits (by design, see README): the aggregator still learns each site's n and the totals; any site whose
mask partner drops out breaks the round (no dropout recovery); two colluding parties can learn more; seeds
are pre-shared files here, where a production system would agree them with Diffie-Hellman.
"""
import hashlib

MOD = 1 << 128
SCALE = 1 << 40
HALF = MOD >> 1
_LIMIT = HALF // (SCALE * 64)          # |value| bound so a sum over <= 64 sites cannot wrap
_DOMAIN = b"fedsecure-v1|"

__all__ = ["MOD", "SCALE", "encode", "decode", "pair_mask", "mask_vector", "unmask_sum",
           "pair_key", "load_pair_seeds"]


def encode(x):
    """float -> fixed-point integer mod 2**128."""
    x = float(x)
    if not abs(x) < _LIMIT:
        raise ValueError(f"value {x!r} outside the secure-aggregation range (|x| < {_LIMIT:.3g})")
    return round(x * SCALE) % MOD


def decode(v):
    """integer mod 2**128 -> float (two's-complement style for negatives)."""
    v %= MOD
    if v >= HALF:
        v -= MOD
    return v / SCALE


def pair_key(a, b):
    """Canonical name of the pair (order-independent)."""
    lo, hi = sorted((str(a), str(b)))
    return f"{lo}__{hi}"


def pair_mask(seed, tag, length):
    """Deterministic mask vector: `length` integers uniform on [0, 2**128). Same on every machine (big-endian bytes)."""
    if not isinstance(seed, (bytes, bytearray)) or len(seed) < 16:
        raise ValueError("pair seed must be at least 16 bytes")
    stream = hashlib.shake_256(_DOMAIN + bytes(seed) + b"|" + str(tag).encode()).digest(16 * length)
    return [int.from_bytes(stream[16 * i:16 * i + 16], "big") for i in range(length)]


def mask_vector(site_id, sites, seeds, tag, values):
    """Encode `values` and add +mask for every peer with a larger id, -mask for every peer with a smaller id.
    seeds: {peer_id: seed_bytes} for every other site in `sites`."""
    site_id = str(site_id)
    peers = [str(s) for s in sites if str(s) != site_id]
    if site_id not in [str(s) for s in sites]:
        raise ValueError(f"{site_id} is not in the site list")
    missing = [p for p in peers if p not in seeds]
    if missing:
        raise ValueError(f"missing pair seeds for {missing}")
    out = [encode(v) for v in values]
    for p in peers:
        m = pair_mask(seeds[p], tag, len(out))
        if site_id < p:
            out = [(o + mi) % MOD for o, mi in zip(out, m)]
        else:
            out = [(o - mi) % MOD for o, mi in zip(out, m)]
    return out


def unmask_sum(vectors):
    """Sum masked vectors from ALL sites mod 2**128 and decode. Only meaningful when every site contributed."""
    vectors = list(vectors)
    n = len(vectors[0])
    if any(len(v) != n for v in vectors):
        raise ValueError("masked vectors have different lengths")
    return [decode(sum(col) % MOD) for col in zip(*vectors)]


def load_pair_seeds(directory, site_id):
    """Read this site's pair seeds: files '<a>__<b>.seed' (hex) that name site_id. Returns {peer: bytes}."""
    import os
    seeds = {}
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".seed"):
            continue
        a, _, b = name[:-5].partition("__")
        if site_id not in (a, b):
            continue
        with open(os.path.join(directory, name)) as f:
            seeds[b if a == site_id else a] = bytes.fromhex(f.read().strip())
    return seeds
