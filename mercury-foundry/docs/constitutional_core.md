# Constitutional Core — MF-CONST-001

## Scopo

Il Constitutional Core è il riferimento condiviso di principi, vincoli e governance
per organi, agenti e future Business Cell di Mercury. È un componente di infrastruttura
puramente deterministico: valuta condizioni verificabili sui dati strutturati disponibili,
non genera output via LLM e non applica euristiche.

---

## Differenza tra Memory, Knowledge e Constitution

| Concetto | Dove vive | Chi lo aggiorna | Scopo |
|----------|-----------|-----------------|-------|
| **Memory** | `.agents/memory/` | Agent | Lezioni operative, decisioni passate, contesto sessione |
| **Knowledge** | Codebase, docs, data | Sviluppatori, agenti | Dati, algoritmi, configurazioni di dominio |
| **Constitution** | `constitution_v1.json` | Solo approvazione umana (CONST-007) | Principi fondanti non modificabili autonomamente |

La Constitution è l'unica delle tre che non può essere modificata da un agente autonomo.
Ogni modifica costituzionale richiede approvazione umana esplicita (CONST-007).

---

## Modalità operative

### `disabled`

```
MERCURY_CONSTITUTIONAL_CORE_MODE=disabled
```

Il Constitutional Core non viene invocato. Nessun overhead, nessun audit.
Usare solo in ambienti completamente isolati o in test che verificano il comportamento
pre-CONST-001.

### `shadow` (default)

```
MERCURY_CONSTITUTIONAL_CORE_MODE=shadow
```

Il validator viene invocato per ogni decisione rilevante ma:
- **non blocca mai** il flusso operativo, indipendentemente dall'esito della validazione;
- registra `constitution.validation.completed` nell'audit log;
- registra `constitution.violation.detected` se un principio è violato;
- registra `constitution.configuration.invalid` se il file è corrotto (e poi ritorna None);
- qualsiasi eccezione tecnica è silenziata.

Usare per osservare il comportamento del sistema prima di attivare enforce.

### `enforce` (predisposto, non abilitato in V0)

```
MERCURY_CONSTITUTIONAL_CORE_MODE=enforce
```

Come shadow, ma se `enforcement_action = DENY`, solleva `ConstitutionalViolationError`.
In V0 nessun principio ha `enforcement = blocking`, quindi enforce è funzionalmente
equivalente a shadow. La struttura è pronta per l'evoluzione futura.

---

## Come aggiungere un principio

### 1. Definire il principio nel JSON

Aggiungere una voce a `mercury_foundry/constitutional/constitution_v1.json`:

```json
{
  "principle_id": "CONST-008",
  "title": "Titolo del principio",
  "description": "Descrizione completa e machine-readable del vincolo.",
  "level": "operational",
  "status": "shadow",
  "enforcement": "advisory",
  "applies_to": ["all"],
  "required_evidence": [],
  "source_refs": [],
  "created_at": "2026-07-16T00:00:00Z",
  "updated_at": "2026-07-16T00:00:00Z"
}
```

**Livelli ammessi:** `immutable` | `constitutional` | `operational` | `local`
**Stati ammessi:** `candidate` | `shadow` | `active` | `deprecated` | `rejected`
**Enforcement ammesso:** `advisory` | `audit_only` | `blocking`

> ⚠️ Non usare `blocking` in V0. La struttura è predisposta ma non ancora attivata.

Avviare sempre un nuovo principio in stato `shadow` con `enforcement: advisory` o
`audit_only`. La promozione ad `active` richiede almeno un ciclo di osservazione
in shadow mode.

### 2. Aggiungere un evaluator (opzionale)

Se il principio ha condizioni verificabili deterministicamente, creare un evaluator:

```python
# mercury_foundry/constitutional/evaluators/my_principle.py

from mercury_foundry.constitutional.evaluators.base import PrincipleEvaluator
from mercury_foundry.constitutional.models import (
    ConstitutionalPrinciple,
    ConstitutionalValidationRequest,
    PrincipleEvaluationDetail,
)

class MyPrincipleEvaluator(PrincipleEvaluator):
    @property
    def principle_id(self) -> str:
        return "CONST-008"

    def evaluate(
        self,
        principle: ConstitutionalPrinciple,
        request: ConstitutionalValidationRequest,
    ) -> PrincipleEvaluationDetail:
        # Implementazione deterministica qui.
        # Mai LLM, mai euristica, solo condizioni sui dati della request.
        ...
```

### 3. Registrare l'evaluator

