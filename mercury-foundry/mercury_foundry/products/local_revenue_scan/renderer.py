"""Rendering del Revenue Scan Report in tre formati.

- render_json(report)     → str  (JSON canonico, UTF-8)
- render_markdown(report) → str  (Markdown leggibile)
- render_html(report)     → str  (HTML autonomo, apribile da browser)
- save_outputs(report, output_dir) → dict[str, Path]

Nessuna libreria esterna aggiuntiva: solo stdlib + html.escape per sicurezza.
"""

from __future__ import annotations

import json
from html import escape as _esc
from pathlib import Path

from mercury_foundry.products.local_revenue_scan.models import ReportStatus, RevenueScanReport


# ======================================================================
# JSON
# ======================================================================

def render_json(report: RevenueScanReport) -> str:
    """Serializza il report come JSON canonico (UTF-8, indentato)."""
    return json.dumps(
        {
            "report_id":                 report.report_id,
            "business_name":             report.business_name,
            "generated_at":              report.generated_at.isoformat(),
            "status":                    report.status.value,
            "executive_summary":         report.executive_summary,
            "overall_score":             report.overall_score,
            "scores": {
                "visibility": report.visibility_score,
                "conversion": report.conversion_score,
                "reputation": report.reputation_score,
                "offer":      report.offer_score,
                "retention":  report.retention_score,
            },
            "top_revenue_leaks":      report.top_revenue_leaks,
            "priority_actions":       report.priority_actions,
            "seven_day_plan":         report.seven_day_plan,
            "ready_to_publish_posts": report.ready_to_publish_posts,
            "promotional_offer":      report.promotional_offer,
            "thirty_day_kpis":        report.thirty_day_kpis,
            "assumptions":            report.assumptions,
            "missing_information":    report.missing_information,
            "confidence_level":       report.confidence_level,
            "human_review_required":  report.human_review_required,
            "estimated_delivery_status": report.estimated_delivery_status,
            "quality_issues":         report.quality_issues,
        },
        ensure_ascii=False,
        indent=2,
    )


# ======================================================================
# Markdown
# ======================================================================

def render_markdown(report: RevenueScanReport) -> str:
    """Produce il report in Markdown leggibile."""
    lines: list[str] = []

    def h(level: int, text: str) -> None:
        lines.append(f"{'#' * level} {text}")
        lines.append("")

    def p(text: str) -> None:
        lines.append(text)
        lines.append("")

    def ul(items: list[str]) -> None:
        if not items:
            lines.append("_Nessun elemento._")
            lines.append("")
            return
        for item in items:
            lines.append(f"- {item}")
        lines.append("")

    def ol(items: list[str]) -> None:
        if not items:
            lines.append("_Nessun elemento._")
            lines.append("")
            return
        for i, item in enumerate(items, 1):
            lines.append(f"{i}. {item}")
        lines.append("")

    h(1, f"Revenue Scan — {report.business_name}")
    p(
        f"**Data:** {report.generated_at.strftime('%d/%m/%Y %H:%M')} UTC  \n"
        f"**Report ID:** `{report.report_id}`  \n"
        f"**Stato:** `{report.status.value}`  \n"
        f"**Confidenza:** {report.confidence_level}/100"
    )

    h(2, "Executive Summary")
    p(report.executive_summary)

    h(2, "Scorecard")
    lines += [
        "| Dimensione      | Punteggio  | Peso |",
        "|-----------------|------------|------|",
        f"| **Complessivo** | **{report.overall_score}/100** | —  |",
        f"| Visibilità      | {report.visibility_score}/100 | 20% |",
        f"| Conversione     | {report.conversion_score}/100 | 25% |",
        f"| Reputazione     | {report.reputation_score}/100 | 20% |",
        f"| Offerta         | {report.offer_score}/100 | 20% |",
        f"| Fidelizzazione  | {report.retention_score}/100 | 15% |",
        "",
    ]

    h(2, "Revenue Leaks Principali")
    ul(report.top_revenue_leaks)

    h(2, "Azioni Prioritarie")
    ol(report.priority_actions)

    h(2, "Piano 7 Giorni")
    ul(report.seven_day_plan)

    h(2, "Contenuti Pronti alla Pubblicazione")
    for i, post in enumerate(report.ready_to_publish_posts, 1):
        h(3, f"Post {i}")
        p(post)

    h(2, "Proposta Promozionale")
    p(report.promotional_offer)

    h(2, "KPI a 30 Giorni")
    ul(report.thirty_day_kpis)

    h(2, "Assunzioni e Limitazioni")
    h(3, "Assunzioni dell'analisi")
    ul(report.assumptions)
    h(3, "Informazioni mancanti")
    ul(report.missing_information)

    if report.quality_issues:
        h(2, "⚠️ Problemi Quality Gate")
        ul(report.quality_issues)

    lines.append("---")
    lines.append("_Mercury Revenue Scan V0 — Documento riservato ad uso interno_")
    return "\n".join(lines)


