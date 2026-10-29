"""Pure logic for Flag Guard: find flag variants that crash more than control,
and build the Remote Config change that kills them. No I/O, so it's all testable."""
import copy
import math
from dataclasses import dataclass

# ponytail: fixed thresholds + one-sided z-test. Move to sequential testing
# (e.g. mSPRT) if false alarms or detection delay become a problem.
DEFAULTS = dict(min_users=500, min_crashes=10, min_ratio=2.0, alpha=0.001)
KILL_PREFIX = "killswitch_"
SIGNALS = ("FATAL", "NON_FATAL", "ANR")  # Crashlytics error_type values
# Non-fatals are whatever the app chooses to log: too easy to trip (or spoof) to kill a feature.
KILL_SIGNALS = {"FATAL", "ANR"}


@dataclass(frozen=True)
class Finding:
    flag: str
    variant: str
    version: str
    signal: str
    users: int
    crashed_users: int
    crash_rate: float
    control_rate: float
    p: float


def p_value(c1, n1, c0, n0):
    """One-sided two-proportion z-test: chance of seeing this gap if variant is no worse."""
    pooled = (c1 + c0) / (n1 + n0)
    se = math.sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n0))
    if se == 0:
        return 1.0
    return 0.5 * math.erfc((c1 / n1 - c0 / n0) / se / math.sqrt(2))


def detect(rows, flags, **thresholds):
    """rows: dicts with flag, variant, version, signal, users, crashed_users
    (crashed_users = distinct users with that signal).
    flags: {name: {"control": "<variant>", ...}} from flags.json."""
    t = {**DEFAULTS, **thresholds}
    groups = {}
    for r in rows:
        if r["flag"] in flags:
            groups.setdefault((r["flag"], r["version"], r["signal"]), {})[r["variant"]] = r

    pairs = []
    for (flag, version, signal), by_variant in groups.items():
        control_name = flags[flag]["control"]
        control = by_variant.get(control_name)
        if control is None:
            continue
        pairs += [(flag, version, signal, v, r, control) for v, r in by_variant.items() if v != control_name]

    findings = []
    for flag, version, signal, variant, r, c in pairs:
        n1, n0 = r["users"], c["users"]
        # Crash and user counts come from different exports; never let crashes exceed users.
        c1, c0 = min(r["crashed_users"], n1), min(c["crashed_users"], n0)
        if min(n1, n0) < t["min_users"] or c1 < t["min_crashes"]:
            continue
        rate, base = c1 / n1, c0 / n0
        if rate < t["min_ratio"] * base:
            continue
        p = p_value(c1, n1, c0, n0)
        if p < t["alpha"] / len(pairs):  # Bonferroni across every comparison this run
            findings.append(Finding(flag, variant, version, signal, n1, c1, rate, base, p))
    return findings


def action_for(f, flags, auto_kill, kills_today, max_kills=1):
    """Kill policy: "kill", "kill_capped" or "alerted"."""
    if not (auto_kill and flags[f.flag].get("auto_kill") and f.signal in KILL_SIGNALS):
        return "alerted"
    return "kill" if kills_today < max_kills else "kill_capped"


def is_killed(template, flag):
    param = template.get("parameters", {}).get(KILL_PREFIX + flag, {})
    return param.get("defaultValue", {}).get("value") == "true"


def kill_template(template, flag):
    """Copy of the Remote Config template with killswitch_<flag> forced to true."""
    # ponytail: assumes killswitch_* params are not inside a parameter group;
    # if one is, the API rejects the duplicate key and nothing changes.
    new = copy.deepcopy(template)
    new.pop("version", None)
    new.setdefault("parameters", {})[KILL_PREFIX + flag] = {
        "defaultValue": {"value": "true"},
        "valueType": "BOOLEAN",
    }
    check_only_kill(template, new, flag)
    return new


def check_only_kill(old, new, flag):
    """Refuse any change other than setting killswitch_<flag> to true."""
    key = KILL_PREFIX + flag
    old_params = dict(old.get("parameters", {}))
    new_params = dict(new.get("parameters", {}))
    old_params.pop(key, None)
    killed = new_params.pop(key, {})
    rest = lambda t: {k: v for k, v in t.items() if k not in ("parameters", "version")}
    if (
        killed.get("defaultValue") != {"value": "true"}
        or killed.get("conditionalValues")
        or old_params != new_params
        or rest(old) != rest(new)
    ):
        raise ValueError(f"refusing Remote Config change beyond {key}=true")
