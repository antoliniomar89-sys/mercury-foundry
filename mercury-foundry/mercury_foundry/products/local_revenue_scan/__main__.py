"""Entrypoint per `python -m mercury_foundry.products.local_revenue_scan`."""
import sys

from mercury_foundry.products.local_revenue_scan.cli import main

sys.exit(main())
