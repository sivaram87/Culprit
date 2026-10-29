"""Run: python test_detect.py"""
from detect import check_only_kill, detect, is_killed, kill_template

FLAGS = {"checkout": {"control": "off"}}


def row(variant, users, crashed, version="5.2", flag="checkout"):
    return dict(flag=flag, variant=variant, version=version, users=users, crashed_users=crashed)


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
    # 40 vs 15 crashes in 10k users: p ~ 0.0004, passes alone, fails across 20 flags.
    marginal = [row("off", 10_000, 15), row("on", 10_000, 40)]
    assert len(detect(marginal, FLAGS)) == 1
    flags = dict(FLAGS)
    for i in range(19):
        flags[f"f{i}"] = {"control": "off"}
        marginal += [row("off", 10_000, 15, flag=f"f{i}"), row("on", 10_000, 15, flag=f"f{i}")]
    assert detect(marginal, flags) == []


def test_never_returns_control():
    rows = [row("off", 10_000, 500), row("on", 10_000, 10), row("b", 10_000, 600)]
    assert {f.variant for f in detect(rows, FLAGS)} <= {"on", "b"}


def test_crashes_above_users_dont_crash_detector():
    detect([row("off", 1_000, 2_000), row("on", 1_000, 5_000)], FLAGS)


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
