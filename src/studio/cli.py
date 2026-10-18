"""Operational entry point, preserving the existing module CLIs."""
from __future__ import annotations

import argparse

from . import backup, doctor


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='X-Studio operations')
    parser.add_argument('command', choices=['doctor', 'backup'])
    args, rest = parser.parse_known_args(argv)
    return doctor.main(rest) if args.command == 'doctor' else backup.main(rest)


if __name__ == '__main__':  # pragma: no cover
    raise SystemExit(main())
