"""Opportunity Agent — core logic.

Riceve un mandato, recupera segnali di mercato via fetch_fn,
analizza con generate_fn (LLM), ritorna un OpportunityResult strutturato.

Entrambe le dipendenze sono iniettabili per i test:
- fetch_fn: () -> dict[str, str]   — default: web.fetch_market_signals
- generate_fn: (str, str) -> dict  — default: costruito da _build_real_generate_fn()
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable

from mercury_foundry.opportunity.models import (
    CandidateProblem,
    Evidence,
    OpportunityResult,
    OpportunityStatus,
)
from mercury_foundry.opportunity.prompts import SYSTEM_PROMPT, build_analysis_prompt
from mercury_foundry.opportunity.web import fetch_market_signals

FetchFn = Callable[[], dict[str, str]]
GenerateFn = Callable[[str, str], dict]

DEFAULT_MANDATE = (
    "Trova un problema reale e monetizzabile risolvibile con un prodotto o servizio AI "
    "semplice, economico da produrre e testabile rapidamente."
)


# ------------------------------------------------------------------
# Costruttore generate_fn reale (riusa pattern del Revenue Scan)
# ------------------------------------------------------------------

def _build_real_generate_fn() -> GenerateFn:
    """Ritorna una generate_fn che chiama il provider AI configurato.

    Riutilizza load_real_provider_config() già presente nel repository.
    Fallisce in modo esplicito se la configurazione è assente.
    """
    from mercury_foundry.ai.provider_config import ProviderConfigError, load_real_provider_config
    import openai

    try:
        config = load_real_provider_config()
    except ProviderConfigError as exc:
        raise RuntimeError(
            f"Provider AI non configurato per Opportunity Agent: {exc}\n"
            "Imposta MERCURY_AI_PROVIDER=openai_compatible e le variabili associate\n"
            "(MERCURY_AI_API_KEY, MERCURY_AI_MODEL, MERCURY_AI_BASE_URL).\n"
            "Nei test usa OpportunityAgent(generate_fn=<fixture_callable>)."
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
            temperature=0.2,
            timeout=config.timeout_seconds,
        )
        raw = response.choices[0].message.content
        return json.loads(raw)

    return generate


# ------------------------------------------------------------------
# Parsing della risposta AI
# ------------------------------------------------------------------

def _parse_evidence(raw_ev: list[dict]) -> list[Evidence]:
    """Converte raw evidence in oggetti Evidence, scartando quelli senza fonte."""
    result = []
    for item in raw_ev[:3]:
        url = str(item.get("source_url", "")).strip()
        text = str(item.get("text", "")).strip()
        if url and text:
            result.append(Evidence(text=text, source_url=url))
    return result


def _parse_candidates(raw_list: list[dict]) -> list[CandidateProblem]:
    """Converte la lista AI in CandidateProblem. Max 3 candidati."""
    candidates = []
    for raw in raw_list[:3]:
        evidence = _parse_evidence(raw.get("evidence", []))
        candidates.append(CandidateProblem(
            problem=str(raw.get("problem", "")).strip(),
            target_customer=str(raw.get("target_customer", "")).strip(),
            evidence=evidence,
            frequency_signal=str(raw.get("frequency_signal", "")).strip(),
            urgency_signal=str(raw.get("urgency_signal", "")).strip(),
            willingness_to_pay_signal=str(raw.get("willingness_to_pay_signal", "")).strip(),
        ))
    return candidates


# ------------------------------------------------------------------
# Agent
# ------------------------------------------------------------------

class OpportunityAgent:
    """Agente minimo per identificare un'opportunità di mercato reale.

    Uso in produzione (richiede provider AI e accesso web):
        agent = OpportunityAgent.with_real_provider()
        result = agent.run()

    Uso nei test (fetch e generate completamente sostituibili):
        agent = OpportunityAgent(fetch_fn=fake_fetch, generate_fn=fake_gen)
        result = agent.run("mandato di test")
    """

    def __init__(
        self,
        fetch_fn: FetchFn | None = None,
        generate_fn: GenerateFn | None = None,
    ) -> None:
        self._fetch_fn: FetchFn = fetch_fn if fetch_fn is not None else fetch_market_signals
        self._generate_fn: GenerateFn | None = generate_fn
        self._last_result: OpportunityResult | None = None

    @classmethod
    def with_real_provider(cls) -> "OpportunityAgent":
        """Factory per l'uso in produzione con provider AI reale."""
        return cls(generate_fn=_build_real_generate_fn())

    @property
    def last_result(self) -> OpportunityResult | None:
        """Ultimo risultato prodotto da run(). None se run() non è mai stato chiamato."""
        return self._last_result

    def run(self, mandate: str | None = None) -> OpportunityResult:
        """Esegue un ciclo completo: fetch → analisi AI → risultato strutturato.

        Ritorna sempre un OpportunityResult con status e next_action popolati.
        Non rilancia mai eccezioni di rete o AI: i fallimenti sono incapsulati nello status.
        """
        mandate = mandate or DEFAULT_MANDATE
        timestamp = datetime.now(timezone.utc).isoformat()

        # ── Step 1: fetch segnali di mercato ────────────────────────────────
        try:
            signals = self._fetch_fn()
        except Exception as exc:
            return self._save(OpportunityResult(
                status=OpportunityStatus.BLOCKED_NO_WEB_ACCESS,
                mandate=mandate,
                timestamp=timestamp,
                block_reason=f"Fetch fallito con eccezione: {exc}",
                next_action=(
                    "Verificare connettività di rete verso hn.algolia.com e "
                    "reddit.com, poi riprovare."
                ),
            ))

        if not signals:
            return self._save(OpportunityResult(
                status=OpportunityStatus.BLOCKED_NO_WEB_ACCESS,
                mandate=mandate,
                timestamp=timestamp,
                block_reason=(
                    "Nessuna fonte raggiungibile. Tutte le richieste HTTP hanno fallito "
                    "(timeout o connessione rifiutata). Fonti tentate: "
                    "hn.algolia.com, reddit.com/r/entrepreneur, reddit.com/r/smallbusiness."
                ),
                next_action=(
                    "Abilitare l'accesso HTTP in uscita verso hn.algolia.com e reddit.com, "
                    "oppure fornire segnali di mercato via fetch_fn iniettabile."
                ),
            ))

        # ── Step 2: verifica/costruzione generate_fn ─────────────────────────
        if self._generate_fn is None:
            try:
                self._generate_fn = _build_real_generate_fn()
            except RuntimeError as exc:
                return self._save(OpportunityResult(
                    status=OpportunityStatus.FAILED,
                    mandate=mandate,
                    timestamp=timestamp,
                    source_urls=list(signals.keys()),
                    block_reason=str(exc),
                    next_action=(
                        "Configurare MERCURY_AI_PROVIDER=openai_compatible, "
                        "MERCURY_AI_API_KEY e MERCURY_AI_MODEL, poi riprovare."
                    ),
                ))

        # ── Step 3: analisi AI ───────────────────────────────────────────────
        try:
            user_prompt = build_analysis_prompt(mandate, signals)
            raw = self._generate_fn(SYSTEM_PROMPT, user_prompt)
        except Exception as exc:
            return self._save(OpportunityResult(
                status=OpportunityStatus.FAILED,
                mandate=mandate,
                timestamp=timestamp,
                source_urls=list(signals.keys()),
                block_reason=f"Errore chiamata AI: {exc}",
                next_action="Verificare provider AI (quota, timeout, formato risposta) e riprovare.",
            ))

        # ── Step 4: parsing risposta ─────────────────────────────────────────
        ai_status = str(raw.get("status", "FAILED")).upper()
        if ai_status == "BLOCKED_NO_EVIDENCE":
            return self._save(OpportunityResult(
                status=OpportunityStatus.BLOCKED_NO_EVIDENCE,
                mandate=mandate,
                timestamp=timestamp,
                source_urls=list(signals.keys()),
                block_reason=(
                    "AI: evidenze insufficienti nei segnali recuperati per identificare "
                    "un problema reale con supporto verificabile."
                ),
                next_action=(
                    "Ampliare le fonti di segnale, affinare il mandato con un settore "
                    "più specifico, oppure attendere che emergano più segnali."
                ),
            ))

        raw_candidates = raw.get("candidates", [])
        if not isinstance(raw_candidates, list):
            raw_candidates = []
        candidates = _parse_candidates(raw_candidates)

        selected_index = raw.get("selected_index", 0)
        try:
            selected_index = int(selected_index)
        except (TypeError, ValueError):
            selected_index = 0
        if selected_index < 0 or selected_index >= len(candidates):
            selected_index = 0

        selected = candidates[selected_index] if candidates else None

        # Aggrega source_urls: nomi fonte + URL nelle evidenze
        all_source_urls = list(signals.keys())
        if selected:
            for ev in selected.evidence:
                if ev.source_url not in all_source_urls:
                    all_source_urls.append(ev.source_url)

        next_action = str(raw.get("next_action", "")).strip()
        if not next_action:
            next_action = "Contattare 3 potenziali clienti del target identificato entro 48h."

        return self._save(OpportunityResult(
            status=OpportunityStatus.COMPLETED,
            mandate=mandate,
            timestamp=timestamp,
            problem=selected.problem if selected else None,
            target_customer=selected.target_customer if selected else None,
            evidence=selected.evidence if selected else [],
            source_urls=all_source_urls,
            frequency_signal=selected.frequency_signal if selected else None,
            urgency_signal=selected.urgency_signal if selected else None,
            willingness_to_pay_signal=selected.willingness_to_pay_signal if selected else None,
            proposed_offer=str(raw.get("proposed_offer", "")).strip() or None,
            delivery_format=str(raw.get("delivery_format", "")).strip() or None,
            initial_price=str(raw.get("initial_price", "")).strip() or None,
            why_testable_fast=str(raw.get("why_testable_fast", "")).strip() or None,
            risks=[str(r) for r in raw.get("risks", []) if str(r).strip()],
            next_action=next_action,
            candidates_considered=candidates,
        ))

    def _save(self, result: OpportunityResult) -> OpportunityResult:
        """Salva in memoria e ritorna il risultato."""
        self._last_result = result
        return result
