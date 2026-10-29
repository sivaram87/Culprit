"""Run: python test_adapters.py   (no network, no cloud libraries needed)"""
import os
import sys
import types

# Stub third-party modules so the adapters and main import without them installed.
for name in ["requests", "google", "google.auth", "google.auth.transport",
             "google.auth.transport.requests", "google.cloud", "google.cloud.bigquery"]:
    sys.modules.setdefault(name, types.ModuleType(name))
sys.modules["google.auth.transport.requests"].AuthorizedSession = None
sys.modules["google.cloud"].bigquery = sys.modules["google.cloud.bigquery"]

import detect  # noqa: E402
import kill_statsig  # noqa: E402
import main  # noqa: E402
import source_sentry  # noqa: E402

KEY = ("checkout", "on", "app@5.2")


def test_merge_fills_zero_for_signals_without_events():
    rows = source_sentry.merge({KEY: 1000}, {"FATAL": {KEY: 12}})
    by_signal = {r["signal"]: r["crashed_users"] for r in rows}
    assert by_signal == {"FATAL": 12, "NON_FATAL": 0, "ANR": 0}
    assert all(r["users"] == 1000 for r in rows)


def test_merge_drops_crashes_without_users():
    rows = source_sentry.merge({}, {"FATAL": {KEY: 12}})
    assert rows == []


def test_parse_sentry_rows():
    data = [
        {"ff_checkout": "on", "release": "app@5.2", "count_unique(user)": 7},
        {"ff_checkout": "", "release": "app@5.2", "count_unique(user)": 3},  # untagged: skipped
    ]
    assert source_sentry.parse(data, "checkout") == {KEY: 7}


def test_sentry_rows_feed_the_detector():
    users = {("checkout", "off", "app@5.2"): 10_000, KEY: 10_000}
    crashes = {"ANR": {("checkout", "off", "app@5.2"): 2, KEY: 90}}
    [f] = detect.detect(source_sentry.merge(users, crashes), {"checkout": {"control": "off"}})
    assert (f.variant, f.signal) == ("on", "ANR")


def test_sentry_fetch_follows_pages():
    os.environ.update(SENTRY_ORG="acme", SENTRY_PROJECT_ID="42", SENTRY_AUTH_TOKEN="t")
    pages = [
        ([{"n": 1}], {"next": {"url": "https://sentry.io/page2", "results": "true"}}),
        ([{"n": 2}], {"next": {"url": "https://sentry.io/page3", "results": "false"}}),
    ]
    urls = []

    def get(url, **kw):
        urls.append(url)
        data, links = pages[len(urls) - 1]
        r = FakeResponse({"data": data})
        r.links = links
        return r

    source_sentry.requests = types.SimpleNamespace(get=get)
    from datetime import datetime
    rows = source_sentry.fetch("errors", "", "ff_x", datetime.now(), datetime.now())
    assert rows == [{"n": 1}, {"n": 2}] and urls[1] == "https://sentry.io/page2"


def test_kill_id_rejects_odd_names():
    assert detect.kill_id("new_checkout") == "killswitch_new_checkout"
    for bad in ["../x", "a/b", "A B", "", "x?y=1"]:
        try:
            detect.kill_id(bad)
        except ValueError:
            continue
        raise AssertionError(f"accepted {bad!r}")


class FakeResponse:
    def __init__(self, body=None):
        self.body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self.body


def fake_statsig(enabled):
    calls = []
    req = types.SimpleNamespace(
        get=lambda url, **kw: calls.append(("GET", url)) or FakeResponse({"data": {"isEnabled": enabled}}),
        put=lambda url, **kw: calls.append(("PUT", url)) or FakeResponse(),
    )
    return req, calls


def test_statsig_kill_enables_only_the_killswitch_gate():
    os.environ["STATSIG_CONSOLE_KEY"] = "test"
    kill_statsig.requests, calls = fake_statsig(enabled=False)
    assert kill_statsig.kill("new_checkout") == "killed"
    writes = [url for method, url in calls if method == "PUT"]
    assert writes == ["https://statsigapi.net/console/v1/gates/killswitch_new_checkout/enable"]


def test_statsig_kill_reports_already_killed():
    kill_statsig.requests, calls = fake_statsig(enabled=True)
    assert kill_statsig.kill("new_checkout") == "already_killed"
    assert all(method == "GET" for method, _ in calls)


def test_dispatch_all_four_combinations():
    main.counts_bigquery = lambda bq, end: "bigquery"
    source_sentry.counts = lambda flags, start, end: "sentry"
    main.kill_remote_config = lambda flag: "firebase"
    kill_statsig.kill = lambda flag: "statsig"
    for source in ["crashlytics", "sentry"]:
        for tool in ["firebase", "statsig"]:
            os.environ.update(SIGNAL_SOURCE=source, FLAG_TOOL=tool)
            got = (main.counts(None, {}, main.dt.datetime.now()), main.kill("x"))
            assert got == ({"crashlytics": "bigquery", "sentry": "sentry"}[source], tool), got
    os.environ["FLAG_TOOL"] = "launchdarkly"
    try:
        main.kill("x")
    except ValueError:
        return
    raise AssertionError("unknown FLAG_TOOL accepted")


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
