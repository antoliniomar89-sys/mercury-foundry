"""Utilità per conversione deterministica di importi monetari a integer minor units.

Regola: 1 EUR = 100 minor units (centesimi).
Conversione via Decimal — mai via float — per evitare errori di arrotondamento.

MF-ECO-001: questo modulo è l'unico punto autorizzato di conversione float→minor.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal


# ---------------------------------------------------------------------------
# Costanti
# ---------------------------------------------------------------------------

MINOR_UNITS_PER_EUR: int = 100
"""Numero di minor units per 1 EUR (centesimi)."""


# ---------------------------------------------------------------------------
# Conversione EUR → minor units
# ---------------------------------------------------------------------------

def eur_to_minor(
    eur: float | str | Decimal,
    *,
    rounding: str = ROUND_HALF_UP,
) -> int:
    """Converte un importo EUR (float, str o Decimal) in integer minor units.

    Usa Decimal con rounding esplicito per evitare errori floating-point.
    Mai usare float aritmetica direttamente.

    Esempi:
        eur_to_minor(500.0)         → 50000
        eur_to_minor("12.34")       → 1234
        eur_to_minor(Decimal("1.005")) → 101  (ROUND_HALF_UP)
        eur_to_minor(1234.56)       → 123456

    Args:
        eur: importo in EUR (float, str o Decimal).
        rounding: modalità di rounding Decimal (default: ROUND_HALF_UP).

    Returns:
        Importo in integer minor units (centesimi).

    Raises:
        ValueError: se il valore non è convertibile.
    """
    try:
        d = Decimal(str(eur))
    except Exception as exc:
        raise ValueError(f"Impossibile convertire {eur!r} in Decimal: {exc}") from exc
    minor = int((d * MINOR_UNITS_PER_EUR).quantize(Decimal("1"), rounding=rounding))
    return minor


# ---------------------------------------------------------------------------
# Conversione minor units → EUR
# ---------------------------------------------------------------------------

def minor_to_eur(minor: int) -> Decimal:
    """Converte integer minor units in Decimal EUR.

    Ritorna Decimal per preservare precisione decimale.

    Esempi:
        minor_to_eur(50000) → Decimal("500.00")
        minor_to_eur(1234)  → Decimal("12.34")
    """
    return Decimal(minor) / Decimal(MINOR_UNITS_PER_EUR)


def minor_to_eur_float(minor: int) -> float:
    """Converte minor units in float EUR.

    **Solo per output/display/backward-compat** — mai per calcoli contabili.
    I calcoli devono usare i campi *_minor interi.
    """
    return float(minor_to_eur(minor))
