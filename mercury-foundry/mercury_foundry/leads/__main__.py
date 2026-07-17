"""Entrypoint per `python -m mercury_foundry.leads`."""
import sys

from mercury_foundry.leads.cli import main

sys.exit(main())
