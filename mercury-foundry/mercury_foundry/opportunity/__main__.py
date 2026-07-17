"""Entrypoint per `python -m mercury_foundry.opportunity`."""
import sys

from mercury_foundry.opportunity.cli import main

sys.exit(main())
