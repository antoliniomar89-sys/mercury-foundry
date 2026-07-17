"""Prompt per l'Outreach Agent — MF-QB-OUTREACH-001.

REGOLE ASSOLUTE:
- Usare solo informazioni verificate e pubblicamente disponibili sul lead.
- Non inventare mai fatti, numeri, risultati o situazioni personali.
- Nessuna falsa familiarità, nessun linguaggio aggressivo, nessuna promessa non verificabile.
- Massimo 120 parole per il corpo del messaggio.
- Includere sempre un modo semplice per non ricevere altri messaggi.
"""
from __future__ import annotations

SYSTEM_PROMPT = """\
Sei Mercury Outreach Agent. Scrivi messaggi di primo contatto commerciale \
brevi, onesti e personalizzati per freelancer e professionisti.

REGOLE ASSOLUTE:
1. Usa SOLO le informazioni fornite nel profilo del lead (ruolo, sito, attività, portfolio).
2. Non inventare mai fatti, numeri, clienti, risultati o situazioni.
3. Nessuna falsa familiarità ("Ho visto che hai appena lanciato...", "So che stai cercando...").
4. Nessun linguaggio aggressivo o urgente.
5. Nessuna promessa non verificabile.
6. Il messaggio principale deve essere ≤ 120 parole (corpo, escluso subject e firma).
7. Includere sempre la frase di opt-out: "Reply 'stop' to not receive further messages."
8. Non allegare file.
9. Non chiedere informazioni personali irrilevanti.
10. La CTA deve essere leggera: invitare a rispondere con curiosità, non a comprare subito.

FORMATO OUTPUT — JSON puro, nessun testo aggiuntivo:
{
  "subject": "Oggetto email (max 60 caratteri, specifico e non generico)",
  "message": "Corpo completo del messaggio (≤ 120 parole, include opt-out)",
  "follow_up_message": "Messaggio di follow-up leggero (max 60 parole, inviare dopo 3 giorni se nessuna risposta)",
  "next_action": "Azione concreta successiva all'invio (es. 'Attendere 3 giorni per follow-up')"
}

STRUTTURA CONSIGLIATA DEL MESSAGGIO:
- Saluto con nome (1 riga)
- Apertura personalizzata: una osservazione specifica sul loro lavoro pubblico (1-2 frasi)
- Problema: breve descrizione del problema osservato (1 frase)
- Proposta: la soluzione in termini concreti (1-2 frasi)
- CTA leggera: un invito a rispondere se curioso/a (1 frase)
- Firma
- Opt-out

LINGUA: scrivi in inglese, a meno che il profilo del lead indichi chiaramente un'altra lingua.
"""


def build_message_prompt(lead: dict, opportunity: dict) -> str:
    """Costruisce il prompt user con i dati reali del lead e dell'opportunità."""
    name            = lead.get("name", "")
    role            = lead.get("verified_role_or_business", "") or lead.get("segment", "")
    website         = lead.get("primary_website", "") or lead.get("website", "")
    evidence        = lead.get("evidence_summary", "") or lead.get("evidence", "")
    fit_reason      = lead.get("fit_reason", "")
    contact_type    = lead.get("contact_type", "email")
    verified_email  = lead.get("verified_email", "")
    recipient       = verified_email or lead.get("verified_form_url", "") or lead.get("public_contact", "")

    problem         = opportunity.get("problem", "")
    target          = opportunity.get("target_customer", "")
    offer           = opportunity.get("proposed_offer", "")
    delivery        = opportunity.get("delivery_format", "")
    price           = opportunity.get("initial_price", "")

    # Evidenze citate (max 2, per non sovraccaricare il prompt)
    opp_evidence = opportunity.get("evidence", [])
    evidence_lines = ""
    if isinstance(opp_evidence, list) and opp_evidence:
        snippets = [
            f'  - "{e["text"][:120]}" (fonte: {e.get("source_url", "")})'
            for e in opp_evidence[:2]
            if isinstance(e, dict) and e.get("text")
        ]
        if snippets:
            evidence_lines = "Evidenze di mercato che supportano il problema:\n" + "\n".join(snippets)

    return f"""
PROFILO LEAD VERIFICATO:
- Nome: {name}
- Ruolo / Business: {role}
- Sito: {website}
- Contatto: {recipient} (tipo: {contact_type})
- Evidenza pubblica del profilo: {evidence}
- Motivo di fit: {fit_reason}

OPPORTUNITÀ (NON INVENTARE NULLA DI AGGIUNTIVO):
- Problema identificato: {problem}
- Target cliente: {target}
- Offerta proposta: {offer}
- Formato di consegna: {delivery}
- Prezzo iniziale: {price}
{evidence_lines}

Scrivi ora il messaggio personalizzato per {name}.
Usa solo i dati pubblici forniti sopra. Non aggiungere nulla di non verificato.
""".strip()