# ======================================================================
# HTML
# ======================================================================

def _score_color(score: int) -> str:
    if score >= 70:
        return "#16a34a"
    if score >= 45:
        return "#f59e0b"
    return "#dc2626"


def _bar(label: str, score: int, weight: str) -> str:
    c = _score_color(score)
    return (
        f"<tr>"
        f"<td style='padding:.35rem .6rem'>{_esc(label)}</td>"
        f"<td style='padding:.35rem .6rem;width:55%'>"
        f"<div style='background:#e5e7eb;border-radius:4px;height:11px'>"
        f"<div style='background:{c};width:{score}%;height:11px;border-radius:4px'></div>"
        f"</div></td>"
        f"<td style='padding:.35rem .6rem;text-align:right;font-weight:600'>{score}/100</td>"
        f"<td style='padding:.35rem .6rem;color:#6b7280;font-size:.82em'>{weight}</td>"
        f"</tr>"
    )


def _ul_html(items: list[str]) -> str:
    if not items:
        return "<p><em>Nessun elemento.</em></p>"
    inner = "".join(f"<li>{_esc(i)}</li>" for i in items)
    return f"<ul style='margin-left:1.3rem'>{inner}</ul>"


def _ol_html(items: list[str]) -> str:
    if not items:
        return "<p><em>Nessun elemento.</em></p>"
    inner = "".join(f"<li>{_esc(i)}</li>" for i in items)
    return f"<ol style='margin-left:1.3rem'>{inner}</ol>"


