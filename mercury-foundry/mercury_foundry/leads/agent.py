"""Lead Agent — core logic.

Ciclo: OPPORTUNITY RESULT → SEARCH → QUALIFY → SAVE 10 LEADS → NEXT ACTION

Dipendenze iniettabili per i test:
- fetch_fn: (opportunity: dict) -> dict[str, list[dict]]
- generate_fn: (system_prompt: str, user_prompt: str) -> dict
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable

from mercury_foundry.leads.models import (
    Lead,
    LeadPriority,
    LeadResult,
    LeadResultStatus,
    LeadStatus,
)
from mercury_foundry.leads.prompts import SYSTEM_PROMPT, build_qualification_prompt
from mercury_foundry.leads.search import fetch_leads_for_opportunity

FetchFn = Callable[[dict], dict[str, list[dict]]]
GenerateFn = Callable[[str, str], dict]

_MIN_QUALIFIED = 5
_MAX_LEADS = 10


# ------------------------------------------------------------------
# Costruttore generate_fn reale (identico al pattern Revenue Scan)
# ------------------------------------------------------------------

def _build_real_generate_fn() -> GenerateFn:
    """Ritorna una generate_fn che chiama il provider AI configurato."""
    from mercury_foundry.ai.provider_config import ProviderConfigError, load_real_provider_config
    import openai

    try:
        config = load_real_provider_config()
    except ProviderConfigError as exc:
        raise RuntimeError(
            f"Provider AI non configurato per Lead Agent: {exc}\n"
            "Imposta MERCURY_AI_PROVIDER=openai_compatible e le variabili associate.\n"
            "Nei test usa LeadAgent(generate_fn=<fixture_callable>)."
        ) from exc

    client_kwargs: dict[str, Any] = {"api_key": config.api_key}
    if config.base_url:
        client_kwargs["base_url"] = config.base_url
    client = openai.OpenAI(**client_kwargs)

    def generate(system_prompt: str, user_prompt: str) -> dict:
        response = client.chat.completions.create(
            model=config.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
            timeout=config.timeout_seconds,
        )
        raw = response.choices[0].message.content
        return json.loads(raw)

    return generate


# ------------------------------------------------------------------
# Parsing e deduplicazione
# ------------------------------------------------------------------

def _normalise_url(url: str) -> str:
    """Normalizza un URL per il confronto di deduplicazione."""
    return (
        url.lower()
        .strip()
        .rstrip("/")
        .removeprefix("https://")
        .removeprefix("http://")
        .removeprefix("www.")
    )


def _parse_leads(raw_leads: list[dict]) -> tuple[list[Lead], list[dict]]:
    """Converte raw lead AI → oggetti Lead. Applica regole di qualificazione.

    Ritorna (leads_validi, scartati_per_regola).
    - Un lead senza source_url → status forzato a REJECTED.
    - Un lead senza evidence → status forzato a REJECTED.
    - Max _MAX_LEADS lead totali.
    """
    parsed: list[Lead] = []
    discarded: list[dict] = []

    seen_websites: set[str] = set()

    for raw in raw_leads[:_MAX_LEADS + 5]:  # legge un po' di più per gestire duplicati
        if len(parsed) >= _MAX_LEADS:
            discarded.append({**raw, "_discard_reason": "limite_massimo_raggiunto"})
            continue

        source_url = str(raw.get("source_url", "")).strip()
        evidence = str(raw.get("evidence", "")).strip()
        name = str(raw.get("name", "")).strip()
        website = str(raw.get("website", "")).strip() or source_url
        status_raw = str(raw.get("status", "NEW")).upper()

        # Regola: source_url obbligatorio
        if not source_url:
            discarded.append({**raw, "_discard_reason": "source_url_mancante"})
            continue

        # Regola: evidence obbligatoria per QUALIFIED
        if not evidence and status_raw == "QUALIFIED":
            status_raw = "REJECTED"
            raw = {**raw, "rejection_reason": "Nessuna evidenza disponibile."}

        # Deduplicazione per website normalizzato
        norm_website = _normalise_url(website)
        if norm_website and norm_website in seen_websites:
            discarded.append({**raw, "_discard_reason": "duplicato"})
            continue
        if norm_website:
            seen_websites.add(norm_website)

        try:
            lead = Lead(
                name=name or source_url,
                segment=str(raw.get("segment", "")).strip(),
                website=website,
                public_contact=str(raw.get("public_contact", website)).strip(),
                contact_type=str(raw.get("contact_type", "github_profile")).strip(),
                location=str(raw.get("location", "")).strip(),
                fit_reason=str(raw.get("fit_reason", "")).strip(),
                evidence=evidence,
                source_url=source_url,
                priority=str(raw.get("priority", "LOW")),
                status=status_raw,
                rejection_reason=str(raw.get("rejection_reason", "")).strip(),
            )
            parsed.append(lead)
        except Exception:
            discarded.append({**raw, "_discard_reason": "parse_error"})

    return parsed, discarded


def _count_duplicates(all_raw: list[dict], kept: list[Lead]) -> int:
    return max(0, len(all_raw) - len(kept))


# ------------------------------------------------------------------
# Agent
# ------------------------------------------------------------------

class LeadAgent:
    """Agente minimo per trovare e qualificare lead da un OpportunityResult.

    Uso in produzione:
        agent = LeadAgent.with_real_provider()
        result = agent.run(opportunity_data)

    Uso nei test:
        agent = LeadAgent(fetch_fn=fake_fetch, generate_fn=fake_gen)
        result = agent.run(opportunity_data)
    """

    def __init__(
        self,
        fetch_fn: FetchFn | None = None,
        generate_fn: GenerateFn | None = None,
    ) -> None:
        self._fetch_fn: FetchFn = fetch_fn if fetch_fn is not None else fetch_leads_for_opportunity
        self._generate_fn: GenerateFn | None = generate_fn
        self._last_result: LeadResult | None = None

    @classmethod
    def with_real_provider(cls) -> "LeadAgent":
        """Factory per l'uso in produzione con provider AI reale."""
        return cls(generate_fn=_build_real_generate_fn())

    @property
    def last_result(self) -> LeadResult | None:
        return self._last_result

    def run(self, opportunity: dict) -> LeadResult:
        """Esegue il ciclo completo per un'opportunità data.

        opportunity: dict compatibile con OpportunityResult.to_dict()
        Ritorna sempre un LeadResult con status e next_action popolati.
        """
        timestamp = datetime.now(timezone.utc).isoformat()

        # Valida input minimo
        required = ["problem", "target_customer", "proposed_offer"]
        if not any(opportunity.get(k) for k in required):
            return self._save(LeadResult(
                status=LeadResultStatus.BLOCKED_INVALID_OPPORTUNITY,
                opportunity_summary=self._summarise(opportunity),
                block_reason=(
                    "OpportunityResult mancante di campi obbligatori: "
                    f"{required}. Fornire un opportunity COMPLETED valido."
                ),
                next_action="Eseguire prima l'Opportunity Agent e salvare il risultato.",
                timestamp=timestamp,
            ))

        opp_status = str(opportunity.get("status", "")).upper()
        if opp_status and opp_status != "COMPLETED":
            return self._save(LeadResult(
                status=LeadResultStatus.BLOCKED_INVALID_OPPORTUNITY,
                opportunity_summary=self._summarise(opportunity),
                block_reason=f"OpportunityResult ha status '{opp_status}', atteso 'COMPLETED'.",
                next_action="Usare un OpportunityResult con status COMPLETED.",
                timestamp=timestamp,
            ))

        # ── Step 1: fetch candidati ──────────────────────────────────────────
        try:
            candidates = self._fetch_fn(opportunity)
        except Exception as exc:
            return self._save(LeadResult(
                status=LeadResultStatus.BLOCKED_NO_WEB_ACCESS,
                opportunity_summary=self._summarise(opportunity),
                block_reason=f"Fetch candidati fallito: {exc}",
                next_action=(
                    "Verificare connettività verso api.github.com e "
                    "hn.algolia.com, poi riprovare."
                ),
                timestamp=timestamp,
            ))

        if not candidates:
            return self._save(LeadResult(
                status=LeadResultStatus.BLOCKED_NO_WEB_ACCESS,
                opportunity_summary=self._summarise(opportunity),
                block_reason=(
                    "Nessuna fonte raggiungibile. Tutte le chiamate HTTP hanno fallito. "
                    "Fonti tentate: api.github.com, hn.algolia.com."
                ),
                next_action=(
                    "Abilitare accesso HTTP in uscita verso api.github.com, "
                    "poi riprovare."
                ),
                timestamp=timestamp,
            ))

        sources_used = list(candidates.keys())
        all_candidate_count = sum(len(v) for v in candidates.values())
        queries_used = list({
            c.get("search_query", "")
            for profiles in candidates.values()
            for c in profiles
        })

        # ── Step 2: verifica/costruzione generate_fn ─────────────────────────
        if self._generate_fn is None:
            try:
                self._generate_fn = _build_real_generate_fn()
            except RuntimeError as exc:
                return self._save(LeadResult(
                    status=LeadResultStatus.FAILED,
                    opportunity_summary=self._summarise(opportunity),
                    block_reason=str(exc),
                    next_action=(
                        "Configurare MERCURY_AI_PROVIDER=openai_compatible, "
                        "MERCURY_AI_API_KEY e MERCURY_AI_MODEL, poi riprovare."
                    ),
                    sources_used=sources_used,
                    search_queries=queries_used,
                    timestamp=timestamp,
                ))

        # ── Step 3: qualificazione AI ────────────────────────────────────────
        try:
            user_prompt = build_qualification_prompt(opportunity, candidates)
            raw = self._generate_fn(SYSTEM_PROMPT, user_prompt)
        except Exception as exc:
            return self._save(LeadResult(
                status=LeadResultStatus.FAILED,
                opportunity_summary=self._summarise(opportunity),
                block_reason=f"Errore chiamata AI: {exc}",
                next_action="Verificare provider AI e riprovare.",
                sources_used=sources_used,
                search_queries=queries_used,
                timestamp=timestamp,
            ))

        # ── Step 4: parsing, deduplicazione, applicazione regole ─────────────
        raw_leads = raw.get("leads", [])
        if not isinstance(raw_leads, list):
            raw_leads = []

        leads, discarded = _parse_leads(raw_leads)
        duplicates_discarded = sum(
            1 for d in discarded if d.get("_discard_reason") == "duplicato"
        )

        next_action = str(raw.get("next_action", "")).strip()
        if not next_action:
            next_action = (
                "Preparare un messaggio personalizzato per ciascun lead QUALIFIED "
                "evidenziando il problema specifico identificato nel loro profilo."
            )

        qualified = [l for l in leads if l.status == LeadStatus.QUALIFIED]

        # ── Step 5: verifica soglia minima ───────────────────────────────────
        if len(qualified) < _MIN_QUALIFIED:
            return self._save(LeadResult(
                status=LeadResultStatus.BLOCKED_INSUFFICIENT_LEADS,
                opportunity_summary=self._summarise(opportunity),
                leads=leads,
                search_queries=queries_used,
                sources_used=sources_used,
                duplicates_discarded=duplicates_discarded,
                block_reason=(
                    f"Trovati solo {len(qualified)} lead qualificati "
                    f"(minimo richiesto: {_MIN_QUALIFIED}). "
                    f"Candidati totali esaminati: {all_candidate_count}."
                ),
                next_action=(
                    "Ampliare le query di ricerca o aggiungere nuove fonti "
                    "(es. LinkedIn, Upwork, directory di settore), poi riprovare."
                ),
                discarded_leads=[{k: v for k, v in d.items() if not k.startswith("_")} for d in discarded],
                timestamp=timestamp,
            ))

        return self._save(LeadResult(
            status=LeadResultStatus.COMPLETED,
            opportunity_summary=self._summarise(opportunity),
            leads=leads,
            search_queries=queries_used,
            sources_used=sources_used,
            duplicates_discarded=duplicates_discarded,
            next_action=next_action,
            discarded_leads=[{k: v for k, v in d.items() if not k.startswith("_")} for d in discarded],
            timestamp=timestamp,
        ))

    def _save(self, result: LeadResult) -> LeadResult:
        self._last_result = result
        return result

    @staticmethod
    def _summarise(opportunity: dict) -> dict:
        """Estrae i campi chiave dell'opportunity per il report."""
        keys = [
            "status", "problem", "target_customer", "proposed_offer",
            "delivery_format", "initial_price", "next_action",
        ]
        return {k: opportunity.get(k) for k in keys}
