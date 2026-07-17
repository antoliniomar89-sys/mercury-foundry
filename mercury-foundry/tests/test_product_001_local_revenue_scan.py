"""MF-PRODUCT-001 — Test selettivi del Revenue Scan for Local Hospitality.

20 casi minimi definiti dalla spec, eseguiti con fixture provider deterministico.
Nessuna chiamata AI reale: il generate_fn è sempre iniettato.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from mercury_foundry.products.local_revenue_scan.models import (
    PrimaryGoal,
    ReportStatus,
    RevenueScanBrief,
    RevenueScanReport,
)
from mercury_foundry.products.local_revenue_scan.scoring import (
    SCORING_WEIGHTS,
    compute_confidence_level,
    compute_overall_score,
)
from mercury_foundry.products.local_revenue_scan.service import (
    RevenueScanService,
    run_quality_gate,
)
from mercury_foundry.products.local_revenue_scan.renderer import (
    render_html,
    render_json,
    render_markdown,
    save_outputs,
)


# ======================================================================
# Fixture helpers
# ======================================================================

def _make_brief(
    *,
    business_name: str = "Test Bar",
    business_type: str = "bar",
    city: str = "Roma",
    primary_goal: str = "foot_traffic",
    idempotency_key: str | None = None,
    **kwargs,
) -> RevenueScanBrief:
    return RevenueScanBrief.from_dict(
        {
            "business_name": business_name,
            "business_type": business_type,
            "city": city,
            "primary_goal": primary_goal,
            "idempotency_key": idempotency_key or str(uuid.uuid4()),
            **kwargs,
        }
    )


def _valid_raw(
    *,
    n_leaks: int = 3,
    n_actions: int = 5,
    n_days: int = 7,
    n_posts: int = 3,
    n_kpis: int = 4,
    executive_summary: str | None = None,
    visibility_score: int = 55,
    conversion_score: int = 60,
    reputation_score: int = 65,
    offer_score: int = 50,
    retention_score: int = 45,
) -> dict:
    """Risposta AI fittizia conforme alla spec."""
    summary = executive_summary or (
        "Il locale presenta opportunità concrete di miglioramento nella "
        "visibilità digitale e nella conversione dei clienti. Le recensioni "
        "suggeriscono buona qualità del prodotto ma gap nella comunicazione. "
        "Priorità immediata: prenotazioni online e aggiornamento profilo Google."
    )
    return {
        "executive_summary": summary,
        "visibility_score": visibility_score,
        "conversion_score": conversion_score,
        "reputation_score": reputation_score,
        "offer_score": offer_score,
        "retention_score": retention_score,
        "top_revenue_leaks": [f"Leak {i}" for i in range(1, n_leaks + 1)],
        "priority_actions": [f"Azione {i}" for i in range(1, n_actions + 1)],
        "seven_day_plan": [f"Giorno {i}: azione specifica del giorno {i}" for i in range(1, n_days + 1)],
        "ready_to_publish_posts": [f"Post {i} fittizio per test" for i in range(1, n_posts + 1)],
        "promotional_offer": "Offerta fittizia: aperitivo speciale giovedì",
        "thirty_day_kpis": [f"KPI {i}" for i in range(1, n_kpis + 1)],
        "assumptions": [
            "Analisi basata esclusivamente sui dati del brief",
            "Stime indicative non garantite",
        ],
        "missing_information": ["Fatturato medio mensile", "Numero coperti medi"],
        "human_review_required": False,
        "estimated_delivery_status": "ready_to_deliver",
    }


def _fixture_service(raw_override: dict | None = None) -> RevenueScanService:
    raw = raw_override if raw_override is not None else _valid_raw()

    def generate_fn(system_prompt: str, user_prompt: str) -> dict:
        return raw

    return RevenueScanService(generate_fn=generate_fn)


# ======================================================================
# TEST 01 — Brief valido
# ======================================================================

def test_01_valid_brief_produces_report():
    """Un brief valido genera un RevenueScanReport con status READY."""
    brief = _make_brief()
    service = _fixture_service()
    report = service.generate(brief)
    assert isinstance(report, RevenueScanReport)
    assert report.status == ReportStatus.READY
    assert report.business_name == "Test Bar"
    assert report.report_id  # non vuoto


# ======================================================================
# TEST 02 — Brief incompleto (solo campi obbligatori)
# ======================================================================

def test_02_minimal_brief_accepted():
    """Brief con solo i campi obbligatori è accettato; confidence bassa."""
    brief = _make_brief()
    service = _fixture_service()
    report = service.generate(brief)
    assert report.confidence_level >= 30
    assert report.confidence_level <= 90


# ======================================================================
# TEST 03 — primary_goal invalido
# ======================================================================

def test_03_invalid_primary_goal_raises():
    """primary_goal non nel set ammesso solleva ValueError."""
    with pytest.raises(ValueError, match="primary_goal"):
        RevenueScanBrief.from_dict(
            {
                "business_name": "Bar",
                "business_type": "bar",
                "city": "Roma",
                "primary_goal": "world_domination",
                "idempotency_key": "k1",
            }
        )


# ======================================================================
# TEST 04 — Score tra 0 e 100
# ======================================================================

def test_04_all_scores_in_range():
    """Tutti i punteggi (subscore e overall) sono nel range 0-100."""
    brief = _make_brief()
    service = _fixture_service()
    report = service.generate(brief)
    for attr in (
        "overall_score", "visibility_score", "conversion_score",
        "reputation_score", "offer_score", "retention_score",
        "confidence_level",
    ):
        v = getattr(report, attr)
        assert 0 <= v <= 100, f"{attr}={v} fuori range"


# ======================================================================
# TEST 05 — Pesi scoring corretti
# ======================================================================

def test_05_scoring_weights_sum_to_100():
    """I pesi di SCORING_WEIGHTS sommano esattamente a 100."""
    assert sum(SCORING_WEIGHTS.values()) == 100


def test_05b_overall_score_formula():
    """compute_overall_score applica correttamente la media pesata."""
    # Con tutti i subscore a 60: overall deve essere 60
    result = compute_overall_score(
        visibility=60, conversion=60, reputation=60, offer=60, retention=60
    )
    assert result == 60

    # Verifica manuale con valori diversi
    expected = round(
        (80 * 20 + 70 * 25 + 60 * 20 + 50 * 20 + 40 * 15) / 100
    )
    result2 = compute_overall_score(
        visibility=80, conversion=70, reputation=60, offer=50, retention=40
    )
    assert result2 == expected


# ======================================================================
# TEST 06 — Dati mancanti riducono la confidence
# ======================================================================

def test_06_missing_data_reduces_confidence():
    """Brief con soli campi obbligatori ha confidence < brief completo."""
    brief_min = _make_brief()
    brief_full = _make_brief(
        business_description="Ottimo ristorante",
        target_customer="Famiglie",
        current_offer="Menu fisso 15 EUR",
        public_reviews_text="Recensioni positive",
        social_profile_text="500 follower",
        website_text="Sito aggiornato",
        website_url="https://example.com",
        instagram_url="https://instagram.com/test",
        google_maps_url="https://maps.google.com/test",
        known_constraints="Budget limitato",
    )
    conf_min  = compute_confidence_level(brief_min)
    conf_full = compute_confidence_level(brief_full)
    assert conf_min < conf_full, (
        f"Brief minimo ({conf_min}) dovrebbe avere confidence < brief completo ({conf_full})"
    )


# ======================================================================
# TEST 07 — Nessuna invenzione di dati
# ======================================================================

def test_07_no_data_invention_without_provider():
    """Senza generate_fn configurato il servizio NON simula una risposta AI."""
    service = RevenueScanService(generate_fn=None)
    brief = _make_brief()
    with pytest.raises(RuntimeError, match="Provider AI non configurato"):
        service.generate(brief)


# ======================================================================
# TEST 08 — Massimo 5 revenue leaks
# ======================================================================

def test_08_max_5_revenue_leaks():
    """Il parser tronca i revenue leaks a 5 anche se l'AI ne restituisce di più."""
    raw = _valid_raw(n_leaks=5)
    service = _fixture_service(raw)
    report = service.generate(_make_brief())
    assert len(report.top_revenue_leaks) <= 5


