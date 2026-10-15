"""Self-contained unit tests for search_space.SearchSpace (P1.1).

Run:  python test_search_space.py
The module under test has no dependency on config.ini, so no environment
setup is required.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from search_space import SearchSpace  # noqa: E402


def make_metadata():
    return {
        "buffer_pool": {
            "type": "integer",
            "min_value": 1,
            "max_value": 64,
            "step": 1,
            "unit": "GB",
        },
        "threads": {
            "type": "integer",
            "min_value": 1,
            "max_value": 128,
            "step": 1,
        },
        "log_size": {
            "type": "integer",
            "min_value": 16,
            "max_value": 4096,
            "step": 16,
            "unit": "MB",
        },
        "engine": {
            "type": "enum",
            "enum_values": ["InnoDB", "MyISAM", "RocksDB"],
        },
        "flag": {"type": "boolean"},
    }


def test_shrink_around_center():
    ss = SearchSpace(make_metadata())
    report = ss.shrink(center={"buffer_pool": 32}, ratio=0.5)
    meta = ss.metadata()
    # buffer_pool 1..64 centred at 32, ratio 0.5 -> about 16.5..48
    assert meta["buffer_pool"]["max_value"] == 48
    assert 16 <= meta["buffer_pool"]["min_value"] <= 17
    # centre missing -> knob skipped rather than guessing
    assert "threads" in report["skipped"]
    # enum / boolean knobs are never rescaled
    assert "engine" in report["skipped"]
    assert "flag" in report["skipped"]
    assert "buffer_pool" in report["changed"]


def test_shrink_stays_inside_original():
    ss = SearchSpace(make_metadata())
    ss.shrink(center={"buffer_pool": 40}, ratio=0.5)   # -> about 20.5..52
    ss.shrink(center={"buffer_pool": 20}, ratio=0.5)   # -> 20..36 (clamped low)
    meta = ss.metadata()
    assert meta["buffer_pool"]["min_value"] == 20
    assert meta["buffer_pool"]["max_value"] == 36
    assert 1 <= meta["buffer_pool"]["min_value"]
    assert meta["buffer_pool"]["max_value"] <= 64


def test_shrink_center_outside_range_is_clamped():
    ss = SearchSpace(make_metadata())
    ss.shrink(center={"buffer_pool": 1000}, ratio=0.5)  # clamped down to hi
    meta = ss.metadata()
    assert meta["buffer_pool"]["max_value"] == 64
    assert 32 <= meta["buffer_pool"]["min_value"] <= 33
    assert 1 <= meta["buffer_pool"]["min_value"]


def test_shrink_does_not_touch_original():
    ss = SearchSpace(make_metadata())
    before = ss.original()
    ss.shrink(center={"buffer_pool": 32}, ratio=0.5)
    assert ss.original() == before
    assert ss.metadata() != before


def test_expand_returns_toward_original():
    ss = SearchSpace(make_metadata())
    ss.shrink(center={"buffer_pool": 32}, ratio=0.5)   # 16..48
    ss.expand(ratio=0.5)
    meta = ss.metadata()
    assert meta["buffer_pool"]["min_value"] < 16
    assert meta["buffer_pool"]["max_value"] > 48
    assert meta["buffer_pool"]["min_value"] >= 1
    assert meta["buffer_pool"]["max_value"] <= 64


def test_expand_cannot_exceed_original():
    ss = SearchSpace(make_metadata())
    ss.expand(ratio=0.99)
    meta = ss.metadata()
    assert meta["buffer_pool"]["min_value"] == 1
    assert meta["buffer_pool"]["max_value"] == 64


def test_reset_restores_original():
    ss = SearchSpace(make_metadata())
    ss.shrink(center={"buffer_pool": 32}, ratio=0.5)
    ss.reset()
    assert ss.metadata()["buffer_pool"] == make_metadata()["buffer_pool"]


def test_integer_bounds_and_step_preserved():
    ss = SearchSpace(make_metadata())
    ss.shrink(center={"log_size": 2048}, ratio=0.5)
    meta = ss.metadata()
    assert isinstance(meta["log_size"]["min_value"], int)
    assert isinstance(meta["log_size"]["max_value"], int)
    assert meta["log_size"]["min_value"] == 1032
    assert meta["log_size"]["max_value"] == 3072
    assert meta["log_size"]["step"] == 16  # untouched


def test_serialization_roundtrip():
    ss = SearchSpace(make_metadata())
    ss.shrink(center={"buffer_pool": 32}, ratio=0.5)
    restored = SearchSpace.from_dict(json.loads(json.dumps(ss.to_dict())))
    assert restored.metadata() == ss.metadata()
    assert restored.original() == ss.original()


def test_compact_json_shape():
    compact = json.loads(SearchSpace(make_metadata()).compact_json())
    assert set(compact.keys()) == set(make_metadata().keys())
    # only the compact fields survive, and min/max are present
    assert set(compact["buffer_pool"].keys()) == {"min_value", "max_value", "step", "type"}
    assert "unit" not in compact["buffer_pool"]


def test_invalid_ratio_raises():
    ss = SearchSpace(make_metadata())
    for bad in (0.0, 1.0, -0.1, 2.0):
        try:
            ss.shrink(center={"buffer_pool": 32}, ratio=bad)
            raise AssertionError("shrink accepted ratio=%r" % bad)
        except ValueError:
            pass
        try:
            ss.expand(ratio=bad)
            raise AssertionError("expand accepted ratio=%r" % bad)
        except ValueError:
            pass
    # a rejected ratio must not have mutated anything
    assert ss.metadata() == make_metadata()


def test_from_dict_invalid_raises():
    for bad in (None, {}, {"original": {}}):
        try:
            SearchSpace.from_dict(bad)
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError for payload=%r" % (bad,))


def test_type_error_for_non_mapping():
    try:
        SearchSpace(["not", "a", "mapping"])
    except TypeError:
        pass
    else:
        raise AssertionError("expected TypeError")


if __name__ == "__main__":
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_")]
    for name, fn in tests:
        fn()
        print("PASS", name)
    print("ALL SEARCH-SPACE TESTS PASSED (%d)" % len(tests))