In `mercury_foundry/constitutional/validator.py`, aggiungere all'interno di
`_build_evaluator_registry()`:

```python
from mercury_foundry.constitutional.evaluators.my_principle import MyPrincipleEvaluator

evaluators: list[PrincipleEvaluator] = [
    ...
    MyPrincipleEvaluator(),  # CONST-008
]
```

I principi senza evaluator vengono ignorati durante la validazione (non contano
come violazioni).

---

## Come aggiungere un evaluator

Un evaluator deve:

1. Estendere `PrincipleEvaluator` (ABC).
2. Implementare `principle_id` (proprietà stringa).
3. Implementare `evaluate(principle, request) → PrincipleEvaluationDetail`.
4. **Mai sollevare eccezioni**: i casi di dati mancanti o inapplicabilità devono
   essere espressi tramite i campi `applicable=False` e `data_missing`.
5. **Mai chiamare LLM o servizi esterni**: solo condizioni deterministiche sui
   campi di `ConstitutionalValidationRequest`.

Il campo `PrincipleEvaluationDetail.applicable` è fondamentale:
- `applicable=False` → il principio non si applica a questa richiesta, non contribuisce al risultato.
- `applicable=True, passed=True` → conforme.
- `applicable=True, passed=False` → in violazione.
- `applicable=True, passed=None` → dato mancante per la valutazione completa.

---

## Flusso di integrazione con MF-ARCH-008

```
Decision Request
  │
  ▼
authorize_organ_decision()       ← autonomy/authorization.py
  │
  ├─ Organ lookup (fail-closed)
  ├─ Mandate lookup (fail-closed)
  ├─ Evidence / risk / budget checks
  │
  ├─ maybe_validate_constitution()   ← constitutional/shadow.py  [MF-CONST-001]
  │     │
  │     ├─ disabled: ritorna None immediatamente
  │     │
  │     ├─ shadow: chiama ConstitutionalValidator
  │     │          registra audit constitution.*
  │     │          mai solleva eccezioni
  │     │
  │     └─ enforce: chiama ConstitutionalValidator
  │                 solleva ConstitutionalViolationError se DENY
  │
  ├─ _apply_authority_mode()
  └─ Audit AUTONOMY_DECISION_*
```

Gli audit `constitution.*` e `AUTONOMY_DECISION_*` sono eventi **distinti**: nessuna
duplicazione dello stesso evento.

---

## Eredità nelle future Business Cell

Il Constitutional Core è progettato come componente di infrastruttura che non dipende
da Product OS o Business Cell. La dipendenza è unidirezionale:

```
Business Cell ──► Constitutional Core
Product OS    ──► Constitutional Core
Governance    ──► Constitutional Core
Organs        ──► Constitutional Core
```

Ogni Business Cell, quando verrà incubata, erediterà automaticamente i principi
costituzionali in vigore al momento della sua attivazione. CONST-005 ("Business Cell
as Economic Unit") descrive questo principio di architettura organizzativa.

Una Business Cell può avere principi `local` specifici del suo contesto, ma non può
derogare ai principi di livello `constitutional` o `immutable`.

---

## Perché una modifica costituzionale non può essere auto-attivata

Il principio CONST-007 ("Human Approval for Constitutional Change") è l'unico
che si auto-applica: vieta autonomamente l'applicazione autonoma di modifiche
alla Costituzione stessa.

Questo crea una protezione ricorsiva: un agente che volesse eludere CONST-007
dovrebbe prima modificare CONST-007, ma questa modifica violerebbe CONST-007.
L'unica uscita è l'intervento umano esplicito fuori banda.

Tecnicamente: qualsiasi `action_type` che contenga "constitutional" con
`authority_mode = "autonomous"` viene rilevato dall'evaluator `ConstitutionalChangeProtectionEvaluator`
e classificato come violazione. In enforce mode, questo bloccherebbe l'operazione.

---

## Principi iniziali (V0)

| ID | Titolo | Livello | Stato | Enforcement |
|----|--------|---------|-------|-------------|
| CONST-001 | Evidence Before Investment | constitutional | active | audit_only |
| CONST-002 | Auditability | constitutional | active | audit_only |
| CONST-003 | Bounded Autonomy | constitutional | active | audit_only |
| CONST-004 | Reversible First | operational | active | advisory |
| CONST-005 | Business Cell as Economic Unit | constitutional | shadow | advisory |
| CONST-006 | Knowledge Classification | operational | shadow | advisory |
| CONST-007 | Human Approval for Constitutional Change | immutable | active | audit_only |
