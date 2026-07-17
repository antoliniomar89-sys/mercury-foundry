"""Entrypoint per python -m mercury_foundry.lead_enrichment."""
import sys

from mercury_foundry.lead_enrichment.cli import main

sys.exit(main())
