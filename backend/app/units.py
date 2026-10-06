"""Unit normalization.

Every adapter declares the unit exactly as the provider publishes it; this module
maps it to one canonical unit per dimension. Unknown units raise - they are never
guessed.

Canonical units
  oil rate      kb/d    (thousand barrels per day)
  oil stock     mb      (million barrels)
  gas volume    bcf     (billion cubic feet)
  gas rate      bcf/d

Ambiguity note: EIA uses Roman-numeral M = thousand ("MBBL", "MBBL/D"), while the
market convention "mb/d" means MILLION b/d. The aliases below are explicit and
case-sensitive for exactly that reason.
"""
from typing import Dict, Tuple

BCF_PER_BCM = 35.3147  # 1 bcm = 35.3147 bcf (at standard conditions)

# alias -> (canonical, factor)
_TABLE: Dict[str, Tuple[str, float]] = {
    # --- oil rates ---
    "bbl/d": ("kb/d", 1e-3),
    "b/d": ("kb/d", 1e-3),
    "kb/d": ("kb/d", 1.0),
    "kbd": ("kb/d", 1.0),
    "KBD": ("kb/d", 1.0),          # JODI
    "TBPD": ("kb/d", 1.0),         # EIA international: thousand barrels per day
    "MBBL/D": ("kb/d", 1.0),       # EIA: M = thousand
    "mb/d": ("kb/d", 1e3),         # million barrels per day
    "mmb/d": ("kb/d", 1e3),
    "MMBBL/D": ("kb/d", 1e3),
    # --- oil stocks ---
    "bbl": ("mb", 1e-6),
    "kbbl": ("mb", 1e-3),
    "KBBL": ("mb", 1e-3),          # JODI
    "MBBL": ("mb", 1e-3),          # EIA: thousand barrels
    "mb": ("mb", 1.0),
    "mmbbl": ("mb", 1.0),
    # --- gas volumes ---
    "bcf": ("bcf", 1.0),
    "Bcf": ("bcf", 1.0),
    "BCF": ("bcf", 1.0),
    "mmcf": ("bcf", 1e-3),
    "MMCF": ("bcf", 1e-3),         # EIA: million cubic feet
    "tcf": ("bcf", 1e3),
    "mcm": ("bcf", BCF_PER_BCM / 1e3),   # million cubic metres
    "bcm": ("bcf", BCF_PER_BCM),
    # --- gas rates ---
    "bcf/d": ("bcf/d", 1.0),
    "mmcf/d": ("bcf/d", 1e-3),
    "mcm/d": ("bcf/d", BCF_PER_BCM / 1e3),
    "bcm/d": ("bcf/d", BCF_PER_BCM),
}

# Units that pass through unchanged (prices, rates, indices).
_PASSTHROUGH = {
    "USD/bbl", "USD/MMBtu", "USD/t", "USD/lb", "USD/ozt", "index", "%", "% GDP",
    "local per USD", "JPY per USD", "count", "vessels/day", "t", "USD", "bp", "k", "USD mn",
}


class UnknownUnit(ValueError):
    pass


def normalize(value: float, unit: str) -> Tuple[float, str]:
    if unit in _PASSTHROUGH:
        return value, unit
    if unit not in _TABLE:
        raise UnknownUnit(f"no conversion registered for unit {unit!r}")
    canon, factor = _TABLE[unit]
    return value * factor, canon


def convert(value: float, unit_from: str, unit_to: str) -> float:
    """Convert between any two registered units of the same dimension."""
    v, canon_from = normalize(value, unit_from)
    if unit_to == canon_from:
        return v
    if unit_to not in _TABLE or _TABLE[unit_to][0] != canon_from:
        raise UnknownUnit(f"cannot convert {unit_from!r} -> {unit_to!r}")
    return v / _TABLE[unit_to][1]