def test_08b_extra_leaks_truncated():
    """Se l'AI restituisce 8 leaks, il report ne contiene al massimo 5."""
    raw = _valid_raw()
    raw["top_revenue_leaks"] = [f"Leak {i}" for i in range(1, 9)]  # 8 leaks
    service = _fixture_service(raw)
    report = service.generate(_make_brief())
    assert len(report.top_revenue_leaks) == 5


# ======================================================================
# TEST 09 — Massimo 10 azioni
# ======================================================================

def test_09_max_10_actions():
    """Il parser tronca le priority_actions a 10."""
    raw = _valid_raw()
    raw["priority_actions"] = [f"Azione {i}" for i in range(1, 15)]  # 14 azioni
    service = _fixture_service(raw)
    report = service.generate(_make_brief())
    assert len(report.priority_actions) == 10


# ======================================================================
# TEST 10 — Esattamente 7 giorni
# ======================================================================

def test_10_exactly_7_days():
    """Il seven_day_plan deve avere esattamente 7 elementi per passare il gate."""
    raw = _valid_raw(n_days=7)
    service = _fixture_service(raw)
    report = service.generate(_make_brief())
    assert len(report.seven_day_plan) == 7
    assert report.status == ReportStatus.READY


# ======================================================================
# TEST 11 — Esattamente 3 post
# ======================================================================

