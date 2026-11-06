from __future__ import annotations

from modules.common import find_episode, load_manifest


def main() -> int:
    rows = load_manifest()
    cases = {
        "EP57": "EP57",
        "57": "EP57",
        "EP670": "EP670",
        "670": "EP670",
        "EP579": "EP579",
    }
    for query, expected in cases.items():
        actual = find_episode(rows, query).get("episode_id")
        if actual != expected:
            raise AssertionError(f"{query} resolved to {actual}, expected {expected}")
    print("episode matching regression passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
