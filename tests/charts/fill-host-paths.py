#!/usr/bin/env python3
"""Print a values file that gives every empty hostPath in a chart's values.yaml a
placeholder, so the chart renders without its environment overlay.

Apps leave hostPath empty on purpose: the real path is per environment. The schema
still requires a string, so without this most charts cannot render in CI at all.
"""
import json, sys
import yaml


def fill(node, path):
    if isinstance(node, dict):
        out = {}
        for key, value in node.items():
            if key == "hostPath" and value is None:
                out[key] = "/ci/" + "/".join(path)
            else:
                sub = fill(value, path + [str(key)])
                if sub is not None:
                    out[key] = sub
        return out or None
    return None


values = yaml.safe_load(open(sys.argv[1])) or {}
json.dump(fill(values, []) or {}, sys.stdout)
