"""Inspect a complete model through the bundled, validating C reader."""
import argparse
import json
import sys

from . import Model, __version__


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("model", help="OptBin v2 or legacy BQP v1 file")
    parser.add_argument("--limit", type=int, default=0, metavar="BYTES",
                        help="decoded-array byte limit; 0 is unlimited (default)")
    args = parser.parse_args()
    if args.limit < 0 or args.limit >= 1 << 64:
        parser.error("--limit must be between 0 and 2^64-1")
    try:
        with Model(args.model, decoded_limit=args.limit) as model:
            print(json.dumps({**model.info, "validation": "full-model"}))
    except (OSError, RuntimeError, ValueError) as error:
        print(f"optbin-info: {error}", file=sys.stderr)
        return 1
    return 0
