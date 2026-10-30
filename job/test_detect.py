"""Run: python test_detect.py"""
from detect import (
    Finding, action_for, check_only_kill, concentration, describe, detect, is_killed, kill_template, msprt_p,
    os_major, p_value,
)

FLAGS = {"checkout": {"control": "off"}}


def row(variant, users, crashed, version="5.2", flag="checkout", signal="FATAL"):
    return dict(flag=flag, variant=variant, version=version, signal=signal, users=users, crashed_users=crashed)


def test_clear_regression_flagged():
    [f] = detect([row("off", 10_000, 20), row("on", 10_000, 120)], FLAGS)
    assert (f.flag, f.variant, f.version) == ("checkout", "on", "5.2")
    assert f.crash_rate > 5 * f.control_rate


def test_small_sample_ignored():
    assert detect([row("off", 100, 0), row("on", 100, 30)], FLAGS) == []


def test_equal_rates_ignored():
    assert detect([row("off", 10_000, 50), row("on", 10_000, 52)], FLAGS) == []


def test_only_bad_version_flagged():
    rows = [
        row("off", 10_000, 20, "5.1"), row("on", 10_000, 21, "5.1"),
        row("off", 10_000, 20, "5.2"), row("on", 10_000, 120, "5.2"),
    ]
    assert [f.version for f in detect(rows, FLAGS)] == ["5.2"]


def test_missing_control_skipped():
    assert detect([row("on", 10_000, 500)], FLAGS) == []


def test_unwatched_flag_ignored():
    assert detect([row("off", 10_000, 20, flag="x"), row("on", 10_000, 500, flag="x")], FLAGS) == []


def test_bonferroni_suppresses_marginal_result():
    # 46 vs 15 crashes in 10k users: p ~ 0.002, passes alone (alpha 0.01), fails across 20 flags.
    marginal = [row("off", 10_000, 15), row("on", 10_000, 46)]
    assert len(detect(marginal, FLAGS)) == 1
    flags = dict(FLAGS)
    for i in range(19):
        flags[f"f{i}"] = {"control": "off"}
        marginal += [row("off", 10_000, 15, flag=f"f{i}"), row("on", 10_000, 15, flag=f"f{i}")]
    assert detect(marginal, flags) == []


def test_msprt_never_below_fixed_sample_p():
    # The price of checking every run: an always-valid p is never smaller than a one-look p.
    for c1, n1, c0, n0 in [(120, 10_000, 20, 10_000), (46, 10_000, 15, 10_000), (30, 1_500, 3, 1_500), (12, 600, 0, 600)]:
        tau2 = ((c1 + c0) / (n1 + n0)) ** 2
        assert msprt_p(c1, n1, c0, n0, tau2) >= p_value(c1, n1, c0, n0)


def test_msprt_evidence_grows_with_data():
    tau2 = 0.004 ** 2
    small, big = msprt_p(12, 600, 1, 600, tau2), msprt_p(48, 2_400, 4, 2_400, tau2)
    assert big < small / 100
    assert msprt_p(10, 1_000, 10, 1_000, tau2) == 1.0  # no gap, no evidence


def test_never_returns_control():
    rows = [row("off", 10_000, 500), row("on", 10_000, 10), row("b", 10_000, 600)]
    assert {f.variant for f in detect(rows, FLAGS)} <= {"on", "b"}


def test_crashes_above_users_dont_crash_detector():
    detect([row("off", 1_000, 2_000), row("on", 1_000, 5_000)], FLAGS)


def test_anr_regression_flagged_separately_from_crashes():
    rows = [
        row("off", 10_000, 20), row("on", 10_000, 21),
        row("off", 10_000, 0, signal="ANR"), row("on", 10_000, 80, signal="ANR"),  # zero-ANR control
    ]
    [f] = detect(rows, FLAGS)
    assert f.signal == "ANR" and f.control_rate == 0


def test_each_signal_gets_its_own_finding():
    rows = [
        row("off", 10_000, 20), row("on", 10_000, 120),
        row("off", 10_000, 300, signal="NON_FATAL"), row("on", 10_000, 900, signal="NON_FATAL"),
    ]
    assert sorted(f.signal for f in detect(rows, FLAGS)) == ["FATAL", "NON_FATAL"]


def finding(signal):
    return Finding("checkout", "on", "5.2", signal, 10_000, 120, 0.012, 0.002, 1e-9)


