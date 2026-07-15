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


class BuildIncompleteError(RuntimeError):
    """La `PatchProposal` (già eventualmente corretta dall'enforcement dei
    literal_constraints) non è completa rispetto a ciò che il goal richiede
    — es. manca uno o più `required_files`, o la proposta non contiene
    alcun file da scrivere.

    Distinta da `LiteralConstraintViolationError`: qui il contenuto proposto
    non diverge da un vincolo letterale conosciuto, semplicemente la BUILD
    non ha prodotto tutto ciò che serve per poter eseguire un TEST
    significativo. Bloccata PRIMA di qualunque scrittura in sandbox e PRIMA
    che TEST possa partire — non consuma un tentativo di FIX/retry
    automatico, richiede intervento umano sul piano/provider."""


class TargetConflictError(RuntimeError):
    """Il target_project reale è cambiato rispetto allo snapshot iniziale
    registrato quando lo staging della candidate è stato creato.

    Promuovere adesso rischierebbe di sovrascrivere silenziosamente un
    cambiamento intercorso nel target dopo la creazione della candidate.
    Blocco fail-closed: nessuna scrittura sul target, la candidate resta
    `pending_review` (o viene marcata `conflict`), serve una nuova candidate
    o una decisione umana esplicita su come procedere."""


class CandidateIntegrityError(RuntimeError):
    """Lo staging della candidate NON è più, byte per byte, nello stato
    registrato quando la candidate è stata creata (MF-FIX-005, gap 1): file
    extra, file mancanti, o contenuto/dimensione cambiati rispetto al
    manifest salvato al momento della creazione.

    Blocco fail-closed PRIMA di qualunque scrittura sul target: nessuna
    promozione avviene, la candidate resta `pending_review`, lo staging
    NON viene eliminato (resta disponibile per la diagnosi), e l'evento è
    registrato per intero in audit log."""


class LegacyCandidateNotPromotableError(RuntimeError):
    """Una candidate `pending_review` manca di una o più garanzie introdotte
    da MF-FIX-004/MF-FIX-005 (staging_root, target_snapshot_hash, target_root
    registrato, uno staging_manifest completo, o un diff manifest valido) e
    perciò NON può essere approvata tramite una semplice transazione DB
    (MF-FIX-006, bypass bloccante rimosso).

    Non esiste alcuna promozione filesystem sicura per una candidate del
    genere: senza staging_manifest non c'è nulla da riverificare byte per
    byte, senza staging_root/target_root non c'è nulla da promuovere in modo
    atomico. Blocco fail-closed PRIMA di qualunque scrittura: target
    invariato, nessuna decisione approve creata, nessun backup creato,
    evento registrato in audit. Queste candidate restano `pending_review`
    per sempre a meno di un rifiuto manuale esplicito (`reject_candidate`) —
    non vengono mai migrate automaticamente al nuovo formato né promosse
    retroattivamente da file già presenti nel target."""


class CandidateRecoveryRequiredError(RuntimeError):
    """Un tentativo di approvazione ha promosso lo staging sul target reale,
    ma un passo successivo (scrittura DB/audit) è fallito E anche il
    ripristino automatico del target dal backup è fallito.

    Il sistema non dichiara un successo silenzioso né un semplice fallimento
    riprovabile: la candidate viene marcata `recovery_required`, backup e
    staging vengono PRESERVATI (nessuna pulizia automatica) per permettere
    una diagnosi/ripristino manuale. Nessun retry automatico: richiede
    intervento umano esplicito."""
