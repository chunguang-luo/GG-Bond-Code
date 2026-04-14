"""CLI entry point with fast-path short-circuits."""

import sys
from datetime import date

from gg_bond_code import __version__


def main() -> None:
    """Bootstrap entry — mirrors cli.tsx fast-path waterfall."""
    args = sys.argv[1:]

    # Fast-path: --version (zero-import return)
    if len(args) == 1 and args[0] in ("--version", "-v", "-V"):
        print(f"{__version__} (GG Bond Code)")
        return

    # Normal path: delegate to main
    from gg_bond_code.main import cli

    cli()


if __name__ == "__main__":
    main()