def test_11_exactly_3_posts():
    """ready_to_publish_posts deve avere esattamente 3 elementi."""
    raw = _valid_raw(n_posts=3)
    service = _fixture_service(raw)
    report = service.generate(_make_brief())
    assert len(report.ready_to_publish_posts) == 3


# ======================================================================
# TEST 12 — Quality gate pass
# ======================================================================

def test_12_quality_gate_pass():
    """Un report conforme alla spec supera il quality gate."""
    raw = _valid_raw()
    service = _fixture_service(raw)
    report = service.generate(_make_brief())
    passed, issues = run_quality_gate(report)
    assert passed, f"Quality gate fallito: {issues}"
    assert report.status == ReportStatus.READY
    assert report.quality_issues == []


# ======================================================================
# TEST 13 — Quality gate review_required
# ======================================================================

def test_13_quality_gate_review_required_on_wrong_days():
    """Un report con seven_day_plan != 7 viene marcato REVIEW_REQUIRED."""
    raw = _valid_raw(n_days=3)  # solo 3 giorni: gate fallisce
    service = _fixture_service(raw)
    report = service.generate(_make_brief())
    assert report.status == ReportStatus.REVIEW_REQUIRED
    assert any("seven_day_plan" in issue for issue in report.quality_issues)


def test_13b_quality_gate_review_required_on_missing_assumptions():
    """Assumptions vuoto → REVIEW_REQUIRED."""
    raw = _valid_raw()
    raw["assumptions"] = []
    service = _fixture_service(raw)
    report = service.generate(_make_brief())
    assert report.status == ReportStatus.REVIEW_REQUIRED
    assert any("assumption" in issue.lower() for issue in report.quality_issues)


# ======================================================================
# TEST 14 — Rendering JSON
# ======================================================================

def test_14_rendering_json():
    """render_json produce JSON valido con tutti i campi obbligatori."""
    raw = _valid_raw()
    service = _fixture_service(raw)
    report = service.generate(_make_brief())
    json_str = render_json(report)
    data = json.loads(json_str)

    for field in (
        "report_id", "business_name", "generated_at", "status",
        "executive_summary", "overall_score", "scores",
        "top_revenue_leaks", "priority_actions", "seven_day_plan",
        "ready_to_publish_posts", "promotional_offer", "thirty_day_kpis",
        "assumptions", "missing_information", "confidence_level",
        "human_review_required", "estimated_delivery_status",
    ):
        assert field in data, f"Campo mancante nel JSON: {field}"

    assert isinstance(data["scores"], dict)
    for dim in ("visibility", "conversion", "reputation", "offer", "retention"):
        assert dim in data["scores"]


# ======================================================================
# TEST 15 — Rendering Markdown
# ======================================================================

def test_15_rendering_markdown():
    """render_markdown produce testo con le sezioni obbligatorie."""
    raw = _valid_raw()
    service = _fixture_service(raw)
    report = service.generate(_make_brief())
    md = render_markdown(report)

    assert "# Revenue Scan" in md
    assert "## Executive Summary" in md
    assert "## Scorecard" in md
    assert "## Revenue Leaks" in md
    assert "## Azioni Prioritarie" in md
    assert "## Piano 7 Giorni" in md
    assert "## Contenuti Pronti" in md
    assert "## Proposta Promozionale" in md
    assert "## KPI a 30 Giorni" in md
    assert "## Assunzioni" in md


# ======================================================================
# TEST 16 — Rendering HTML
# ======================================================================

def test_16_rendering_html():
    """render_html produce HTML con <!DOCTYPE html> e le sezioni chiave."""
    raw = _valid_raw()
    service = _fixture_service(raw)
    report = service.generate(_make_brief())
    html = render_html(report)

    assert "<!DOCTYPE html>" in html
    assert "<html" in html
    assert "Revenue Scan" in html
    assert "Executive Summary" in html
    assert "Scorecard" in html
    assert "Revenue Leaks" in html
    assert "Piano 7 Giorni" in html
    assert "KPI" in html
    assert report.business_name in html


# ======================================================================
# TEST 17 — CLI con fixture provider
# ======================================================================

