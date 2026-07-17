"""Costruzione dei prompt per il Revenue Scan AI.

Il system prompt definisce regole assolute per evitare invenzione di dati.
Lo user prompt è costruito esclusivamente dai dati del brief: nessun dato
esterno viene aggiunto dal codice.
"""

from __future__ import annotations

from mercury_foundry.products.local_revenue_scan.models import RevenueScanBrief

# ------------------------------------------------------------------
# System prompt — inviato come "role: system" al provider AI
# ------------------------------------------------------------------
SYSTEM_PROMPT = """\
Sei un consulente di marketing esperto in hospitality locale (bar, ristoranti, bistrot, caffetterie).
Il tuo compito è produrre un Revenue Scan Audit operativo e concreto.

REGOLE ASSOLUTE — da rispettare senza eccezioni:
1. Analizza SOLO i dati presenti nel brief. Non inventare recensioni, metriche, prezzi o dati di affluenza.
2. Distingui sempre tra evidenza diretta (testi forniti) e ipotesi ragionate.
3. Non promettere incrementi garantiti di fatturato.
4. Privilegia azioni applicabili entro 7 giorni, specifiche per questo locale.
5. Collega ogni revenue leak a un'azione prioritaria.
6. Usa tono professionale e comprensibile. Evita gergo tecnico.
7. Rispondi ESCLUSIVAMENTE con JSON valido — nessun testo fuori dal JSON.
8. Usa la lingua indicata nel brief (campo preferred_language).
9. Se mancano dati, indica cosa manca in missing_information invece di inventarlo.

STRUTTURA JSON OBBLIGATORIA (rispetta i limiti numerici):
{
  "executive_summary": "<3-5 frasi che riassumono la situazione e le priorità>",
  "visibility_score": <int 0-100>,
  "conversion_score": <int 0-100>,
  "reputation_score": <int 0-100>,
  "offer_score": <int 0-100>,
  "retention_score": <int 0-100>,
  "top_revenue_leaks": ["<leak 1>", ..., "<max 5 leaks>"],
  "priority_actions": ["<azione 1>", ..., "<max 10 azioni>"],
  "seven_day_plan": [
    "Giorno 1: <azione specifica>",
    "Giorno 2: <azione specifica>",
    "Giorno 3: <azione specifica>",
    "Giorno 4: <azione specifica>",
    "Giorno 5: <azione specifica>",
    "Giorno 6: <azione specifica>",
    "Giorno 7: <azione specifica>"
  ],
  "ready_to_publish_posts": ["<post 1>", "<post 2>", "<post 3>"],
  "promotional_offer": "<1 proposta promozionale specifica e applicabile>",
  "thirty_day_kpis": ["<KPI 1>", ..., "<max 6 KPI>"],
  "assumptions": ["<assunzione 1>", ...],
  "missing_information": ["<dato mancante 1>", ...],
  "human_review_required": <true|false>,
  "estimated_delivery_status": "<ready_to_deliver|review_recommended>"
}

VINCOLI OBBLIGATORI:
- seven_day_plan: ESATTAMENTE 7 elementi (Giorno 1 ... Giorno 7)
- ready_to_publish_posts: ESATTAMENTE 3 elementi
- top_revenue_leaks: massimo 5 elementi
- priority_actions: massimo 10 elementi
- thirty_day_kpis: massimo 6 elementi
- assumptions: almeno 1 elemento (obbligatorio per trasparenza)\
"""


def build_user_prompt(brief: RevenueScanBrief) -> str:
    """Costruisce lo user prompt dai soli dati del brief.

    Nessun dato esterno viene aggiunto: ciò che non è nel brief non entra
    nel prompt, in modo che il provider non possa "inventare" informazioni.
    """
    lines: list[str] = [
        "REVENUE SCAN BRIEF",
        "",
        f"Nome attività:       {brief.business_name}",
        f"Tipo attività:       {brief.business_type}",
        f"Città:               {brief.city}",
        f"Obiettivo principale:{brief.primary_goal.value}",
        f"Lingua preferita:    {brief.preferred_language}",
    ]

    if brief.business_description:
        lines += ["", f"Descrizione attività:\n{brief.business_description}"]
    if brief.target_customer:
        lines += ["", f"Cliente target:\n{brief.target_customer}"]
    if brief.current_offer:
        lines += ["", f"Offerta attuale:\n{brief.current_offer}"]

    url_lines = []
    if brief.website_url:
        url_lines.append(f"  Sito web:    {brief.website_url}")
    if brief.instagram_url:
        url_lines.append(f"  Instagram:   {brief.instagram_url}")
    if brief.google_maps_url:
        url_lines.append(f"  Google Maps: {brief.google_maps_url}")
    if url_lines:
        lines += ["", "URL pubblici:"] + url_lines

    if brief.public_reviews_text:
        lines += [
            "",
            "RECENSIONI PUBBLICHE (copiate manualmente dal cliente — nessuno scraping):",
            brief.public_reviews_text,
        ]
    if brief.social_profile_text:
        lines += [
            "",
            "PROFILO SOCIAL (copiato manualmente):",
            brief.social_profile_text,
        ]
    if brief.website_text:
        lines += [
            "",
            "TESTO SITO WEB (copiato manualmente):",
            brief.website_text,
        ]
    if brief.known_constraints:
        lines += ["", f"Vincoli noti:\n{brief.known_constraints}"]

    lines += [
        "",
        "---",
        "Produci il Revenue Scan Audit rispettando ESATTAMENTE la struttura JSON del system prompt.",
        "Nessun testo al di fuori del JSON.",
    ]
    return "\n".join(lines)