def render_html(report: RevenueScanReport) -> str:
    """Produce un HTML autonomo (no dipendenze esterne) apribile da browser."""
    status_bg = "#16a34a" if report.status == ReportStatus.READY else "#dc2626"
    score_c   = _score_color(report.overall_score)

    posts_html = "".join(
        f'<div style="background:#f0f9ff;border-left:3px solid #3b82f6;'
        f'padding:.8rem 1rem;margin-bottom:.8rem;border-radius:0 4px 4px 0">'
        f'<strong>Post {i}</strong><p style="margin-top:.4rem">{_esc(p)}</p></div>'
        for i, p in enumerate(report.ready_to_publish_posts, 1)
    )

    quality_html = ""
    if report.quality_issues:
        quality_html = (
            f'<div style="background:#fff5f5;border:1px solid #fca5a5;'
            f'border-radius:8px;padding:1.5rem 2rem;margin-bottom:1.5rem">'
            f'<h2 style="font-size:1.05rem;font-weight:700;margin-bottom:.8rem;color:#dc2626">'
            f'⚠️ Problemi Quality Gate</h2>'
            f'{_ul_html(report.quality_issues)}</div>'
        )

    def section(title: str, body: str) -> str:
        return (
            f'<div style="background:#fff;border-radius:8px;padding:1.5rem 2rem;'
            f'margin-bottom:1.5rem;box-shadow:0 1px 3px rgba(0,0,0,.07)">'
            f'<h2 style="font-size:1.05rem;font-weight:700;margin-bottom:1rem;'
            f'color:#1e293b;border-bottom:2px solid #f1f5f9;padding-bottom:.5rem">'
            f'{_esc(title)}</h2>{body}</div>'
        )

    miss_html = (
        _ul_html(report.missing_information)
        if report.missing_information
        else "<p><em>Nessuna informazione critica mancante.</em></p>"
    )

    return f"""<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Revenue Scan — {_esc(report.business_name)}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
      background:#f8fafc;color:#1e293b;line-height:1.65;font-size:15px}}
p{{margin-bottom:.6rem}}
li{{margin-bottom:.25rem}}
</style>
</head>
<body>
<!-- Header -->
<div style="background:#1e293b;color:#fff;padding:2rem 2.5rem">
  <h1 style="font-size:1.55rem;margin-bottom:.4rem">
    Revenue Scan — {_esc(report.business_name)}
  </h1>
  <span style="display:inline-block;padding:.2rem .75rem;border-radius:999px;
               font-size:.8rem;font-weight:600;background:{status_bg}">
    {report.status.value.replace("_", " ").title()}
  </span>
  <div style="font-size:.82rem;opacity:.7;margin-top:.6rem">
    Report ID: {_esc(report.report_id)} &nbsp;|&nbsp;
    {report.generated_at.strftime("%d/%m/%Y %H:%M")} UTC &nbsp;|&nbsp;
    Confidenza: {report.confidence_level}/100
  </div>
</div>

<!-- Content -->
<div style="max-width:920px;margin:0 auto;padding:2rem 1.5rem">

{section("Executive Summary",
    f"<p>{_esc(report.executive_summary)}</p>")}

{section("Scorecard",
    f'<div style="margin-bottom:1rem">'
    f'<span style="font-size:2.8rem;font-weight:800;color:{score_c}">'
    f'{report.overall_score}</span>'
    f'<span style="font-size:1rem;color:#64748b"> /100 complessivo</span>'
    f'</div>'
    f'<table style="width:100%;border-collapse:collapse">'
    f'<tbody>'
    f'{_bar("Visibilità",    report.visibility_score,  "20%")}'
    f'{_bar("Conversione",   report.conversion_score,  "25%")}'
    f'{_bar("Reputazione",   report.reputation_score,  "20%")}'
    f'{_bar("Offerta",       report.offer_score,        "20%")}'
    f'{_bar("Fidelizzazione",report.retention_score,   "15%")}'
    f'</tbody></table>')}

{section("Revenue Leaks Principali", _ul_html(report.top_revenue_leaks))}
{section("Azioni Prioritarie",       _ol_html(report.priority_actions))}
{section("Piano 7 Giorni",           _ul_html(report.seven_day_plan))}
{section("Contenuti Pronti alla Pubblicazione", posts_html)}
{section("Proposta Promozionale",
    f"<p>{_esc(report.promotional_offer)}</p>")}
{section("KPI a 30 Giorni",          _ul_html(report.thirty_day_kpis))}
{section("Assunzioni e Limitazioni",
    f'<h3 style="font-size:.9rem;font-weight:600;margin:.6rem 0 .4rem;color:#475569">'
    f'Assunzioni dell\'analisi</h3>'
    f'{_ul_html(report.assumptions)}'
    f'<h3 style="font-size:.9rem;font-weight:600;margin:.8rem 0 .4rem;color:#475569">'
    f'Informazioni mancanti</h3>'
    f'{miss_html}')}

{quality_html}

</div>
<div style="text-align:center;font-size:.78rem;color:#94a3b8;padding:2rem 0">
  Mercury Revenue Scan V0 — Documento riservato ad uso interno
</div>
</body>
</html>"""


# ======================================================================
# Salvataggio output
# ======================================================================

def save_outputs(report: RevenueScanReport, output_dir: Path) -> dict[str, Path]:
    """Salva JSON, Markdown e HTML nella cartella output_dir.

    Crea la cartella se non esiste.
    Ritorna un dict {formato: percorso_file}.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # Slug dal nome attività (sicuro per filesystem)
    slug = (
        report.business_name
        .lower()
        .replace(" ", "_")
        .replace("/", "_")
        [:40]
    )

    paths: dict[str, Path] = {}

    p_json = output_dir / f"{slug}_report.json"
    p_json.write_text(render_json(report), encoding="utf-8")
    paths["json"] = p_json

    p_md = output_dir / f"{slug}_report.md"
    p_md.write_text(render_markdown(report), encoding="utf-8")
    paths["markdown"] = p_md

    p_html = output_dir / f"{slug}_report.html"
    p_html.write_text(render_html(report), encoding="utf-8")
    paths["html"] = p_html

    return paths
