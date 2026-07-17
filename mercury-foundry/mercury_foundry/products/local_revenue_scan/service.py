"""Servizio principale per la generazione del Revenue Scan.

Design:
- `RevenueScanService` accetta un `generate_fn` iniettabile (Callable).
  In produzione si usa `_build_real_generate_fn()` che carica la config dal
  provider AI esistente. Nei test si inietta un fixture deterministico.
- Il quality gate è parte del servizio (non un modulo separato).
- L'idempotency_key garantisce che la stessa richiesta ritorni lo stesso report.
- Se il provider non è configurato, l'errore è esplicito e utile: nessun
  fallback silenzioso a dati simulati.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from mercury_foundry.products.local_revenue_scan.models import (
    PrimaryGoal,
    ReportStatus,
    RevenueScanBrief,
    RevenueScanReport,
)
from mercury_foundry.products.local_revenue_scan.prompts import (
    SYSTEM_PROMPT,
    build_user_prompt,
)
from mercury_foundry.products.local_revenue_scan.scoring import (
    compute_confidence_level,
    compute_overall_score,
)

# (system_prompt: str, user_prompt: str) -> dict con i campi del report
GenerateFn = Callable[[str, str], dict]

# Cache idempotency in-memory (V0 — nessun DB).
# Chiave: idempotency_key del brief → RevenueScanReport già generato.
_IDEMPOTENCY_CACHE: dict[str, RevenueScanReport] = {}


# ------------------------------------------------------------------
# Costruttore del generatore reale
# ------------------------------------------------------------------

def _build_real_generate_fn() -> GenerateFn:
    """Ritorna una funzione che chiama il provider AI configurato.

    Riutilizza `load_real_provider_config()` già presente nel repository.
    Fallisce in modo esplicito se la configurazione è assente o incompleta:
    non simula mai una risposta AI.

    Richiede:
      MERCURY_AI_PROVIDER=openai_compatible
      MERCURY_AI_API_KEY=<chiave>
      MERCURY_AI_MODEL=<modello>
      MERCURY_AI_BASE_URL=<endpoint> (opzionale per OpenAI nativo)
    """
    from mercury_foundry.ai.provider_config import ProviderConfigError, load_real_provider_config
    import openai

    try:
        config = load_real_provider_config()
    except ProviderConfigError as exc:
        raise RuntimeError(
            f"Provider AI non configurato per il Revenue Scan: {exc}\n"
            "Imposta MERCURY_AI_PROVIDER=openai_compatible e le variabili associate\n"
            "(MERCURY_AI_API_KEY, MERCURY_AI_MODEL, MERCURY_AI_BASE_URL).\n"
            "Nei test usa RevenueScanService(generate_fn=<fixture_callable>)."
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
                {"role": "user",   "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.3,
            timeout=config.timeout_seconds,
        )
        raw = response.choices[0].message.content
        return json.loads(raw)

    return generate


# ------------------------------------------------------------------
# Parsing della risposta AI
# ------------------------------------------------------------------

def _int_score(v: Any, default: int = 50) -> int:
    try:
        return max(0, min(100, int(v)))
    except (TypeError, ValueError):
        return default


def _str_list(v: Any, max_items: int) -> list[str]:
    if not isinstance(v, list):
        return []
    return [str(x) for x in v[:max_items]]


def _parse_ai_response(raw: dict, brief: RevenueScanBrief) -> RevenueScanReport:
    """Converte la risposta AI grezza in RevenueScanReport.

    Tronca le liste ai limiti di specifica (il quality gate validerà dopo).
    Non aggiunge dati non presenti nella risposta AI.
    """
    vis  = _int_score(raw.get("visibility_score",  50))
    conv = _int_score(raw.get("conversion_score",  50))
    rep  = _int_score(raw.get("reputation_score",  50))
    off  = _int_score(raw.get("offer_score",       50))
    ret  = _int_score(raw.get("retention_score",   50))

    seven_day_raw = raw.get("seven_day_plan", [])
    seven_day: list[str] = (
        [str(x) for x in seven_day_raw[:7]]
        if isinstance(seven_day_raw, list) else []
    )

    posts_raw = raw.get("ready_to_publish_posts", [])
    posts: list[str] = (
        [str(x) for x in posts_raw[:3]]
        if isinstance(posts_raw, list) else []
    )

    return RevenueScanReport(
        report_id=str(uuid.uuid4()),
        business_name=brief.business_name,
        generated_at=datetime.now(timezone.utc),
        executive_summary=str(raw.get("executive_summary", "")),
        overall_score=compute_overall_score(
            visibility=vis, conversion=conv,
            reputation=rep, offer=off, retention=ret,
        ),
        visibility_score=vis,
        conversion_score=conv,
        reputation_score=rep,
        offer_score=off,
        retention_score=ret,
        top_revenue_leaks=_str_list(raw.get("top_revenue_leaks", []),  5),
        priority_actions= _str_list(raw.get("priority_actions",  []), 10),
        seven_day_plan=seven_day,
        ready_to_publish_posts=posts,
        promotional_offer=str(raw.get("promotional_offer", "")),
        thirty_day_kpis=_str_list(raw.get("thirty_day_kpis", []), 6),
        assumptions=      _str_list(raw.get("assumptions",       []), 50),
        missing_information=_str_list(raw.get("missing_information", []), 50),
        confidence_level=compute_confidence_level(brief),
        human_review_required=bool(raw.get("human_review_required", False)),
        estimated_delivery_status=str(
            raw.get("estimated_delivery_status", "review_recommended")
        ),
        status=ReportStatus.READY,
        quality_issues=[],
    )


# ------------------------------------------------------------------
# Quality gate
# ------------------------------------------------------------------

def run_quality_gate(report: RevenueScanReport) -> tuple[bool, list[str]]:
    """Valida il report prima della consegna.

    Ritorna (passed: bool, issues: list[str]).
    Se passed=False il chiamante deve impostare status=REVIEW_REQUIRED.

    Controlla:
    - Presenza di tutte le sezioni obbligatorie
    - Limiti numerici rispettati (max leaks, azioni, KPI; esatto per giorni e post)
    - Score nel range 0-100
    - Assumptions presente (trasparenza)
    - Confidence_level nel range
    """
    issues: list[str] = []

    if not report.executive_summary or len(report.executive_summary) < 50:
        issues.append("executive_summary assente o troppo breve (< 50 caratteri)")

    for dim in (
        "overall_score", "visibility_score", "conversion_score",
        "reputation_score", "offer_score", "retention_score",
    ):
        v = getattr(report, dim)
        if not (0 <= v <= 100):
            issues.append(f"{dim} fuori range 0-100: {v}")

    if not report.top_revenue_leaks:
        issues.append("top_revenue_leaks vuoto: almeno 1 leak richiesto")
    if len(report.top_revenue_leaks) > 5:
        issues.append(f"top_revenue_leaks supera il limite di 5: {len(report.top_revenue_leaks)}")

    if not report.priority_actions:
        issues.append("priority_actions vuoto: almeno 1 azione richiesta")
    if len(report.priority_actions) > 10:
        issues.append(f"priority_actions supera il limite di 10: {len(report.priority_actions)}")

    if len(report.seven_day_plan) != 7:
        issues.append(
            f"seven_day_plan deve avere esattamente 7 elementi: {len(report.seven_day_plan)}"
        )

    if len(report.ready_to_publish_posts) != 3:
        issues.append(
            f"ready_to_publish_posts deve avere esattamente 3 elementi: "
            f"{len(report.ready_to_publish_posts)}"
        )

    if not report.promotional_offer:
        issues.append("promotional_offer assente")

    if len(report.thirty_day_kpis) > 6:
        issues.append(f"thirty_day_kpis supera il limite di 6: {len(report.thirty_day_kpis)}")

    if not report.assumptions:
        issues.append("assumptions assente: obbligatorio per trasparenza")

    if not (0 <= report.confidence_level <= 100):
        issues.append(f"confidence_level fuori range 0-100: {report.confidence_level}")

    return len(issues) == 0, issues


# ------------------------------------------------------------------
# Servizio
# ------------------------------------------------------------------

class RevenueScanService:
    """Genera il Revenue Scan Report da un RevenueScanBrief.

    Uso in produzione:
        service = RevenueScanService.with_real_provider()
        report  = service.generate(brief)

    Uso nei test (fixture deterministico):
        service = RevenueScanService(generate_fn=my_fixture_fn)
        report  = service.generate(brief)
    """

    def __init__(self, generate_fn: GenerateFn | None = None) -> None:
        self._generate_fn = generate_fn
        # Cache privata per istanza: evita contaminazione cross-test.
        self._cache: dict[str, RevenueScanReport] = {}

    def generate(self, brief: RevenueScanBrief) -> RevenueScanReport:
        """Genera il report. Rispetta l'idempotency_key.

        Raises:
            RuntimeError: se il provider AI non è configurato e nessun
                          generate_fn è stato iniettato.
        """
        if brief.idempotency_key in self._cache:
            return self._cache[brief.idempotency_key]

        if self._generate_fn is None:
            raise RuntimeError(
                "Provider AI non configurato.\n"
                "In produzione: usa RevenueScanService.with_real_provider().\n"
                "Nei test:      usa RevenueScanService(generate_fn=fixture_fn).\n"
                "Non viene mai simulata una risposta AI senza configurazione esplicita."
            )

        raw = self._generate_fn(SYSTEM_PROMPT, build_user_prompt(brief))
        report = _parse_ai_response(raw, brief)

        passed, issues = run_quality_gate(report)
        if not passed:
            report.status = ReportStatus.REVIEW_REQUIRED
            report.quality_issues = issues

        self._cache[brief.idempotency_key] = report
        return report

    @classmethod
    def with_real_provider(cls) -> "RevenueScanService":
        """Factory per uso in produzione. Fallisce se il provider non è configurato."""
        return cls(generate_fn=_build_real_generate_fn())
