"""Per-metric sanity bounds. Failing observations go to the quarantine log."""
from typing import Optional, Tuple


class SanityChecker:
    def __init__(self, bounds: dict):
        self.bounds = bounds or {}

    def rule_for(self, entity: str, metric: str) -> Optional[dict]:
        return self.bounds.get(f"{entity}.{metric}") or self.bounds.get(f"*.{metric}")

    def check(self, entity: str, metric: str, value: float,
              prev_value: Optional[float] = None) -> Tuple[bool, str]:
        rule = self.rule_for(entity, metric)
        if not rule:
            return True, ""
        if "min_exclusive" in rule and not value > rule["min_exclusive"]:
            return False, f"value {value} <= floor {rule['min_exclusive']}"
        if "min" in rule and value < rule["min"]:
            return False, f"value {value} < min {rule['min']}"
        if "max" in rule and value > rule["max"]:
            return False, f"value {value} > max {rule['max']}"
        if prev_value is not None:
            if "max_move_abs" in rule and abs(value - prev_value) > rule["max_move_abs"]:
                return False, (f"move {value - prev_value:+.4g} vs prev {prev_value} "
                               f"exceeds ±{rule['max_move_abs']}")
            if "max_move_pct" in rule and prev_value != 0:
                pct = (value / prev_value - 1.0) * 100.0
                if abs(pct) > rule["max_move_pct"]:
                    return False, (f"move {pct:+.1f}% vs prev {prev_value} "
                                   f"exceeds ±{rule['max_move_pct']}%")
        return True, ""
