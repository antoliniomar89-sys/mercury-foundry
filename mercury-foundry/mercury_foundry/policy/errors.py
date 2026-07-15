"""Errori del motore di enforcement dei vincoli letterali (`literal_constraints`).

Distinti dagli errori del provider AI (`ai/errors.py`): qui il provider ha
risposto con successo (la chiamata reale è avvenuta ed è registrabile in
`provider_calls`), ma la sua proposta diverge da un literal_constraint e non
è correggibile in modo sicuro e deterministico — quindi si blocca il task
invece di scrivere una patch non conforme.
"""

from __future__ import annotations


class LiteralConstraintViolationError(RuntimeError):
    """La proposta del provider diverge da un literal_constraint e la
    correzione deterministica non è possibile (manca percorso o contenuto
    completi): blocco fail-closed prima di qualunque scrittura in sandbox."""
