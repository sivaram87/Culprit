"""Kill a flag in Statsig by enabling its kill-switch gate.

Setup per watched flag: gate `killswitch_<flag>` with an "Everyone, 100% pass" rule,
then disabled. Disabled gates return false; enabling it sends everyone to control.
Env: STATSIG_CONSOLE_KEY (Console API key, from Secret Manager).
"""
import os

import requests

import detect

API = "https://statsigapi.net/console/v1/gates/"


def kill(flag):
    gate = detect.kill_id(flag)
    headers = {"STATSIG-API-KEY": os.environ["STATSIG_CONSOLE_KEY"]}
    r = requests.get(API + gate, headers=headers, timeout=30)
    r.raise_for_status()  # 404 = kill-switch gate not set up: fail loudly
    if r.json()["data"]["isEnabled"]:
        return "already_killed"
    requests.put(API + gate + "/enable", headers=headers, timeout=30).raise_for_status()
    return "killed"
