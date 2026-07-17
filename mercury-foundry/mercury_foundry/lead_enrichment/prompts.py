"""Prompt per il Lead Enrichment Agent."""
from __future__ import annotations

import json

SYSTEM_PROMPT = """\
Sei un assistente per la qualificazione commerciale leggera di lead B2B.

REGOLA FONDAMENTALE — NESSUNA INVENZIONE:
Non inventare mai email, nomi, URL, ruoli, aziende o dati di contatto.
Usa esclusivamente le informazioni fornite nell'input.
Se un'informazione non è disponibile, lascia il campo vuoto o scrivi "N/D".

CLASSIFICAZIONE:
- HIGH_FIT: ruolo/business chiaramente verificato, target match forte, canale pubblico diretto, motivo specifico.
- PLAUSIBLE: ruolo/business ragionevolmente plausibile, target match sufficiente, almeno un canale pubblico, nessuna contraddizione evidente.
- NEEDS_REVIEW: sembra utile ma manca conferma secondaria, canale indiretto, dubbi limitati.
- REJECTED: fuori target, nessun canale pubblico, identità non verificabile, fonti contraddittorie, profilo inattivo/falso/irrilevante.

CONTACTABILITY:
- DIRECT: esiste un URL o sito diretto e raggiungibile (website, email pubblica, form di contatto).
- INDIRECT: solo profilo social o piattaforma intermediaria.
- NONE: nessun canale pubblico disponibile.

REGOLA QB — INCLUSIONE:
Preferire l'inclusione alla esclusione.
Un lead PLAUSIBLE deve restare nella lista.
Scartare solo in presenza di contraddizioni evidenti o assenza totale di canali pubblici.

OUTPUT JSON (non aggiungere campi extra, non inventare dati):
{
  "enriched_leads": [
    {
      "lead_id": "<id originale>",
      "verified_role_or_business": "<ruolo o business verificato o plausibile>",
      "target_match": "<strong|sufficient|weak>",
      "contactability": "<DIRECT|INDIRECT|NONE>",
      "qualification_status": "<HIGH_FIT|PLAUSIBLE|NEEDS_REVIEW|REJECTED>",
      "evidence_summary": "<sintesi breve delle evidenze disponibili, solo da input>",
      "secondary_profiles": ["<url>"],
      "fit_reason": "<motivo specifico di compatibilità col target>",
      "rejection_reason": "<motivo se REJECTED, altrimenti stringa vuota>",
      "next_action": "<azione concreta per contattare o verificare>"
    }
  ],
  "next_action": "<azione generale per il primo batch di contatto>"
}
"""


def build_enrichment_prompt(
    leads: list[dict],
    enrichment_data: dict[str, dict],
    opportunity_summary: dict,
) -> str:
    """Costruisce il prompt utente per la classificazione.

    leads: lead grezzi dal LeadResult
    enrichment_data: {lead_id: verifica_leggera} da enrich_leads()
    opportunity_summary: contesto dell'opportunità
    """
    lines: list[str] = []

    lines.append("## OPPORTUNITÀ")
    lines.append(f"Problema: {opportunity_summary.get('problem', 'N/D')}")
    lines.append(f"Target: {opportunity_summary.get('target_customer', 'N/D')}")
    lines.append(f"Offerta: {opportunity_summary.get('proposed_offer', 'N/D')}")
    lines.append("")

    lines.append("## LEAD GREZZI DA CLASSIFICARE")
    lines.append("")

    for lead in leads:
        lead_id = str(lead.get("id", ""))
        enr = enrichment_data.get(lead_id, {})

        sources_checked = enr.get("sources_checked", [])
        is_reachable = enr.get("is_reachable", False)
        secondary_profiles = enr.get("secondary_profiles", [])
        extra_evidence = enr.get("extra_evidence", "")

        lines.append(f"### Lead ID: {lead_id}")
        lines.append(f"Nome: {lead.get('name', 'N/D')}")
        lines.append(f"Segmento: {lead.get('segment', 'N/D')}")
        lines.append(f"Website: {lead.get('website', 'N/D')}")
        lines.append(f"Contatto pubblico: {lead.get('public_contact', 'N/D')}")
        lines.append(f"Tipo contatto: {lead.get('contact_type', 'N/D')}")
        lines.append(f"Location: {lead.get('location', 'N/D')}")
        lines.append(f"Motivo fit originale: {lead.get('fit_reason', 'N/D')}")
        lines.append(f"Evidenza originale: {lead.get('evidence', 'N/D')}")
        lines.append(f"URL fonte: {lead.get('source_url', 'N/D')}")
        lines.append(f"Priorità originale: {lead.get('priority', 'N/D')}")
        lines.append(f"Status originale: {lead.get('status', 'N/D')}")
        lines.append(f"--- Verifica leggera ---")
        lines.append(f"Fonti consultate ({len(sources_checked)}): {', '.join(sources_checked) or 'nessuna'}")
        lines.append(f"Almeno una fonte raggiungibile: {'sì' if is_reachable else 'no'}")
        if secondary_profiles:
            lines.append(f"Profili secondari trovati: {', '.join(secondary_profiles)}")
        if extra_evidence:
            lines.append(f"Evidenza aggiuntiva: {extra_evidence}")
        lines.append("")

    lines.append(
        "Classifica ogni lead secondo le regole di sistema. "
        "Restituisci JSON valido. Non inventare dati non presenti nell'input."
    )

    return "\n".join(lines)
