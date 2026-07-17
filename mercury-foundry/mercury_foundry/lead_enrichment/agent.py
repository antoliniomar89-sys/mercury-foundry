"""Lead Enrichment Agent — core logic.

Ciclo: LEAD_RESULT → ENRICH (max 3 fonti/lead) → CLASSIFY (AI) → SAVE → NEXT ACTION

Dipendenze iniettabili per i test:
- fetch_fn: (leads: list[dict]) -> dict[str, dict]
- generate_fn: (system_prompt: str, user_prompt: str) -> dict
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable

from mercury_foundry.lead_enrichment.enrich import enrich_leads
from mercury_foundry.lead_enrichment.models import (
    Contactability,
    EnrichedLead,
    EnrichedLeadResult,
    EnrichedLeadResultStatus,
    EnrichedLeadStatus,
)
from mercury_foundry.lead_enrichment.prompts import SYSTEM_PROMPT, build_enrichment_prompt

# fetch_fn: data una lista di lead grezzi, ritorna {lead_id: enrichment_dict}
EnrichFn = Callable[[list[dict]], dict[str, dict]]
GenerateFn = Callable[[str, str], dict]

# Soglia minima di lead utilizzabili (HIGH_FIT + PLAUSIBLE)
_MIN_CONTACTABLE = 5


# ------------------------------------------------------------------
# Costruttore generate_fn reale
# ------------------------------------------------------------------

def _build_real_generate_fn() -> GenerateFn:
    """Ritorna una generate_fn che chiama il provider AI configurato."""
    from mercury_foundry.ai.provider_config import ProviderConfigError, load_real_provider_config
    import openai

    try:
        config = load_real_provider_config()
    except ProviderConfigError as exc:
        raise RuntimeError(
            f"Provider AI non configurato per Lead Enrichment Agent: {exc}\n"
            "Imposta MERCURY_AI_PROVIDER=openai_compatible e le variabili associate.\n"
            "Nei test usa EnrichmentAgent(generate_fn=<fixture_callable>)."
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
# Parsing e hard rules
# ------------------------------------------------------------------

def _parse_enriched_leads(
    raw_leads: list[dict],
    original_by_id: dict[str, dict],
    enrichment_by_id: dict[str, dict],
) -> tuple[list[EnrichedLead], list[EnrichedLead]]:
    """Converte output AI → EnrichedLead, applica regole inderogabili.

    Regole hard (applicate dopo l'AI):
    1. Lead senza alcun canale pubblico (contactability NONE + is_reachable False)
       → forzato a REJECTED.
    2. Lead con qualification_status REJECTED → va nella lista rejected_leads.

    Ritorna (enriched_usable, rejected).
    """
    usable: list[EnrichedLead] = []
    rejected: list[EnrichedLead] = []

    for raw in raw_leads:
        lead_id = str(raw.get("lead_id", "")).strip()
        orig = original_by_id.get(lead_id, {})
        enr = enrichment_by_id.get(lead_id, {})

        # Recupera campi dall'originale se mancanti nell'output AI
        name = str(raw.get("name", "") or orig.get("name", lead_id)).strip()
        primary_website = str(
            raw.get("primary_website", "") or orig.get("website", "")
        ).strip()
        public_contact = str(
            raw.get("public_contact", "") or orig.get("public_contact", "")
        ).strip()
        contact_type = str(
            raw.get("contact_type", "") or orig.get("contact_type", "github_profile")
        ).strip()

        # source_urls: combina fonte originale + fonti verificate
        source_urls_raw = raw.get("source_urls", [])
        if not isinstance(source_urls_raw, list):
            source_urls_raw = []
        orig_source = orig.get("source_url", "")
        sources_checked = enr.get("sources_checked", [])
        source_urls = list(
            dict.fromkeys(
                [u for u in [orig_source] + sources_checked + source_urls_raw if u]
            )
        )

        secondary_profiles_raw = raw.get("secondary_profiles", [])
        if not isinstance(secondary_profiles_raw, list):
            secondary_profiles_raw = []
        secondary_profiles = list(
            dict.fromkeys(
                enr.get("secondary_profiles", []) + secondary_profiles_raw
            )
        )

        contactability_raw = str(raw.get("contactability", "NONE")).upper()
        qualification_raw = str(raw.get("qualification_status", "NEEDS_REVIEW")).upper()

        # Regola hard: nessun canale pubblico → REJECTED
        is_reachable = enr.get("is_reachable", False)
        has_any_channel = bool(
            primary_website or public_contact or source_urls or secondary_profiles
        )
        if not has_any_channel and not is_reachable:
            contactability_raw = "NONE"
            qualification_raw = "REJECTED"
            if not raw.get("rejection_reason"):
                raw["rejection_reason"] = (
                    "Nessun canale pubblico disponibile o raggiungibile."
                )

        try:
            lead = EnrichedLead(
                lead_id=lead_id or name,
                name=name,
                verified_role_or_business=str(
                    raw.get("verified_role_or_business", "")
                ).strip(),
                target_match=str(raw.get("target_match", "weak")).strip(),
                primary_website=primary_website,
                public_contact=public_contact,
                contact_type=contact_type,
                secondary_profiles=secondary_profiles,
                evidence_summary=str(raw.get("evidence_summary", "")).strip(),
                source_urls=source_urls,
                fit_reason=str(raw.get("fit_reason", "")).strip(),
                contactability=contactability_raw,
                qualification_status=qualification_raw,
                rejection_reason=str(raw.get("rejection_reason", "")).strip(),
                next_action=str(raw.get("next_action", "")).strip(),
            )
        except Exception:
            continue

        if lead.qualification_status == EnrichedLeadStatus.REJECTED:
            rejected.append(lead)
        else:
            usable.append(lead)

    return usable, rejected


# ------------------------------------------------------------------
# Agent
# ------------------------------------------------------------------

class EnrichmentAgent:
    """Agente minimo per arricchire e classificare lead grezzi.

    Uso in produzione:
        agent = EnrichmentAgent.with_real_provider()
        result = agent.run(lead_result_dict)

    Uso nei test:
        agent = EnrichmentAgent(fetch_fn=fake_enrich, generate_fn=fake_gen)
        result = agent.run(lead_result_dict)
    """

    def __init__(
        self,
        fetch_fn: EnrichFn | None = None,
        generate_fn: GenerateFn | None = None,
    ) -> None:
        self._fetch_fn: EnrichFn = fetch_fn if fetch_fn is not None else enrich_leads
        self._generate_fn: GenerateFn | None = generate_fn
        self._last_result: EnrichedLeadResult | None = None

    @classmethod
    def with_real_provider(cls) -> "EnrichmentAgent":
        """Factory per l'uso in produzione con provider AI reale."""
        return cls(generate_fn=_build_real_generate_fn())

    @property
    def last_result(self) -> EnrichedLeadResult | None:
        return self._last_result

    def run(self, lead_result: dict) -> EnrichedLeadResult:
        """Esegue il ciclo completo per un LeadResult dato.

        lead_result: dict compatibile con LeadResult.to_dict()
        Ritorna sempre un EnrichedLeadResult con status e next_action popolati.
        """
        timestamp = datetime.now(timezone.utc).isoformat()

        # ── Validazione input ────────────────────────────────────────────────
        leads_raw = lead_result.get("leads", [])
        if not isinstance(leads_raw, list) or not leads_raw:
            return self._save(EnrichedLeadResult(
                status=EnrichedLeadResultStatus.BLOCKED_INVALID_LEAD_INPUT,
                lead_result_summary=self._summarise(lead_result),
                block_reason=(
                    "LeadResult non contiene lead validi. "
                    "Eseguire prima Lead Agent e salvare il risultato."
                ),
                next_action=(
                    "Eseguire: python -m mercury_foundry.leads --run-latest "
                    "--output output/leads/latest.json"
                ),
                timestamp=timestamp,
            ))

        # ── Step 1: arricchimento leggero ────────────────────────────────────
        try:
            enrichment_data = self._fetch_fn(leads_raw)
        except Exception as exc:
            return self._save(EnrichedLeadResult(
                status=EnrichedLeadResultStatus.BLOCKED_NO_WEB_ACCESS,
                lead_result_summary=self._summarise(lead_result),
                block_reason=f"Arricchimento fallito: {exc}",
                next_action=(
                    "Verificare connettività HTTP e riprovare."
                ),
                timestamp=timestamp,
            ))

        # Il fetch è andato a buon fine: procediamo alla classificazione AI.
        # I lead senza canali pubblici verranno gestiti dalle hard rules in _parse_enriched_leads.

        # ── Step 2: verifica/costruzione generate_fn ─────────────────────────
        if self._generate_fn is None:
            try:
                self._generate_fn = _build_real_generate_fn()
            except RuntimeError as exc:
                return self._save(EnrichedLeadResult(
                    status=EnrichedLeadResultStatus.FAILED,
                    lead_result_summary=self._summarise(lead_result),
                    block_reason=str(exc),
                    next_action=(
                        "Configurare MERCURY_AI_PROVIDER=openai_compatible, "
                        "MERCURY_AI_API_KEY e MERCURY_AI_MODEL, poi riprovare."
                    ),
                    timestamp=timestamp,
                ))

        # ── Step 3: classificazione AI ───────────────────────────────────────
        opportunity_summary = lead_result.get("opportunity_summary", {})
        original_by_id = {str(l.get("id", "")): l for l in leads_raw}

        try:
            user_prompt = build_enrichment_prompt(
                leads_raw, enrichment_data, opportunity_summary
            )
            raw = self._generate_fn(SYSTEM_PROMPT, user_prompt)
        except Exception as exc:
            return self._save(EnrichedLeadResult(
                status=EnrichedLeadResultStatus.FAILED,
                lead_result_summary=self._summarise(lead_result),
                block_reason=f"Errore chiamata AI: {exc}",
                next_action="Verificare provider AI e riprovare.",
                timestamp=timestamp,
            ))

        # ── Step 4: parsing e hard rules ────────────────────────────────────
        raw_enriched = raw.get("enriched_leads", [])
        if not isinstance(raw_enriched, list):
            raw_enriched = []

        usable, rejected = _parse_enriched_leads(
            raw_enriched, original_by_id, enrichment_data
        )

        next_action = str(raw.get("next_action", "")).strip()
        if not next_action:
            next_action = (
                "Preparare un messaggio personalizzato per ogni lead HIGH_FIT e PLAUSIBLE "
                "evidenziando il problema specifico identificato nel loro profilo."
            )

        # Fonti consultate (dedup, da tutti gli enrichment data)
        sources_consulted: list[str] = list(
            dict.fromkeys(
                url
                for ed in enrichment_data.values()
                for url in ed.get("sources_checked", [])
            )
        )

        # ── Step 5: verifica soglia minima ───────────────────────────────────
        contactable = [
            l for l in usable
            if l.qualification_status in (
                EnrichedLeadStatus.HIGH_FIT,
                EnrichedLeadStatus.PLAUSIBLE,
            )
        ]

        if len(contactable) < _MIN_CONTACTABLE:
            return self._save(EnrichedLeadResult(
                status=EnrichedLeadResultStatus.BLOCKED_INSUFFICIENT_CONTACTABLE_LEADS,
                lead_result_summary=self._summarise(lead_result),
                enriched_leads=usable,
                rejected_leads=rejected,
                sources_consulted=sources_consulted,
                block_reason=(
                    f"Trovati solo {len(contactable)} lead utilizzabili "
                    f"(HIGH_FIT + PLAUSIBLE), minimo richiesto: {_MIN_CONTACTABLE}."
                ),
                next_action=(
                    "Ampliare il LeadResult di partenza o cercare nuove fonti, "
                    "poi riprovare l'arricchimento."
                ),
                timestamp=timestamp,
            ))

        # ── Step 6: determina status finale ─────────────────────────────────
        has_review = any(
            l.qualification_status == EnrichedLeadStatus.NEEDS_REVIEW
            for l in usable
        )
        final_status = (
            EnrichedLeadResultStatus.COMPLETED_WITH_REVIEW
            if has_review
            else EnrichedLeadResultStatus.COMPLETED
        )

        return self._save(EnrichedLeadResult(
            status=final_status,
            lead_result_summary=self._summarise(lead_result),
            enriched_leads=usable,
            rejected_leads=rejected,
            sources_consulted=sources_consulted,
            next_action=next_action,
            timestamp=timestamp,
        ))

    def _save(self, result: EnrichedLeadResult) -> EnrichedLeadResult:
        self._last_result = result
        return result

    @staticmethod
    def _summarise(lead_result: dict) -> dict:
        """Estrae i campi chiave del LeadResult per il report."""
        opp = lead_result.get("opportunity_summary", {})
        return {
            "status": lead_result.get("status"),
            "total_leads": lead_result.get("total_leads"),
            "qualified_count": lead_result.get("qualified_count"),
            "timestamp": lead_result.get("timestamp"),
            "opportunity_problem": opp.get("problem"),
            "opportunity_target": opp.get("target_customer"),
            "opportunity_offer": opp.get("proposed_offer"),
        }