def test_17_cli_with_fixture_provider(tmp_path, monkeypatch):
    """CLI legge il brief, genera il report, salva i 3 file."""
    from mercury_foundry.products.local_revenue_scan import cli
    from mercury_foundry.products.local_revenue_scan.service import _build_real_generate_fn

    brief_file = tmp_path / "brief.json"
    brief_file.write_text(
        json.dumps(
            {
                "business_name": "CLI Test Bar",
                "business_type": "bar",
                "city": "Torino",
                "primary_goal": "bookings",
                "idempotency_key": "cli-test-001",
            }
        ),
        encoding="utf-8",
    )
    output_dir = tmp_path / "output"

    # Inietta un generate_fn deterministico patchando _build_real_generate_fn
    # sul modulo service (il CLI lo importa da lì dentro la funzione).
    def fake_build_real() -> object:
        def generate(sp: str, up: str) -> dict:
            return _valid_raw()
        return generate

    monkeypatch.setattr(
        "mercury_foundry.products.local_revenue_scan.service._build_real_generate_fn",
        fake_build_real,
    )

    exit_code = cli.main(["--input", str(brief_file), "--output-dir", str(output_dir)])
    assert exit_code in (0, 2), f"Exit code inatteso: {exit_code}"

    files = list(output_dir.glob("*"))
    extensions = {f.suffix for f in files}
    assert ".json" in extensions, "File JSON non trovato"
    assert ".md"   in extensions, "File Markdown non trovato"
    assert ".html" in extensions, "File HTML non trovato"


# ======================================================================
# TEST 18 — Idempotency key
# ======================================================================

def test_18_idempotency_key_returns_same_report():
    """Stessa idempotency_key → stesso report_id (nessuna rigenerazione)."""
    ikey = "idem-test-" + str(uuid.uuid4())
    brief = _make_brief(idempotency_key=ikey)
    service = _fixture_service()

    report1 = service.generate(brief)
    report2 = service.generate(brief)

    assert report1.report_id == report2.report_id


def test_18b_different_idempotency_keys_different_reports():
    """Chiavi diverse → report_id diversi."""
    service = _fixture_service()
    brief1 = _make_brief(idempotency_key="key-aaa")
    brief2 = _make_brief(idempotency_key="key-bbb")

    r1 = service.generate(brief1)
    r2 = service.generate(brief2)
    assert r1.report_id != r2.report_id


# ======================================================================
# TEST 19 — Mission integration minima
# ======================================================================

def test_19_mission_type_local_revenue_scan_exists():
    """MissionType.LOCAL_REVENUE_SCAN è definito e ha il valore atteso."""
    from mercury_foundry.mission.models import MissionType

    assert hasattr(MissionType, "LOCAL_REVENUE_SCAN")
    assert MissionType.LOCAL_REVENUE_SCAN.value == "local_revenue_scan"
    # Verificabile come stringa (str, Enum)
    assert str(MissionType.LOCAL_REVENUE_SCAN) == "MissionType.LOCAL_REVENUE_SCAN"


def test_19b_mission_type_in_enum_values():
    """LOCAL_REVENUE_SCAN compare nella lista dei valori dell'enum."""
    from mercury_foundry.mission.models import MissionType

    values = [m.value for m in MissionType]
    assert "local_revenue_scan" in values


# ======================================================================
# TEST 20 — Nessun pagamento reale
# ======================================================================

def test_20_no_payment_imports_in_service():
    """Il modulo service non importa librerie di pagamento."""
    import importlib
    import sys

    # Carica il modulo source come testo per ispezione statica
    import inspect
    from mercury_foundry.products.local_revenue_scan import service as svc_module

    src = inspect.getsource(svc_module)
    payment_keywords = ("stripe", "paypal", "braintree", "checkout", "invoice", "billing")
    for kw in payment_keywords:
        assert kw not in src.lower(), (
            f"Il modulo service contiene riferimento a '{kw}' (pagamenti non ammessi in V0)"
        )


def test_20b_no_payment_imports_in_cli():
    """Il modulo CLI non importa librerie di pagamento."""
    import inspect
    from mercury_foundry.products.local_revenue_scan import cli as cli_module

    src = inspect.getsource(cli_module)
    payment_keywords = ("stripe", "paypal", "braintree", "checkout", "invoice", "billing")
    for kw in payment_keywords:
        assert kw not in src.lower(), (
            f"Il modulo CLI contiene riferimento a '{kw}' (pagamenti non ammessi in V0)"
        )


# ======================================================================
# TEST aggiuntivo — save_outputs crea i 3 file su disco
# ======================================================================

def test_save_outputs_creates_files(tmp_path):
    """save_outputs crea JSON, Markdown e HTML nella cartella indicata."""
    raw = _valid_raw()
    service = _fixture_service(raw)
    report = service.generate(_make_brief())
    paths = save_outputs(report, tmp_path / "out")

    assert set(paths.keys()) == {"json", "markdown", "html"}
    for fmt, path in paths.items():
        assert path.exists(), f"File {fmt} non creato: {path}"
        assert path.stat().st_size > 0, f"File {fmt} vuoto: {path}"