def test_kill_policy():
    opted_in = {"checkout": {"control": "off", "auto_kill": True}}
    assert action_for(finding("FATAL"), opted_in, True, 0) == "kill"
    assert action_for(finding("ANR"), opted_in, True, 0) == "kill"
    assert action_for(finding("NON_FATAL"), opted_in, True, 0) == "alerted"  # never kills
    assert action_for(finding("FATAL"), opted_in, True, 1) == "kill_capped"
    assert action_for(finding("FATAL"), opted_in, False, 0) == "alerted"  # AUTO_KILL off
    assert action_for(finding("FATAL"), FLAGS, True, 0) == "alerted"  # flag not opted in


def test_os_major():
    assert os_major("Android 14.0.1") == "Android 14"
    assert os_major("iOS 17.2") == "iOS 17"
    assert os_major("iPadOS 18") == "iPadOS 18"
    assert os_major("") == "unknown" and os_major(None) == "unknown"
    assert os_major("Linux") == "Linux"


def seg(variant, dim, segment, n):
    return dict(variant=variant, dim=dim, segment=segment, crashed_users=n)


def test_concentration_finds_the_hot_segment():
    rows = [
        seg("on", "os", "Android 14.0", 50), seg("on", "os", "Android 14.1", 12), seg("on", "os", "Android 13", 38),
        seg("off", "os", "Android 14.0", 20), seg("off", "os", "Android 13", 80),
        seg("on", "device", "Samsung SM-S918B", 30), seg("on", "device", "Pixel 8", 70),
        seg("off", "device", "Samsung SM-S918B", 30), seg("off", "device", "Pixel 8", 70),
    ]
    [top] = concentration(rows, "on", "off")
    # Patch versions merged: 62 of 100 variant users on Android 14 vs 20 of 100 in control.
    assert top == dict(dim="os", segment="Android 14", variant_share=0.62, control_share=0.2)


def test_concentration_equal_mix_is_empty():
    rows = [seg("on", "os", "iOS 17", 50), seg("on", "os", "iOS 18", 50),
            seg("off", "os", "iOS 17", 5), seg("off", "os", "iOS 18", 5)]
    assert concentration(rows, "on", "off") == []


def test_concentration_with_zero_error_control():
    rows = [seg("on", "device", "Pixel 6", 40), seg("on", "device", "Pixel 8", 10)]
    segs = concentration(rows, "on", "off")  # no control errors: biggest variant shares come first
    assert [s["segment"] for s in segs] == ["Pixel 6", "Pixel 8"]
    assert all(s["control_share"] == 0.0 for s in segs)


def test_concentration_ignores_noise_when_control_has_few_errors():
    # Control has 2 errors: 35% vs 0% on one device is chance, 22 of 24 on one OS isn't.
    rows = [
        seg("on", "device", "Galaxy S23", 7), seg("on", "device", "Pixel 8", 6),
        seg("on", "device", "iPhone 13", 6), seg("on", "device", "iPhone 15", 1),
        seg("off", "device", "Pixel 8", 2),
        seg("on", "os", "Android 14", 22), seg("on", "os", "iOS 18", 2),
        seg("off", "os", "iOS 18", 2),
    ]
    segs = concentration(rows, "on", "off")
    assert [s["segment"] for s in segs] == ["Android 14"], segs


def test_describe():
    assert describe(None) == "Breakdown unavailable."
    assert describe([]) == "No OS or device stands out."
    line = describe([dict(dim="os", segment="Android 14", variant_share=0.62, control_share=0.2)])
    assert line == "Concentrated in: Android 14 (62% vs 20% control)."


TEMPLATE = {
    "version": {"versionNumber": "7"},
    "conditions": [{"name": "ios", "expression": "device.os == 'ios'"}],
    "parameters": {
        "checkout": {"defaultValue": {"value": "on"}},
        "killswitch_checkout": {"defaultValue": {"value": "false"}, "valueType": "BOOLEAN"},
    },
}


def test_kill_only_flips_killswitch_true():
    new = kill_template(TEMPLATE, "checkout")
    assert is_killed(new, "checkout") and not is_killed(TEMPLATE, "checkout")
    assert new["parameters"]["checkout"] == TEMPLATE["parameters"]["checkout"]
    assert new["conditions"] == TEMPLATE["conditions"]
    assert "version" not in new


def test_safety_check_refuses_other_changes():
    bad = [
        {**TEMPLATE, "parameters": {**TEMPLATE["parameters"], "checkout": {"defaultValue": {"value": "off"}}}},
        {**TEMPLATE, "parameters": {**TEMPLATE["parameters"], "killswitch_checkout": {"defaultValue": {"value": "false"}}}},
        {**TEMPLATE, "conditions": []},
    ]
    for new in bad:
        try:
            check_only_kill(TEMPLATE, new, "checkout")
        except ValueError:
            continue
        raise AssertionError(f"accepted unsafe change: {new}")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
