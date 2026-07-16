"""Replication Layer — MF-REPL-001: Dedicated Mercury Genesis Contract V0.

Implementa il contratto di genesis e distacco di una Dedicated Mercury.
NON crea repliche reali: nessun repository, container, runtime, DB separato.
L'activation è forbidden in V0 (REPLICATION_GOVERNANCE.GENESIS_ACTIVATE=forbidden).

Flusso principale:
  Mission → Expedition → Evidence → Product Validation
  → Replication Gate → Genesis Contract → Dedicated Mercury (contratto solo)
"""
