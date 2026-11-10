"""Select one PythonClient environment before building CLI setting defaults."""

import argparse
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


def add_environment_argument(parser):
    parser.add_argument(
        "--env", choices=("dev", "prod"), default=os.getenv("PYTHONCLIENT_ENV", "dev"),
        help="Load .env.dev or .env.prod (default: PYTHONCLIENT_ENV, otherwise dev).",
    )


def load_environment(argv=None, *, root=ROOT):
    """Load only the selected file; exported process settings take precedence."""
    from dotenv import load_dotenv

    arguments = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(add_help=False)
    add_environment_argument(parser)
    args, _ = parser.parse_known_args(arguments)
    # argparse does not validate a string default against choices.
    if args.env not in ("dev", "prod"):
        parser.error("PYTHONCLIENT_ENV must be dev or prod.")
    path = Path(root) / f".env.{args.env}"
    if "--help" not in arguments and "-h" not in arguments:
        if not path.is_file():
            parser.error(f"Missing {path}. Copy .env.{args.env}.example and fill in its settings.")
        load_dotenv(path, override=False, interpolate=False)
        print(f"Environment: {args.env} ({path.name})", file=sys.stderr)
    return args.env
