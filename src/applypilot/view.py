"""ApplyPilot Dashboard.

Renders the jobs dashboard as HTML and serves it from a lightweight stdlib
HTTP server. Clicking a job's title or Apply link fires a POST to
``/api/mark-applied`` so the database records the application immediately.

Exposes:
  - render_dashboard_html(): build the full dashboard HTML as a string.
  - generate_dashboard(): write the rendered HTML to disk (legacy).
  - serve_dashboard(): run an HTTP server that renders on each request and
    handles the mark-applied endpoint.
  - open_dashboard(): launch the server and open it in the default browser.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import webbrowser
from datetime import datetime, timezone
from html import escape
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from rich.console import Console

from applypilot.config import (
    APP_DIR,
    list_profiles,
    get_active_profile_name,
    set_active_profile,
    create_profile,
    delete_profile,
    profile_dir,
    profile_files_status,
)
from applypilot.database import get_connection

console = Console()


def render_dashboard_html() -> str:
    """Build the dashboard HTML for the current database state.

    Returns:
        Full HTML document as a string.
    """
    conn = get_connection()

    # Profile data for the profile bar
    active_profile = get_active_profile_name()
    all_profiles = list_profiles()
    profile_options = "".join(
        f'<option value="{escape(p)}" {"selected" if p == active_profile else ""}>{escape(p)}</option>'
        for p in all_profiles
    )
    delete_profiles = [p for p in all_profiles if p != active_profile]
    if delete_profiles:
        delete_profile_options = "".join(
            f'<option value="{escape(p)}">{escape(p)}</option>'
            for p in delete_profiles
        )
    else:
        delete_profile_options = '<option value="">No other profiles available</option>'
    profile_dir_path = escape(str(profile_dir(active_profile)))
    summary_path = profile_dir(active_profile) / "rejection_summary.txt"
    if summary_path.exists():
        rejection_summary = escape(summary_path.read_text(encoding="utf-8"))
    else:
        rejection_summary = "No rejection feedback summary available yet. Run a manual discovery scan to collect feedback."

    # Stats
    total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    ready = conn.execute(
        "SELECT COUNT(*) FROM jobs "
        "WHERE full_description IS NOT NULL AND application_url IS NOT NULL"
    ).fetchone()[0]
    scored = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE fit_score IS NOT NULL"
    ).fetchone()[0]
    high_fit = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE fit_score >= 7"
    ).fetchone()[0]

    # Score distribution
    score_dist: dict[int, int] = {}
    if scored:
        rows = conn.execute(
            "SELECT fit_score, COUNT(*) FROM jobs "
            "WHERE fit_score IS NOT NULL "
            "GROUP BY fit_score ORDER BY fit_score DESC"
        ).fetchall()
        for r in rows:
            score_dist[r[0]] = r[1]

    # Site stats
    site_stats = conn.execute("""
        SELECT site,
               COUNT(*) as total,
               SUM(CASE WHEN fit_score >= 7 THEN 1 ELSE 0 END) as high_fit,
               SUM(CASE WHEN fit_score BETWEEN 5 AND 6 THEN 1 ELSE 0 END) as mid_fit,
               SUM(CASE WHEN fit_score < 5 AND fit_score IS NOT NULL THEN 1 ELSE 0 END) as low_fit,
               SUM(CASE WHEN fit_score IS NULL THEN 1 ELSE 0 END) as unscored,
               ROUND(AVG(fit_score), 1) as avg_score
        FROM jobs GROUP BY site ORDER BY high_fit DESC, total DESC
    """).fetchall()

    # All scored jobs, plus any already-applied jobs regardless of score
    jobs = conn.execute("""
        SELECT url, title, salary, description, location, site, strategy,
               full_description, application_url, detail_error,
               fit_score, score_reasoning, applied_at
        FROM jobs
        WHERE fit_score IS NOT NULL OR applied_at IS NOT NULL
        ORDER BY fit_score DESC, site, title
    """).fetchall()

    # Color map per site
    colors = {
        "RemoteOK": "#10b981", "WelcomeToTheJungle": "#f59e0b",
        "Job Bank Canada": "#3b82f6", "CareerJet Canada": "#8b5cf6",
        "Hacker News Jobs": "#ff6600", "BuiltIn Remote": "#ec4899",
        "TD Bank": "#00a651", "CIBC": "#c41f3e", "RBC": "#003168",
        "indeed": "#2164f3", "linkedin": "#0a66c2",
        "Dice": "#eb1c26", "Glassdoor": "#0caa41",
    }

    # Score distribution bar chart
    score_bars = ""
    max_count = max(score_dist.values()) if score_dist else 1
    for s in range(10, 0, -1):
        count = score_dist.get(s, 0)
        pct = (count / max_count * 100) if max_count else 0
        score_color = "#10b981" if s >= 7 else ("#f59e0b" if s >= 5 else "#ef4444")
        score_bars += f"""
        <div class="score-row">
          <span class="score-label">{s}</span>
          <div class="score-bar-track">
            <div class="score-bar-fill" style="width:{pct}%;background:{score_color}"></div>
          </div>
          <span class="score-count">{count}</span>
        </div>"""

    # Site stats rows
    site_rows = ""
    for s in site_stats:
        site = s["site"] or "?"
        color = colors.get(site, "#6b7280")
        avg = s["avg_score"] or 0
        site_rows += f"""
        <div class="site-row">
          <div class="site-name" style="color:{color}">{escape(site)}</div>
          <div class="site-nums">{s['total']} jobs &middot; {s['high_fit']} strong fit &middot; avg score {avg}</div>
          <div class="bar-track">
            <div class="bar-fill" style="width:{s['high_fit']/max(s['total'],1)*100}%;background:{color}"></div>
            <div class="bar-fill" style="width:{s['mid_fit']/max(s['total'],1)*100}%;background:{color}66"></div>
          </div>
        </div>"""

    # Job cards grouped by score
    job_sections = ""
    if total == 0:
        job_sections = """
        <div class="empty-state">
          <svg style="width: 4rem; height: 4rem; margin-bottom: 1rem; color: #475569; display: block; margin-left: auto; margin-right: auto;" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-3 7h3m-3 4h3m-6-4h.01M9 16h.01"></path>
          </svg>
          <h3>No Jobs Found in Profile</h3>
          <p style="margin-top: 0.5rem;">To get started, switch to the <strong>Profile Configuration</strong> tab above to upload your resume and customize search queries, then run your search.</p>
        </div>"""

    current_score = None
    for j in jobs:
        score = j["fit_score"] or 0
        if score != current_score:
            if current_score is not None:
                job_sections += "</div>"
            score_color = "#10b981" if score >= 7 else ("#f59e0b" if score >= 5 else "#6b7280")
            score_label = {
                10: "Perfect Match", 9: "Excellent Fit", 8: "Strong Fit",
                7: "Good Fit", 6: "Moderate+", 5: "Moderate",
                4: "Low Fit", 3: "Low Fit", 2: "Poor Match", 1: "Poor Match"
            }.get(score, f"Score {score}" if score else "Unscored")
            count_at_score = score_dist.get(score, 0) if score else 0
            count_html = f" ({count_at_score} jobs)" if count_at_score else ""
            job_sections += f"""
            <h2 class="score-header" style="border-color:{score_color}">
              <span class="score-badge" style="background:{score_color}">{score}</span>
              {score_label}{count_html}
            </h2>
            <div class="job-grid">"""
            current_score = score

        title = escape(j["title"] or "Untitled")
        url = escape(j["url"] or "")
        salary = escape(j["salary"] or "")
        location = escape(j["location"] or "")
        site = escape(j["site"] or "")
        site_color = colors.get(j["site"] or "", "#6b7280")
        apply_url = escape(j["application_url"] or "")
        applied = bool(j["applied_at"])

        # Parse keywords and reasoning from score_reasoning
        reasoning_raw = j["score_reasoning"] or ""
        reasoning_lines = reasoning_raw.split("\n")
        keywords = reasoning_lines[0][:120] if reasoning_lines else ""
        reasoning = reasoning_lines[1][:200] if len(reasoning_lines) > 1 else ""

        desc_preview = escape(j["full_description"] or "")[:300]
        full_desc_html = escape(j["full_description"] or "").replace("\n", "<br>")
        desc_len = len(j["full_description"] or "")

        meta_parts = []
        meta_parts.append(
            f'<span class="meta-tag site-tag" style="background:{site_color}33;color:{site_color}">{site}</span>'
        )
        if salary:
            meta_parts.append(f'<span class="meta-tag salary">{salary}</span>')
        if location:
            meta_parts.append(f'<span class="meta-tag location">{location[:40]}</span>')
        if applied:
            meta_parts.append('<span class="meta-tag applied-badge">Applied ✓</span>')
        meta_html = " ".join(meta_parts)

        apply_html = ""
        if apply_url:
            apply_html = (
                f'<a href="{apply_url}" class="apply-link" target="_blank" '
                f'rel="noopener" onclick="markApplied(this)">Apply</a>'
            )

        pill_color = "#10b981" if score >= 7 else ("#f59e0b" if score >= 5 else "#6b7280")
        job_sections += f"""
        <div class="job-card{' applied' if applied else ''}" data-score="{score}" data-site="{escape(j['site'] or '')}" data-location="{location.lower()}" data-url="{url}" data-applied="{1 if applied else 0}">
          <div class="card-header">
            <span class="score-pill" style="background:{pill_color}">{score}</span>
            <a href="{url}" class="job-title" target="_blank" rel="noopener" onclick="markApplied(this)">{title}</a>
          </div>
          <div class="meta-row">{meta_html}</div>
          {f'<div class="keywords-row">{escape(keywords)}</div>' if keywords else ''}
          {f'<div class="reasoning-row">{escape(reasoning)}</div>' if reasoning else ''}
          <p class="desc-preview">{desc_preview}...</p>
          {"<details class='full-desc-details'><summary class='expand-btn'>Full Description (" + f'{desc_len:,}' + " chars)</summary><div class='full-desc'>" + full_desc_html + "</div></details>" if j["full_description"] else ""}
          <div class="card-footer">{apply_html}</div>
        </div>"""

    if current_score is not None:
        job_sections += "</div>"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ApplyPilot Dashboard</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif; background: #0f172a; color: #e2e8f0; padding: 2rem; }}

  h1 {{ font-size: 1.8rem; font-weight: 700; margin-bottom: 0.5rem; }}
  .subtitle {{ color: #94a3b8; margin-bottom: 2rem; }}

  /* Tabs CSS */
  .tabs {{ display: flex; gap: 0.5rem; border-bottom: 2px solid #334155; margin-bottom: 1.75rem; }}
  .tab-btn {{ background: none; border: none; color: #94a3b8; padding: 0.75rem 1.25rem; font-size: 0.95rem; font-weight: 600; cursor: pointer; transition: all 0.15s; border-bottom: 3px solid transparent; margin-bottom: -2px; outline: none; }}
  .tab-btn:hover {{ color: #e2e8f0; }}
  .tab-btn.active {{ color: #60a5fa; border-bottom-color: #60a5fa; }}
  
  .tab-content.hidden {{ display: none !important; }}

  /* Summary cards */
  .summary {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 1rem; margin-bottom: 2.5rem; }}
  .stat-card {{ background: #1e293b; border-radius: 12px; padding: 1.25rem; }}
  .stat-num {{ font-size: 2rem; font-weight: 700; }}
  .stat-label {{ color: #94a3b8; font-size: 0.85rem; margin-top: 0.25rem; }}
  .stat-ok .stat-num {{ color: #10b981; }}
  .stat-scored .stat-num {{ color: #60a5fa; }}
  .stat-high .stat-num {{ color: #f59e0b; }}
  .stat-total .stat-num {{ color: #e2e8f0; }}

  /* Filters */
  .filters {{ background: #1e293b; border-radius: 12px; padding: 1.25rem; margin-bottom: 2rem; display: flex; gap: 1rem; flex-wrap: wrap; align-items: center; }}
  .filter-label {{ color: #94a3b8; font-size: 0.85rem; font-weight: 600; }}
  .filter-btn {{ background: #334155; border: none; color: #94a3b8; padding: 0.4rem 0.8rem; border-radius: 6px; cursor: pointer; font-size: 0.8rem; transition: all 0.15s; }}
  .filter-btn:hover {{ background: #475569; color: #e2e8f0; }}
  .filter-btn.active {{ background: #60a5fa; color: #0f172a; font-weight: 600; }}
  .search-input {{ background: #334155; border: 1px solid #475569; color: #e2e8f0; padding: 0.4rem 0.8rem; border-radius: 6px; font-size: 0.8rem; width: 200px; }}
  .search-input::placeholder {{ color: #64748b; }}

  /* Score distribution */
  .score-section {{ display: grid; grid-template-columns: 1fr 1fr; gap: 1.5rem; margin-bottom: 2.5rem; }}
  .score-dist {{ background: #1e293b; border-radius: 12px; padding: 1.5rem; }}
  .score-dist h3 {{ font-size: 1rem; margin-bottom: 1rem; color: #94a3b8; }}
  .score-row {{ display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.4rem; }}
  .score-label {{ width: 1.5rem; text-align: right; font-size: 0.85rem; font-weight: 600; }}
  .score-bar-track {{ flex: 1; height: 14px; background: #334155; border-radius: 4px; overflow: hidden; }}
  .score-bar-fill {{ height: 100%; border-radius: 4px; transition: width 0.3s; }}
  .score-count {{ width: 2.5rem; font-size: 0.8rem; color: #94a3b8; }}

  /* Site bars */
  .sites-section {{ background: #1e293b; border-radius: 12px; padding: 1.5rem; }}
  .sites-section h3 {{ font-size: 1rem; margin-bottom: 1rem; color: #94a3b8; }}
  .site-row {{ margin-bottom: 0.8rem; }}
  .site-name {{ font-weight: 600; font-size: 0.9rem; }}
  .site-nums {{ color: #94a3b8; font-size: 0.75rem; margin: 0.15rem 0; }}
  .bar-track {{ height: 8px; background: #334155; border-radius: 4px; display: flex; overflow: hidden; }}
  .bar-fill {{ height: 100%; transition: width 0.3s; }}

  /* Score group headers */
  .score-header {{ font-size: 1.2rem; font-weight: 600; margin: 2.5rem 0 1rem; padding-bottom: 0.5rem; border-bottom: 3px solid; display: flex; align-items: center; gap: 0.75rem; }}
  .score-badge {{ display: inline-flex; align-items: center; justify-content: center; width: 2rem; height: 2rem; border-radius: 8px; color: #0f172a; font-weight: 700; font-size: 1rem; }}

  /* Job grid */
  .job-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(380px, 1fr)); gap: 1rem; }}

  .job-card {{ background: #1e293b; border-radius: 10px; padding: 1rem; border-left: 3px solid #334155; transition: all 0.15s; }}
  .job-card:hover {{ transform: translateY(-2px); box-shadow: 0 4px 12px #00000044; }}
  .job-card[data-score="9"], .job-card[data-score="10"] {{ border-left-color: #10b981; }}
  .job-card[data-score="8"] {{ border-left-color: #34d399; }}
  .job-card[data-score="7"] {{ border-left-color: #60a5fa; }}
  .job-card[data-score="6"] {{ border-left-color: #f59e0b; }}
  .job-card[data-score="5"] {{ border-left-color: #f59e0b88; }}

  .card-header {{ display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.5rem; }}
  .score-pill {{ display: inline-flex; align-items: center; justify-content: center; min-width: 1.6rem; height: 1.6rem; border-radius: 6px; color: #0f172a; font-weight: 700; font-size: 0.8rem; flex-shrink: 0; }}

  .job-title {{ color: #e2e8f0; text-decoration: none; font-weight: 600; font-size: 0.95rem; }}
  .job-title:hover {{ color: #60a5fa; }}

  .meta-row {{ display: flex; flex-wrap: wrap; gap: 0.4rem; margin-bottom: 0.4rem; }}
  .meta-tag {{ font-size: 0.72rem; padding: 0.15rem 0.5rem; border-radius: 4px; background: #334155; color: #94a3b8; }}
  .meta-tag.salary {{ background: #064e3b; color: #6ee7b7; }}
  .meta-tag.location {{ background: #1e3a5f; color: #93c5fd; }}
  .meta-tag.applied-badge {{ background: #064e3b; color: #6ee7b7; font-weight: 600; }}

  /* Applied state -- dim card + strike title */
  .job-card.applied {{ opacity: 0.55; border-left-color: #6ee7b7 !important; }}
  .job-card.applied .job-title {{ text-decoration: line-through; color: #94a3b8; }}
  .job-card.applied .apply-link {{ background: #064e3b; color: #6ee7b7; border-color: #6ee7b766; }}

  .keywords-row {{ font-size: 0.75rem; color: #10b981; margin-bottom: 0.3rem; line-height: 1.4; }}
  .reasoning-row {{ font-size: 0.75rem; color: #94a3b8; margin-bottom: 0.5rem; font-style: italic; line-height: 1.4; }}

  .desc-preview {{ font-size: 0.8rem; color: #64748b; line-height: 1.5; margin-bottom: 0.75rem; max-height: 3.6em; overflow: hidden; }}

  .card-footer {{ display: flex; justify-content: flex-end; }}
  .apply-link {{ font-size: 0.8rem; color: #60a5fa; text-decoration: none; padding: 0.3rem 0.8rem; border: 1px solid #60a5fa33; border-radius: 6px; font-weight: 500; }}
  .apply-link:hover {{ background: #60a5fa22; }}

  /* Expandable full description */
  .full-desc-details {{ margin-bottom: 0.75rem; }}
  .expand-btn {{ font-size: 0.8rem; color: #60a5fa; cursor: pointer; list-style: none; padding: 0.3rem 0; }}
  .expand-btn::-webkit-details-marker {{ display: none; }}
  .expand-btn:hover {{ color: #93c5fd; }}
  .full-desc {{ font-size: 0.8rem; color: #cbd5e1; line-height: 1.6; margin-top: 0.5rem; padding: 0.75rem; background: #0f172a; border-radius: 8px; max-height: 400px; overflow-y: auto; white-space: pre-wrap; word-break: break-word; }}

  .hidden {{ display: none !important; }}
  .job-count {{ color: #94a3b8; font-size: 0.85rem; margin-bottom: 1rem; }}

  /* Profile bar */
  .profile-bar {{ background: #1e293b; border-radius: 12px; padding: 0.85rem 1.25rem; margin-bottom: 1.75rem; display: flex; flex-wrap: wrap; align-items: center; gap: 0.75rem; border: 1px solid #334155; }}
  .profile-label {{ color: #94a3b8; font-size: 0.82rem; font-weight: 600; white-space: nowrap; }}
  .profile-select {{ background: #0f172a; border: 1px solid #334155; color: #e2e8f0; padding: 0.35rem 0.7rem; border-radius: 6px; font-size: 0.85rem; cursor: pointer; }}
  .profile-btn {{ background: #334155; border: none; color: #94a3b8; padding: 0.35rem 0.9rem; border-radius: 6px; cursor: pointer; font-size: 0.8rem; transition: all 0.15s; white-space: nowrap; }}
  .profile-btn:hover {{ background: #475569; color: #e2e8f0; }}
  .profile-btn.primary {{ background: #3b82f6; color: #fff; }}
  .profile-btn.primary:hover {{ background: #2563eb; }}
  .profile-btn.danger {{ background: #7f1d1d; color: #fca5a5; }}
  .profile-btn.danger:hover {{ background: #991b1b; }}
  .profile-path {{ color: #475569; font-size: 0.72rem; font-family: monospace; margin-left: auto; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 380px; }}
  .profile-modal-bg {{ display:none; position:fixed; inset:0; background:#00000088; z-index:100; align-items:center; justify-content:center; }}
  .profile-modal-bg.open {{ display:flex; }}
  .profile-modal {{ background:#1e293b; border-radius:12px; padding:1.5rem; min-width:320px; max-width:440px; border:1px solid #334155; }}
  .profile-modal h3 {{ margin-bottom:1rem; font-size:1rem; }}
  .profile-modal input, .profile-modal select {{ width:100%; background:#0f172a; border:1px solid #334155; color:#e2e8f0; padding:0.5rem 0.75rem; border-radius:6px; font-size:0.85rem; margin-bottom:0.75rem; box-sizing:border-box; }}
  .profile-modal .modal-actions {{ display:flex; gap:0.5rem; justify-content:flex-end; margin-top:0.5rem; }}
  
  /* Toast Notifications */
  .toast-container {{ position: fixed; bottom: 20px; right: 20px; z-index: 1000; display: flex; flex-direction: column; gap: 10px; pointer-events: none; }}
  .toast {{
    background: #1e293b;
    border: 1px solid #334155;
    color: #e2e8f0;
    padding: 0.75rem 1.25rem;
    border-radius: 8px;
    font-size: 0.85rem;
    font-weight: 500;
    box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.3), 0 4px 6px -4px rgba(0, 0, 0, 0.3);
    display: flex;
    align-items: center;
    gap: 0.5rem;
    transform: translateY(50px);
    opacity: 0;
    transition: all 0.3s cubic-bezier(0.16, 1, 0.3, 1);
    pointer-events: auto;
    min-width: 250px;
    max-width: 380px;
  }}
  .toast.show {{ transform: translateY(0); opacity: 1; }}
  .toast.success {{ border-color: #10b981; background: #064e3b; color: #a7f3d0; }}
  .toast.error {{ border-color: #ef4444; background: #7f1d1d; color: #fca5a5; }}
  .toast.info {{ border-color: #3b82f6; background: #1e3a8a; color: #bfdbfe; }}
  
  .control-panel {{ background: #1e293b; border-radius: 12px; padding: 1.25rem 1.5rem; margin-bottom: 1.75rem; border: 1px solid #334155; }}
  @keyframes pulse {{
    0% {{ opacity: 0.6; }}
    50% {{ opacity: 1; }}
    100% {{ opacity: 0.6; }}
  }}

  /* Config Editor CSS */
  .config-grid {{ display: grid; grid-template-columns: 1.2fr 1fr; gap: 1.5rem; margin-top: 1rem; }}
  .config-card {{ background: #1e293b; border-radius: 12px; padding: 1.5rem; border: 1px solid #334155; display: flex; flex-direction: column; gap: 0.75rem; }}
  .config-card h3 {{ font-size: 1.1rem; color: #94a3b8; display: flex; align-items: center; justify-content: space-between; }}
  .config-editor {{ width: 100%; height: 350px; background: #0f172a; border: 1px solid #334155; color: #e2e8f0; font-family: monospace; font-size: 0.85rem; padding: 0.75rem; border-radius: 8px; resize: vertical; outline: none; }}
  .config-editor:focus {{ border-color: #60a5fa; }}
  
  .resume-section {{ display: flex; flex-direction: column; gap: 0.75rem; }}
  .resume-status {{ display: flex; align-items: center; justify-content: space-between; padding: 0.75rem 1rem; border-radius: 8px; font-size: 0.9rem; font-weight: 500; }}
  .resume-status.present {{ background: #064e3b; color: #6ee7b7; border: 1px solid #065f46; }}
  .resume-status.missing {{ background: #7f1d1d; color: #fca5a5; border: 1px solid #991b1b; }}
  .upload-area {{ border: 2px dashed #475569; border-radius: 8px; padding: 2rem 1.5rem; text-align: center; cursor: pointer; transition: all 0.15s; }}
  .upload-area:hover {{ border-color: #60a5fa; background: #33415533; }}
  .file-input {{ display: none; }}
  .upload-label {{ font-size: 0.85rem; color: #94a3b8; cursor: pointer; margin-top: 0.5rem; }}
  .upload-label strong {{ color: #60a5fa; }}
  
  .empty-state {{ text-align: center; padding: 4rem 2rem; background: #1e293b; border: 1px dashed #334155; border-radius: 12px; margin-top: 1rem; color: #94a3b8; }}
  .empty-state h3 {{ color: #cbd5e1; font-size: 1.25rem; margin-bottom: 0.5rem; }}
  .empty-state p {{ font-size: 0.9rem; max-width: 450px; margin: 0 auto; line-height: 1.5; }}

  @media (max-width: 768px) {{
    .summary {{ grid-template-columns: repeat(2, 1fr); }}
    .score-section {{ grid-template-columns: 1fr; }}
    .job-grid {{ grid-template-columns: 1fr; }}
    .config-grid {{ grid-template-columns: 1fr; }}
    body {{ padding: 1rem; }}
    .profile-path {{ display: none; }}
  }}
</style>
</head>
<body>

<!-- Profile bar -->
<div class="profile-bar">
  <span class="profile-label">Profile:</span>
  <select class="profile-select" id="profile-select" onchange="switchProfile(this.value)">
    {profile_options}
  </select>
  <button class="profile-btn primary" onclick="openCreateModal()">+ New</button>
  <button class="profile-btn danger" onclick="openDeleteModal()">Delete</button>
  <span class="profile-path" title="{profile_dir_path}">{profile_dir_path}</span>
</div>

<!-- Create profile modal -->
<div class="profile-modal-bg" id="create-modal">
  <div class="profile-modal">
    <h3>Create New Profile</h3>
    <label style="font-size:0.8rem;color:#94a3b8;display:block;margin-bottom:0.3rem">Name (letters, digits, - _ only)</label>
    <input type="text" id="new-profile-name" placeholder="e.g. frontend-jobs" maxlength="40">
    <label style="font-size:0.8rem;color:#94a3b8;display:block;margin-bottom:0.3rem">Clone files from (optional)</label>
    <select id="clone-from-select">
      <option value="">— start empty —</option>
      {profile_options}
    </select>
    <div class="profile-msg" id="create-msg"></div>
    <div class="modal-actions">
      <button class="profile-btn" onclick="closeCreateModal()">Cancel</button>
      <button class="profile-btn primary" onclick="submitCreate()">Create</button>
    </div>
  </div>
</div>

<!-- Delete profile modal -->
<div class="profile-modal-bg" id="delete-modal">
  <div class="profile-modal">
    <h3>Delete Profile</h3>
    <label style="font-size:0.8rem;color:#94a3b8;display:block;margin-bottom:0.3rem">Select profile to delete (cannot delete active profile)</label>
    <select id="delete-profile-select">
      {delete_profile_options}
    </select>
    <div class="profile-msg" id="delete-msg" style="color:#ef4444;font-size:0.8rem;margin-bottom:0.75rem"></div>
    <div class="modal-actions">
      <button class="profile-btn" onclick="closeDeleteModal()">Cancel</button>
      <button class="profile-btn danger" onclick="submitDelete()">Delete</button>
    </div>
  </div>
</div>

<!-- Custom Confirm Modal -->
<div class="profile-modal-bg" id="confirm-modal">
  <div class="profile-modal">
    <h3 id="confirm-modal-title">Confirm Action</h3>
    <p id="confirm-modal-text" style="font-size:0.85rem; color:#cbd5e1; line-height:1.5; margin-bottom:1.25rem;"></p>
    <div class="modal-actions">
      <button class="profile-btn" id="confirm-btn-cancel">Cancel</button>
      <button class="profile-btn primary" id="confirm-btn-ok">Confirm</button>
    </div>
  </div>
</div>

<div class="toast-container" id="toast-container"></div>

<h1>ApplyPilot Dashboard</h1>
<p class="subtitle">{active_profile} &middot; {total} jobs &middot; {scored} scored &middot; {high_fit} strong matches (7+)</p>

<!-- TABS NAVIGATION -->
<div class="tabs">
  <button class="tab-btn active" onclick="switchTab('jobs')">Jobs Dashboard</button>
  <button class="tab-btn" onclick="switchTab('config')">Profile Configuration</button>
</div>

<!-- TAB 1: JOBS DASHBOARD -->
<div id="tab-jobs" class="tab-content">
  <!-- Discovery panel -->
  <div class="control-panel">
    <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:1rem">
      <div>
        <h3 style="margin:0; font-size:1.15rem; color:#cbd5e1">Job Discovery Agent</h3>
        <p style="margin:0.25rem 0 0; font-size:0.8rem; color:#94a3b8">Scan job boards using your searches.yaml and score results against your resume</p>
      </div>
      <div style="display:flex; align-items:center; gap:0.75rem; flex-wrap:wrap">
        <div style="display:flex; align-items:center; gap:0.35rem">
          <label for="discover-target" style="font-size:0.8rem; color:#94a3b8">Target:</label>
          <input type="number" id="discover-target" value="10" min="1" max="50" style="width:50px; background:#0f172a; border:1px solid #334155; color:#fff; padding:0.25rem 0.5rem; border-radius:6px; font-size:0.85rem">
        </div>
        <div style="display:flex; align-items:center; gap:0.35rem">
          <label for="discover-min-score" style="font-size:0.8rem; color:#94a3b8">Min Score:</label>
          <select id="discover-min-score" style="background:#0f172a; border:1px solid #334155; color:#fff; padding:0.25rem 0.5rem; border-radius:6px; font-size:0.85rem">
            <option value="5">5+ Moderate</option>
            <option value="6">6+ Moderate+</option>
            <option value="7" selected>7+ Good Fit</option>
            <option value="8">8+ Strong Fit</option>
            <option value="9">9+ Excellent</option>
          </select>
        </div>
        <button class="profile-btn primary" id="discover-btn" onclick="startDiscovery()" style="background:#3b82f6; display:flex; align-items:center; gap:0.4rem">
          <svg style="width:0.9rem; height:0.9rem" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"></path>
          </svg>
          Discover Jobs
        </button>
      </div>
    </div>
    
    <div id="discovery-progress-container" style="display:none; margin-top:1rem; border-top:1px solid #334155; padding-top:1rem">
      <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:0.4rem">
        <div style="display:flex; align-items:center; gap:0.5rem">
          <span id="discovery-status-text" style="font-size:0.82rem; color:#60a5fa">Starting search...</span>
          <button class="profile-btn delete" id="discovery-cancel-btn" onclick="cancelDiscovery()" style="background:#ef4444; border:none; padding:0.2rem 0.5rem; font-size:0.75rem; display:flex; align-items:center; gap:0.25rem">
            Cancel
          </button>
        </div>
        <span id="discovery-timer" style="font-size:0.82rem; color:#64748b">00:00</span>
      </div>
      <div id="discovery-logs" style="width:100%; height:140px; background:#020617; border:1px solid #1e293b; border-radius:6px; font-family:monospace; font-size:0.8rem; color:#94a3b8; padding:0.5rem; overflow-y:auto; white-space:pre-wrap; line-height:1.4"></div>
    </div>
  </div>

  <div class="summary">
    <div class="stat-card stat-total"><div class="stat-num">{total}</div><div class="stat-label">Total Jobs</div></div>
    <div class="stat-card stat-ok"><div class="stat-num">{ready}</div><div class="stat-label">Ready (desc + URL)</div></div>
    <div class="stat-card stat-scored"><div class="stat-num">{scored}</div><div class="stat-label">Scored by LLM</div></div>
    <div class="stat-card stat-high"><div class="stat-num">{high_fit}</div><div class="stat-label">Strong Fit (7+)</div></div>
  </div>

  <div class="filters">
    <span class="filter-label">Score:</span>
    <button class="filter-btn active" onclick="filterScore(event, 0)">All 5+</button>
    <button class="filter-btn" onclick="filterScore(event, 7)">7+ Strong</button>
    <button class="filter-btn" onclick="filterScore(event, 8)">8+ Excellent</button>
    <button class="filter-btn" onclick="filterScore(event, 9)">9+ Perfect</button>
    <span class="filter-label" style="margin-left:1rem">Applied:</span>
    <button class="filter-btn active" id="applied-toggle" onclick="toggleApplied(event)">Show</button>
    <span class="filter-label" style="margin-left:1rem">Search:</span>
    <input type="text" class="search-input" placeholder="Filter by title, site..." oninput="filterText(this.value)">
  </div>

  <div class="score-section">
    <div class="score-dist">
      <h3>Score Distribution</h3>
      {score_bars}
    </div>
    <div class="sites-section">
      <h3>By Source</h3>
      {site_rows}
    </div>
  </div>

  <div id="job-count" class="job-count"></div>
  {job_sections}
</div>

<!-- TAB 2: PROFILE CONFIGURATION -->
<div id="tab-config" class="tab-content hidden">
  <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:1.5rem">
    <h2>Configure Profile: <span id="current-profile-title" style="color:#60a5fa">{active_profile}</span></h2>
    <div style="display:flex; gap:0.5rem">
      <button class="profile-btn" id="generate-btn" onclick="autoGenerateConfig()" style="background:#0f172a; border:1px solid #3b82f6; color:#60a5fa">⚡ Auto-Generate from Resume</button>
      <button class="profile-btn primary" id="save-all-btn" onclick="saveAllConfig()">Save All Changes</button>
    </div>
  </div>
  
  <div class="config-grid">
    <!-- Left Column: Editors -->
    <div style="display:flex; flex-direction:column; gap:1.5rem">
      <div class="config-card">
        <h3>
          <span>Profile Data (profile.json)</span>
          <span style="font-size:0.75rem; font-weight:normal; color:#64748b" id="profile-status-badge"></span>
        </h3>
        <textarea id="profile-json-editor" class="config-editor" placeholder="Loading profile.json..."></textarea>
      </div>
      
      <div class="config-card">
        <h3>
          <span>Search Queries (searches.yaml)</span>
          <span style="font-size:0.75rem; font-weight:normal; color:#64748b" id="searches-status-badge"></span>
        </h3>
        <textarea id="searches-yaml-editor" class="config-editor" placeholder="Loading searches.yaml..."></textarea>
      </div>
    </div>
    
    <!-- Right Column: Resume Upload -->
    <div style="display:flex; flex-direction:column; gap:1.5rem">
      <div class="config-card">
        <h3>Resume Document</h3>
        <div class="resume-section">
          <div id="resume-pdf-status" class="resume-status missing">
            <span>resume.pdf</span>
            <div style="display:flex; align-items:center; gap:0.5rem">
              <span id="resume-pdf-badge">Missing</span>
              <button class="profile-btn danger" id="delete-pdf-btn" onclick="deleteResumeFile('pdf')" style="padding:0.2rem 0.5rem; font-size:0.75rem; display:none">Delete</button>
            </div>
          </div>
          
          <div id="resume-txt-status" class="resume-status missing">
            <span>resume.txt</span>
            <div style="display:flex; align-items:center; gap:0.5rem">
              <span id="resume-txt-badge">Missing</span>
              <button class="profile-btn danger" id="delete-txt-btn" onclick="deleteResumeFile('txt')" style="padding:0.2rem 0.5rem; font-size:0.75rem; display:none">Delete</button>
            </div>
          </div>
          
          <div class="upload-area" onclick="document.getElementById('resume-file-input').click()">
            <input type="file" id="resume-file-input" class="file-input" accept=".pdf,.txt" onchange="handleResumeUpload(this)">
            <svg style="width:2.5rem; height:2.5rem; display:block; margin:0 auto 0.5rem; color:#64748b" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12"></path>
            </svg>
            <p class="upload-label">Drag & drop or <strong>click to upload</strong> resume.pdf or resume.txt</p>
          </div>
        </div>
      </div>

      <!-- Rejection Feedback Card -->
      <div class="config-card" id="rejection-feedback-card">
        <h3>Rejection Reasons Summary</h3>
        <p style="font-size:0.8rem; color:#94a3b8; line-height:1.4">Here is a summary of why recent jobs were rejected, based on 10 random rejections from manual discovery:</p>
        <div id="rejection-summary-text" style="font-size:0.82rem; color:#cbd5e1; background:#0f172a; padding:0.75rem; border-radius:6px; border:1px solid #334155; margin-top:0.5rem; white-space:pre-wrap; line-height:1.4">{rejection_summary}</div>
      </div>
    </div>
  </div>
</div>

<script>
// Custom Toast alert system
function showToast(message, type = 'info') {{
  const container = document.getElementById('toast-container');
  if (!container) return;
  
  const toast = document.createElement('div');
  toast.className = 'toast ' + type;
  
  let icon = 'ℹ️';
  if (type === 'success') icon = '✅';
  if (type === 'error') icon = '❌';
  
  toast.innerHTML = `<span style="font-size:1rem">${{icon}}</span> <span style="flex-grow:1">${{message}}</span>`;
  container.appendChild(toast);
  
  setTimeout(() => toast.classList.add('show'), 50);
  
  setTimeout(() => {{
    toast.classList.remove('show');
    setTimeout(() => toast.remove(), 300);
  }}, 4000);
}}

// Override global window.alert with Toast
window.alert = function(message) {{
  const lower = message.toLowerCase();
  let type = 'info';
  if (lower.includes('success') || lower.includes('complete') || lower.includes('uploaded') || lower.includes('saved')) {{
    type = 'success';
  }} else if (lower.includes('failed') || lower.includes('error') || lower.includes('invalid') || lower.includes('cannot') || lower.includes('missing')) {{
    type = 'error';
  }}
  showToast(message, type);
}};

// Custom Confirm modal replacement
function showConfirm(message, title = 'Confirm') {{
  return new Promise((resolve) => {{
    const bg = document.getElementById('confirm-modal');
    const textEl = document.getElementById('confirm-modal-text');
    const titleEl = document.getElementById('confirm-modal-title');
    const okBtn = document.getElementById('confirm-btn-ok');
    const cancelBtn = document.getElementById('confirm-btn-cancel');
    
    if (!bg || !textEl) {{
      resolve(confirm(message));
      return;
    }}
    
    titleEl.textContent = title;
    textEl.innerHTML = message.replace(/\\n/g, '<br>');
    bg.classList.add('open');
    
    const onCancel = () => {{
      bg.classList.remove('open');
      resolve(false);
      cleanup();
    }};
    
    const onOk = () => {{
      bg.classList.remove('open');
      resolve(true);
      cleanup();
    }};
    
    const cleanup = () => {{
      cancelBtn.removeEventListener('click', onCancel);
      okBtn.removeEventListener('click', onOk);
      bg.removeEventListener('click', onBgClick);
    }};
    
    const onBgClick = (e) => {{
      if (e.target === bg) onCancel();
    }};
    
    cancelBtn.addEventListener('click', onCancel);
    okBtn.addEventListener('click', onOk);
    bg.addEventListener('click', onBgClick);
  }});
}}

let minScore = 0;
let searchText = '';
let hideApplied = false;
let activeTab = 'jobs';

// Default empty configurations for new profiles
const defaultProfileTemplate = {{
  "personal": {{
    "full_name": "YOUR_LEGAL_NAME",
    "preferred_name": "YOUR_PREFERRED_NAME",
    "email": "your.email@example.com",
    "password": "YOUR_JOB_SITE_PASSWORD",
    "phone": "555-123-4567",
    "address": "123 Main St",
    "city": "Your City",
    "province_state": "Your State/Province",
    "country": "Your Country",
    "postal_code": "A1B 2C3",
    "linkedin_url": "https://www.linkedin.com/in/yourprofile",
    "github_url": "https://github.com/yourusername",
    "portfolio_url": "",
    "website_url": ""
  }},
  "work_authorization": {{
    "legally_authorized_to_work": "Yes",
    "require_sponsorship": "No",
    "work_permit_type": ""
  }},
  "availability": {{
    "earliest_start_date": "Immediately",
    "available_for_full_time": "Yes",
    "available_for_contract": "No"
  }},
  "compensation": {{
    "salary_expectation": "",
    "salary_currency": "USD",
    "salary_range_min": "",
    "salary_range_max": "",
    "currency_conversion_note": ""
  }},
  "experience": {{
    "years_of_experience_total": "",
    "education_level": "",
    "current_job_title": "",
    "current_company": "",
    "target_role": ""
  }},
  "skills_boundary": {{
    "languages": [],
    "frameworks": [],
    "devops": [],
    "databases": [],
    "tools": []
  }},
  "resume_facts": {{
    "preserved_companies": [],
    "preserved_projects": [],
    "preserved_school": "",
    "real_metrics": []
  }},
  "eeo_voluntary": {{
    "gender": "Decline to self-identify",
    "race_ethnicity": "Decline to self-identify",
    "veteran_status": "I am not a protected veteran",
    "disability_status": "I do not wish to answer"
  }}
}};

const defaultSearchesTemplate = `queries:
  - query: "software engineer"
    tier: 1
  - query: "python developer"
    tier: 2
`;

function switchTab(tabId) {{
  activeTab = tabId;
  document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(content => content.classList.add('hidden'));
  
  const activeBtn = Array.from(document.querySelectorAll('.tab-btn')).find(btn => btn.textContent.toLowerCase().includes(tabId === 'config' ? 'configuration' : 'dashboard'));
  if (activeBtn) activeBtn.classList.add('active');
  
  const content = document.getElementById('tab-' + tabId);
  if (content) content.classList.remove('hidden');
  
  if (tabId === 'config') {{
    loadConfigData();
  }}
}}

function loadConfigData() {{
  const profileName = document.getElementById('profile-select').value;
  document.getElementById('current-profile-title').textContent = profileName;
  
  const jsonEditor = document.getElementById('profile-json-editor');
  const yamlEditor = document.getElementById('searches-yaml-editor');
  jsonEditor.value = "Loading profile.json...";
  yamlEditor.value = "Loading searches.yaml...";
  
  fetch('/api/config/get')
    .then(r => r.json())
    .then(data => {{
      // Profile json
      if (data.profile_exists) {{
        jsonEditor.value = data.profile_data;
        document.getElementById('profile-status-badge').textContent = 'Saved on disk';
      }} else {{
        jsonEditor.value = JSON.stringify(defaultProfileTemplate, null, 2);
        document.getElementById('profile-status-badge').textContent = 'Unsaved (showing template)';
      }}
      
      // Searches yaml
      if (data.searches_exists) {{
        yamlEditor.value = data.searches_data;
        document.getElementById('searches-status-badge').textContent = 'Saved on disk';
      }} else {{
        yamlEditor.value = defaultSearchesTemplate;
        document.getElementById('searches-status-badge').textContent = 'Unsaved (showing defaults)';
      }}
      
      updateResumeStatus('pdf', data.resume_pdf_exists);
      updateResumeStatus('txt', data.resume_txt_exists);
    }})
    .catch(err => {{
      alert("Error loading config files from server.");
    }});
}}

function updateResumeStatus(type, exists) {{
  const statusDiv = document.getElementById('resume-' + type + '-status');
  const badgeSpan = document.getElementById('resume-' + type + '-badge');
  const deleteBtn = document.getElementById('delete-' + type + '-btn');
  if (exists) {{
    statusDiv.className = 'resume-status present';
    badgeSpan.textContent = 'Present';
    if (deleteBtn) deleteBtn.style.display = 'inline-block';
  }} else {{
    statusDiv.className = 'resume-status missing';
    badgeSpan.textContent = 'Missing';
    if (deleteBtn) deleteBtn.style.display = 'none';
  }}
}}

async function deleteResumeFile(type) {{
  if (!await showConfirm("Are you sure you want to delete your resume." + type + " file?")) return;
  
  fetch('/api/resume/delete', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{type: type}})
  }})
  .then(r => r.json())
  .then(data => {{
    if (data.ok) {{
      alert("Deleted resume." + type + " successfully!");
      loadConfigData();
    }} else {{
      alert("Delete failed: " + (data.error || 'unknown error'));
    }}
  }})
  .catch(err => {{
    alert("Network error: failed to delete resume file.");
  }});
}}

async function autoGenerateConfig() {{
  if (!await showConfirm("Are you sure? This will call the local AI model to parse your uploaded resume text and overwrite your current profile.json and searches.yaml settings.")) return;
  
  const genBtn = document.getElementById('generate-btn');
  genBtn.disabled = true;
  genBtn.textContent = '⚡ Parsing & Generating...';
  
  fetch('/api/config/generate', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}}
  }})
  .then(r => r.json())
  .then(data => {{
    genBtn.disabled = false;
    genBtn.textContent = '⚡ Auto-Generate from Resume';
    if (data.ok) {{
      alert("Profile and searches successfully generated from resume using AI!");
      loadConfigData();
    }} else {{
      alert("Generation failed: " + (data.error || 'unknown error'));
    }}
  }})
  .catch(err => {{
    genBtn.disabled = false;
    genBtn.textContent = '⚡ Auto-Generate from Resume';
    alert("Network error: failed to trigger generation.");
  }});
}}

function saveAllConfig() {{
  const jsonEditor = document.getElementById('profile-json-editor');
  const yamlEditor = document.getElementById('searches-yaml-editor');
  
  const profileJson = jsonEditor.value.trim();
  try {{
    JSON.parse(profileJson);
  }} catch (e) {{
    alert("Invalid JSON in Profile Data: " + e.message);
    return;
  }}
  
  const searchesYaml = yamlEditor.value.trim();
  const saveBtn = document.getElementById('save-all-btn');
  saveBtn.disabled = true;
  saveBtn.textContent = 'Saving...';
  
  fetch('/api/config/save', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{
      profile_data: profileJson,
      searches_data: searchesYaml
    }})
  }})
  .then(r => r.json())
  .then(data => {{
    saveBtn.disabled = false;
    saveBtn.textContent = 'Save All Changes';
    if (data.ok) {{
      alert("Profile and search configuration saved successfully!");
      loadConfigData();
    }} else {{
      alert("Save failed: " + (data.error || 'unknown error'));
    }}
  }})
  .catch(err => {{
    saveBtn.disabled = false;
    saveBtn.textContent = 'Save All Changes';
    alert("Network error: failed to save.");
  }});
}}

function handleResumeUpload(input) {{
  const file = input.files[0];
  if (!file) return;
  
  const filename = file.name;
  const lowerName = filename.toLowerCase();
  if (!lowerName.endsWith('.pdf') && !lowerName.endsWith('.txt')) {{
    alert("Only .pdf or .txt resume documents are accepted.");
    input.value = '';
    return;
  }}
  
  const reader = new FileReader();
  reader.onload = function(e) {{
    const arrayBuffer = e.target.result;
    
    fetch('/api/resume/upload', {{
      method: 'POST',
      headers: {{
        'Content-Type': 'application/octet-stream',
        'X-File-Name': filename
      }},
      body: arrayBuffer
    }})
    .then(r => r.json())
    .then(data => {{
      input.value = '';
      if (data.ok) {{
        let msg = filename + " uploaded successfully!";
        if (data.warning) {{
          msg += "\\n\\nWarning: " + data.warning;
        }}
        alert(msg);
        loadConfigData();
      }} else {{
        alert("Upload failed: " + (data.error || 'unknown error'));
      }}
    }})
    .catch(err => {{
      input.value = '';
      alert("Network error: failed to upload resume.");
    }});
  }};
  reader.readAsArrayBuffer(file);
}}
let discoveryInterval = null;
let discoveryStartTime = null;
let currentTaskId = null;

function startDiscovery() {{
  const target = document.getElementById('discover-target').value;
  const minScore = document.getElementById('discover-min-score').value;
  const btn = document.getElementById('discover-btn');
  const cancelBtn = document.getElementById('discovery-cancel-btn');
  const progContainer = document.getElementById('discovery-progress-container');
  const statusText = document.getElementById('discovery-status-text');
  const timerSpan = document.getElementById('discovery-timer');
  const logsDiv = document.getElementById('discovery-logs');
  
  btn.disabled = true;
  btn.style.opacity = '0.6';
  cancelBtn.disabled = false;
  cancelBtn.style.display = 'inline-block';
  cancelBtn.textContent = 'Cancel';
  progContainer.style.display = 'block';
  statusText.textContent = 'Contacting server...';
  logsDiv.textContent = 'Initializing discovery loop...\\n';
  
  discoveryStartTime = Date.now();
  if (discoveryInterval) clearInterval(discoveryInterval);
  
  discoveryInterval = setInterval(() => {{
    const elapsed = Math.floor((Date.now() - discoveryStartTime) / 1000);
    const m = String(Math.floor(elapsed / 60)).padStart(2, '0');
    const s = String(elapsed % 60).padStart(2, '0');
    timerSpan.textContent = m + ':' + s;
  }}, 1000);
  
  fetch('/api/discover/run', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{
      target_count: parseInt(target),
      min_score: parseInt(minScore)
    }})
  }})
  .then(r => r.json())
  .then(data => {{
    if (data.ok && data.task_id) {{
      statusText.textContent = 'Search in progress (this may take 1-3 minutes)...';
      pollDiscoveryStatus(data.task_id);
    }} else {{
      clearInterval(discoveryInterval);
      btn.disabled = false;
      btn.style.opacity = '1';
      progContainer.style.display = 'none';
      alert('Failed to start discovery: ' + (data.error || 'unknown error'));
    }}
  }})
  .catch(err => {{
    clearInterval(discoveryInterval);
    btn.disabled = false;
    btn.style.opacity = '1';
    progContainer.style.display = 'none';
    alert('Network error starting discovery.');
  }});
}}

function cancelDiscovery() {{
  if (!currentTaskId) return;
  const cancelBtn = document.getElementById('discovery-cancel-btn');
  cancelBtn.disabled = true;
  cancelBtn.textContent = 'Cancelling...';
  
  fetch('/api/discover/cancel', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{task_id: currentTaskId}})
  }})
  .then(r => r.json())
  .then(data => {{
    if (data.ok) {{
      const logs = document.getElementById('discovery-logs');
      logs.textContent += '\\n[System] Cancellation request sent successfully.\\n';
      logs.scrollTop = logs.scrollHeight;
    }} else {{
      alert('Failed to cancel search: ' + (data.error || 'unknown error'));
      cancelBtn.disabled = false;
      cancelBtn.textContent = 'Cancel';
    }}
  }})
  .catch(() => {{
    alert('Network error requesting cancellation.');
    cancelBtn.disabled = false;
    cancelBtn.textContent = 'Cancel';
  }});
}}

function pollDiscoveryStatus(taskId) {{
  currentTaskId = taskId;
  const statusText = document.getElementById('discovery-status-text');
  const btn = document.getElementById('discover-btn');
  const cancelBtn = document.getElementById('discovery-cancel-btn');
  const progContainer = document.getElementById('discovery-progress-container');
  const logsDiv = document.getElementById('discovery-logs');
  
  fetch('/api/status?task_id=' + taskId)
  .then(r => r.json())
  .then(data => {{
    if (data.logs && Array.isArray(data.logs)) {{
      logsDiv.textContent = data.logs.join('\\n');
      logsDiv.scrollTop = logsDiv.scrollHeight;
    }}
    
    if (data.status === 'processing') {{
      statusText.textContent = data.message || 'Searching...';
      setTimeout(() => pollDiscoveryStatus(taskId), 3000);
    }} else if (data.status === 'completed') {{
      clearInterval(discoveryInterval);
      statusText.textContent = data.message || 'Discovery finished!';
      cancelBtn.style.display = 'none';
      setTimeout(() => {{
        alert(data.message || 'Job discovery finished successfully!');
        setTimeout(() => location.reload(), 3000);
      }}, 500);
    }} else if (data.status === 'cancelled') {{
      clearInterval(discoveryInterval);
      btn.disabled = false;
      btn.style.opacity = '1';
      progContainer.style.display = 'none';
      alert('Job discovery cancelled.');
    }} else if (data.status === 'error') {{
      clearInterval(discoveryInterval);
      btn.disabled = false;
      btn.style.opacity = '1';
      progContainer.style.display = 'none';
      alert('Discovery error: ' + (data.message || 'unknown error'));
    }} else {{
      setTimeout(() => pollDiscoveryStatus(taskId), 3000);
    }}
  }})
  .catch(() => {{
    setTimeout(() => pollDiscoveryStatus(taskId), 5000);
  }});
}}

function filterScore(ev, min) {{
  minScore = min;
  // Only toggle the score-row buttons (the first group)
  document.querySelectorAll('.filters .filter-btn').forEach(b => {{
    if (b.id !== 'applied-toggle') b.classList.remove('active');
  }});
  ev.target.classList.add('active');
  applyFilters();
}}

function filterText(text) {{
  searchText = text.toLowerCase();
  applyFilters();
}}

function toggleApplied(ev) {{
  hideApplied = !hideApplied;
  const btn = document.getElementById('applied-toggle');
  btn.textContent = hideApplied ? 'Hidden' : 'Show';
  btn.classList.toggle('active', !hideApplied);
  applyFilters();
}}

function markApplied(el) {{
  const card = el.closest('.job-card');
  if (!card) return true;
  if (card.dataset.applied === '1') return true;
  const url = card.dataset.url;
  if (!url) return true;
  // Fire-and-forget; keepalive lets the request complete even if the tab navigates
  try {{
    fetch('/api/mark-applied', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{url: url}}),
      keepalive: true,
    }}).catch(() => {{}});
  }} catch (e) {{ /* ignore */ }}
  card.dataset.applied = '1';
  card.classList.add('applied');
  // Inject an Applied badge if not already present
  const meta = card.querySelector('.meta-row');
  if (meta && !meta.querySelector('.applied-badge')) {{
    const span = document.createElement('span');
    span.className = 'meta-tag applied-badge';
    span.textContent = 'Applied \u2713';
    meta.appendChild(span);
  }}
  applyFilters();
  return true;
}}

function applyFilters() {{
  let shown = 0;
  let total = 0;
  document.querySelectorAll('.job-card').forEach(card => {{
    total++;
    const score = parseInt(card.dataset.score) || 0;
    const applied = card.dataset.applied === '1';
    const text = card.textContent.toLowerCase();
    // Applied cards bypass the score floor so they always remain visible
    const scoreMatch = applied || score >= (minScore || 5);
    const textMatch = !searchText || text.includes(searchText);
    const appliedMatch = !hideApplied || !applied;
    if (scoreMatch && textMatch && appliedMatch) {{
      card.classList.remove('hidden');
      shown++;
    }} else {{
      card.classList.add('hidden');
    }}
  }});
  document.getElementById('job-count').textContent = `Showing ${{shown}} of ${{total}} jobs`;

  // Hide empty score groups
  document.querySelectorAll('.score-header').forEach(header => {{
    const grid = header.nextElementSibling;
    if (grid && grid.classList.contains('job-grid')) {{
      const visible = grid.querySelectorAll('.job-card:not(.hidden)').length;
      header.style.display = visible ? '' : 'none';
      grid.style.display = visible ? '' : 'none';
    }}
  }});
}}

applyFilters();

// ── Profile management ──────────────────────────────────────────────────────

function switchProfile(name) {{
  if (!name) return;
  fetch('/api/profiles/switch', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{name: name}}),
  }}).then(r => r.json()).then(data => {{
    if (data.ok) location.reload();
    else alert('Switch failed: ' + (data.error || 'unknown error'));
  }}).catch(() => alert('Could not reach dashboard server.'));
}}

function openCreateModal() {{
  document.getElementById('create-msg').textContent = '';
  document.getElementById('new-profile-name').value = '';
  document.getElementById('create-modal').classList.add('open');
  document.getElementById('new-profile-name').focus();
}}

function closeCreateModal() {{
  document.getElementById('create-modal').classList.remove('open');
}}

function submitCreate() {{
  const name = document.getElementById('new-profile-name').value.trim();
  const cloneFrom = document.getElementById('clone-from-select').value;
  if (!name) {{ document.getElementById('create-msg').textContent = 'Name is required.'; return; }}
  fetch('/api/profiles/create', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{name: name, clone_from: cloneFrom || null}}),
  }}).then(r => r.json()).then(data => {{
    if (data.ok) location.reload();
    else document.getElementById('create-msg').textContent = data.error || 'Error creating profile.';
  }}).catch(() => {{ document.getElementById('create-msg').textContent = 'Server error.'; }});
}}

function openDeleteModal() {{
  document.getElementById('delete-msg').textContent = '';
  document.getElementById('delete-modal').classList.add('open');
}}

function closeDeleteModal() {{
  document.getElementById('delete-modal').classList.remove('open');
}}

async function submitDelete() {{
  const sel = document.getElementById('delete-profile-select');
  const name = sel.value;
  if (!name) {{
    document.getElementById('delete-msg').textContent = 'Please select a profile to delete.';
    return;
  }}
  if (!await showConfirm('Are you sure you want to delete profile "' + name + '"? This cannot be undone.')) return;
  fetch('/api/profiles/delete', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{name: name}}),
  }}).then(r => r.json()).then(data => {{
    if (data.ok) location.reload();
    else document.getElementById('delete-msg').textContent = 'Delete failed: ' + (data.error || 'unknown error');
  }}).catch(() => {{
    document.getElementById('delete-msg').textContent = 'Server error.';
  }});
}}

// Close modal on background click
document.getElementById('create-modal').addEventListener('click', function(e) {{
  if (e.target === this) closeCreateModal();
}});

document.getElementById('delete-modal').addEventListener('click', function(e) {{
  if (e.target === this) closeDeleteModal();
}});

// Drag and drop for resume upload area
const uploadArea = document.querySelector('.upload-area');
if (uploadArea) {{
  ['dragenter', 'dragover'].forEach(eventName => {{
    uploadArea.addEventListener(eventName, (e) => {{
      e.preventDefault();
      e.stopPropagation();
      uploadArea.style.borderColor = '#60a5fa';
      uploadArea.style.background = '#33415555';
    }}, false);
  }});

  ['dragleave', 'drop'].forEach(eventName => {{
    uploadArea.addEventListener(eventName, (e) => {{
      e.preventDefault();
      e.stopPropagation();
      uploadArea.style.borderColor = '#475569';
      uploadArea.style.background = '';
    }}, false);
  }});

  uploadArea.addEventListener('drop', (e) => {{
    e.preventDefault();
    e.stopPropagation();
    const dt = e.dataTransfer;
    const files = dt.files;
    if (files.length) {{
      const input = document.getElementById('resume-file-input');
      const container = new DataTransfer();
      container.items.add(files[0]);
      input.files = container.files;
      handleResumeUpload(input);
    }}
  }}, false);
}}
</script>

</body>
</html>"""

    return html


def generate_dashboard(output_path: str | None = None) -> str:
    """Render the dashboard to a static HTML file (legacy helper).

    Args:
        output_path: Where to write the HTML file. Defaults to ~/.applypilot/dashboard.html.

    Returns:
        Absolute path to the generated HTML file.
    """
    out = Path(output_path) if output_path else APP_DIR / "dashboard.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_dashboard_html(), encoding="utf-8")
    abs_path = str(out.resolve())
    console.print(f"[green]Dashboard written to {abs_path}[/green]")
    return abs_path


# ---------------------------------------------------------------------------
# Lightweight HTTP server
# ---------------------------------------------------------------------------

# Serialize DB writes from request handlers (HTTPServer is single-threaded, but
# keep an explicit lock so the contract holds if we ever swap to ThreadingHTTPServer).
_db_write_lock = threading.Lock()


def _mark_applied(url: str) -> bool:
    """Mark a job URL as applied in the database. Returns True on update."""
    if not url:
        return False
    now = datetime.now(timezone.utc).isoformat()
    with _db_write_lock:
        conn = get_connection()
        cur = conn.execute(
            "UPDATE jobs SET apply_status = 'applied', applied_at = ? "
            "WHERE url = ? AND applied_at IS NULL",
            (now, url),
        )
        conn.commit()
    return cur.rowcount > 0


class _DashboardHandler(BaseHTTPRequestHandler):
    """Request handler for the dashboard server."""

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        # Route access logs through rich at debug level (quiet by default)
        console.print(f"[dim]{self.address_string()} - {format % args}[/dim]")

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length > 0 else b""
        return json.loads(raw.decode("utf-8")) if raw else {}

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        from urllib.parse import urlparse, parse_qs
        parsed_path = urlparse(self.path)
        
        if parsed_path.path == '/api/status':
            from applypilot.apply.fill import _tasks, _tasks_lock
            origin = self.headers.get('Origin', '*')
            query = parse_qs(parsed_path.query)
            task_ids = query.get("task_id", [])
            task_id = task_ids[0] if task_ids else ""
            
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Access-Control-Allow-Origin', origin)
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            
            with _tasks_lock:
                task = _tasks.get(task_id)
                
            if task:
                self.wfile.write(json.dumps(task).encode('utf-8'))
            else:
                self.wfile.write(json.dumps({"status": "error", "message": "Task not found"}).encode('utf-8'))
            return

        if parsed_path.path == '/api/config/get':
            from applypilot.config import profile_dir
            origin = self.headers.get('Origin', '*')
            
            p_dir = profile_dir()
            profile_path = p_dir / "profile.json"
            searches_path = p_dir / "searches.yaml"
            resume_pdf = p_dir / "resume.pdf"
            resume_txt = p_dir / "resume.txt"
            
            profile_exists = profile_path.exists()
            profile_data = profile_path.read_text(encoding="utf-8") if profile_exists else ""
            
            searches_exists = searches_path.exists()
            searches_data = searches_path.read_text(encoding="utf-8") if searches_exists else ""
            
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Access-Control-Allow-Origin', origin)
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            
            payload = {
                "profile_exists": profile_exists,
                "profile_data": profile_data,
                "searches_exists": searches_exists,
                "searches_data": searches_data,
                "resume_pdf_exists": resume_pdf.exists(),
                "resume_txt_exists": resume_txt.exists()
            }
            self.wfile.write(json.dumps(payload).encode('utf-8'))
            return

        if self.path in ("/", "/index.html"):
            try:
                html = render_dashboard_html()
            except sqlite3.Error as exc:
                self._send_json(500, {"error": f"db: {exc}"})
                return
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/api/health":
            self._send_json(200, {"ok": True})
            return
        if self.path == "/api/profiles":
            active = get_active_profile_name()
            profiles = [
                {"name": p, "active": p == active, **profile_files_status(p)}
                for p in list_profiles()
            ]
            self._send_json(200, {"active": active, "profiles": profiles})
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        if self.path == '/api/discover/cancel':
            from applypilot.apply.fill import _tasks, _tasks_lock
            origin = self.headers.get("Origin", "*")
            try:
                data = self._read_json_body()
            except Exception:
                data = {}
            task_id = (data.get("task_id") or "").strip()
            if not task_id:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Access-Control-Allow-Origin', origin)
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(json.dumps({"error": "missing task_id"}).encode('utf-8'))
                return
            
            with _tasks_lock:
                if task_id in _tasks:
                    _tasks[task_id]["status"] = "cancelled"
                    _tasks[task_id]["message"] = "Task cancelled by user."
                    _tasks[task_id].setdefault("logs", []).append("[System] Cancellation requested by user. Aborting...")
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json; charset=utf-8')
                    self.send_header('Access-Control-Allow-Origin', origin)
                    self.send_header('Cache-Control', 'no-store')
                    self.end_headers()
                    self.wfile.write(json.dumps({"ok": True}).encode('utf-8'))
                else:
                    self.send_response(404)
                    self.send_header('Content-Type', 'application/json; charset=utf-8')
                    self.send_header('Access-Control-Allow-Origin', origin)
                    self.send_header('Cache-Control', 'no-store')
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": "Task not found"}).encode('utf-8'))
            return

        if self.path == '/api/discover/run':
            from applypilot.apply.fill import _tasks, _tasks_lock
            from applypilot.config import profile_dir
            import uuid
            origin = self.headers.get("Origin", "*")
            try:
                data = self._read_json_body()
            except Exception:
                data = {}
            target_count = int(data.get("target_count") or 10)
            min_score = int(data.get("min_score") or 7)
            max_evaluated = data.get("max_evaluated")
            if max_evaluated is not None:
                try:
                    max_evaluated = int(max_evaluated)
                except ValueError:
                    max_evaluated = 50
            else:
                max_evaluated = 50
            
            p_dir = profile_dir()
            resume_txt = p_dir / "resume.txt"
            searches_yaml = p_dir / "searches.yaml"
            if not resume_txt.exists():
                self.send_response(400)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Access-Control-Allow-Origin', origin)
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Resume file is missing. Please upload a resume first."}).encode('utf-8'))
                return
            if not searches_yaml.exists():
                self.send_response(400)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Access-Control-Allow-Origin', origin)
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Search configuration is missing. Please save searches.yaml first."}).encode('utf-8'))
                return
                
            task_id = str(uuid.uuid4())
            with _tasks_lock:
                _tasks[task_id] = {
                    "status": "processing",
                    "message": "Starting manual discovery...",
                    "logs": ["Starting manual discovery loop..."]
                }
                
            def _async_discover():
                from applypilot.discovery.manual import run_manual_discovery
                
                def _log(msg):
                    with _tasks_lock:
                        if task_id in _tasks:
                            _tasks[task_id].setdefault("logs", []).append(msg)
                            _tasks[task_id]["message"] = msg

                _log("Deduplicating and fetching job postings from job boards...")
                
                def custom_score(resume_txt, job):
                    # Check for cancellation before calling LLM
                    with _tasks_lock:
                        if _tasks.get(task_id, {}).get("status") == "cancelled":
                            raise RuntimeError("Task cancelled by user.")
                    
                    job_title = job.get("title") or "Unknown Title"
                    job_site = job.get("site") or "unknown site"
                    _log(f"Scoring: {job_title} on {job_site}...")
                    
                    from applypilot.scoring.scorer import score_job as _default_score
                    try:
                        res = _default_score(resume_txt, job)
                        
                        # Check again
                        with _tasks_lock:
                            if _tasks.get(task_id, {}).get("status") == "cancelled":
                                raise RuntimeError("Task cancelled by user.")
                        
                        score = int(res.get("score") or 0)
                        reasoning = res.get("reasoning", "").strip()
                        reason_summary = (reasoning[:150] + "...") if len(reasoning) > 150 else reasoning
                        if score >= min_score:
                            _log(f"  [Match] Score={score} | Title: {job_title}")
                        else:
                            _log(f"  [Rejected] Score={score} | Title: {job_title} | Reason: {reason_summary}")
                        return res
                    except Exception as e:
                        if "cancelled" in str(e).lower() or _tasks.get(task_id, {}).get("status") == "cancelled":
                            raise RuntimeError("Task cancelled by user.")
                        _log(f"  [Error] Failed to score {job_title}: {e}")
                        raise e
                
                try:
                    res = run_manual_discovery(
                        target_count=target_count,
                        min_score=min_score,
                        max_evaluated=max_evaluated,
                        score_fn=custom_score
                    )
                    with _tasks_lock:
                        if task_id in _tasks:
                            if _tasks[task_id].get("status") == "cancelled":
                                return
                            _tasks[task_id]["status"] = "completed"
                            _tasks[task_id]["message"] = f"Collected {res.get('collected')} high-fit jobs, evaluated {res.get('evaluated')}!"
                            _tasks[task_id].setdefault("logs", []).append("Discovery completed successfully!")
                            _tasks[task_id]["result"] = res
                except Exception as e:
                    with _tasks_lock:
                        if task_id in _tasks:
                            if _tasks[task_id].get("status") == "cancelled":
                                return
                            _tasks[task_id]["status"] = "error"
                            _tasks[task_id]["message"] = f"Discovery failed: {e}"
                            _tasks[task_id].setdefault("logs", []).append(f"Fatal Error: {e}")
            
            thread = threading.Thread(target=_async_discover, daemon=True)
            thread.start()
            
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Access-Control-Allow-Origin', origin)
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True, "task_id": task_id}).encode('utf-8'))
            return

        if self.path == '/api/config/generate':
            from applypilot.config import profile_dir
            from applypilot.llm import get_client
            origin = self.headers.get("Origin", "*")
            
            try:
                p_dir = profile_dir()
                resume_txt = p_dir / "resume.txt"
                if not resume_txt.exists():
                    self.send_response(400)
                    self.send_header('Content-Type', 'application/json; charset=utf-8')
                    self.send_header('Access-Control-Allow-Origin', origin)
                    self.send_header('Cache-Control', 'no-store')
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": "resume.txt not found. Please upload a resume first."}).encode('utf-8'))
                    return
                
                resume_text = resume_txt.read_text(encoding="utf-8")
                client = get_client()
                
                # Generate profile JSON
                profile_prompt = """
You are an expert resume parsing AI.
Read the candidate's resume text and extract all relevant information to populate a JSON profile matching the schema below.
Keep values precise and strictly base them on the provided resume content.
If a field is not found, leave it as an empty string (or empty list/dictionary where appropriate).
Output ONLY valid JSON, with no markdown code blocks and no backticks.

SCHEMA:
{
  "personal": {
    "full_name": "",
    "preferred_name": "",
    "email": "",
    "password": "",
    "phone": "",
    "address": "",
    "city": "",
    "province_state": "",
    "country": "",
    "postal_code": "",
    "linkedin_url": "",
    "github_url": "",
    "portfolio_url": "",
    "website_url": ""
  },
  "work_authorization": {
    "legally_authorized_to_work": "Yes/No (default: Yes)",
    "require_sponsorship": "Yes/No (default: No)",
    "work_permit_type": ""
  },
  "availability": {
    "earliest_start_date": "Immediately",
    "available_for_full_time": "Yes",
    "available_for_contract": "No"
  },
  "compensation": {
    "salary_expectation": "",
    "salary_currency": "USD",
    "salary_range_min": "",
    "salary_range_max": "",
    "currency_conversion_note": ""
  },
  "experience": {
    "years_of_experience_total": "",
    "education_level": "",
    "current_job_title": "",
    "current_company": "",
    "target_role": ""
  },
  "skills_boundary": {
    "languages": [],
    "frameworks": [],
    "devops": [],
    "databases": [],
    "tools": []
  },
  "resume_facts": {
    "preserved_companies": [],
    "preserved_projects": [],
    "preserved_school": "",
    "real_metrics": []
  },
  "eeo_voluntary": {
    "gender": "Decline to self-identify",
    "race_ethnicity": "Decline to self-identify",
    "veteran_status": "I am not a protected veteran",
    "disability_status": "I do not wish to answer"
  }
}
"""
                messages = [
                    {"role": "system", "content": profile_prompt},
                    {"role": "user", "content": f"Resume content:\n\n{resume_text}"}
                ]
                raw_profile = client.chat(messages, temperature=0.0)
                raw_profile = raw_profile.strip()
                if raw_profile.startswith("```"):
                    lines = raw_profile.split("\n")
                    if lines[0].startswith("```"):
                        lines = lines[1:]
                    if lines and lines[-1].startswith("```"):
                        lines = lines[:-1]
                    raw_profile = "\n".join(lines).strip()
                
                # Verify JSON parses
                json.loads(raw_profile)
                
                # Generate searches YAML
                searches_prompt = """
You are an expert job search strategy AI.
Read the candidate's resume and target role, then generate a YAML document containing a list of job search queries relevant to their skills.
Include 2 to 5 search queries, with their search priority tier (tier 1 is highest priority, tier 2 is medium, etc.).
Output ONLY valid YAML, with no markdown code blocks and no backticks.

YAML STRUCTURE:
queries:
  - query: "exact search phrase (e.g. Backend Engineer)"
    tier: 1
"""
                messages = [
                    {"role": "system", "content": searches_prompt},
                    {"role": "user", "content": f"Resume content:\n\n{resume_text}"}
                ]
                raw_searches = client.chat(messages, temperature=0.0)
                raw_searches = raw_searches.strip()
                if raw_searches.startswith("```"):
                    lines = raw_searches.split("\n")
                    if lines[0].startswith("```"):
                        lines = lines[1:]
                    if lines and lines[-1].startswith("```"):
                        lines = lines[:-1]
                    raw_searches = "\n".join(lines).strip()
                
                if "queries:" not in raw_searches:
                    raise ValueError("Generated searches configuration is invalid")
                
                # Save both files
                (p_dir / "profile.json").write_text(raw_profile, encoding="utf-8")
                (p_dir / "searches.yaml").write_text(raw_searches, encoding="utf-8")
                
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Access-Control-Allow-Origin', origin)
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(json.dumps({"ok": True}).encode('utf-8'))
                
            except Exception as e:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Access-Control-Allow-Origin', origin)
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode('utf-8'))
            return

        if self.path == '/api/resume/delete':
            from applypilot.config import profile_dir
            origin = self.headers.get("Origin", "*")
            try:
                data = self._read_json_body()
            except Exception:
                data = {}
            file_type = data.get("type")
            if file_type not in ('pdf', 'txt'):
                self.send_response(400)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Access-Control-Allow-Origin', origin)
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Invalid type. Must be 'pdf' or 'txt'"}).encode('utf-8'))
                return
                
            try:
                p_dir = profile_dir()
                target_file = p_dir / f"resume.{file_type}"
                if target_file.exists():
                    target_file.unlink()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Access-Control-Allow-Origin', origin)
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(json.dumps({"ok": True}).encode('utf-8'))
            except Exception as e:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Access-Control-Allow-Origin', origin)
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode('utf-8'))
            return

        if self.path == '/api/fill':
            import uuid
            from applypilot.apply.fill import _async_fill_worker
            origin = self.headers.get("Origin", "*")
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length)
            
            try:
                data = json.loads(body.decode('utf-8'))
                fields = data.get("fields", [])
                
                # Start background thread
                task_id = str(uuid.uuid4())
                thread = threading.Thread(target=_async_fill_worker, args=(task_id, fields), daemon=True)
                thread.start()
                
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Access-Control-Allow-Origin', origin)
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                
                # Instantly respond with the Task ID to prevent browser timeouts
                self.wfile.write(json.dumps({"status": "processing", "task_id": task_id}).encode('utf-8'))
            except Exception as e:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Access-Control-Allow-Origin', origin)
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": str(e)}).encode('utf-8'))
            return

        if self.path == '/api/resume/upload':
            from applypilot.config import profile_dir
            origin = self.headers.get("Origin", "*")
            filename = self.headers.get('X-File-Name', '').strip()
            
            lower_name = filename.lower()
            if not (lower_name.endswith('.pdf') or lower_name.endswith('.txt')):
                self.send_response(400)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Access-Control-Allow-Origin', origin)
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Invalid file type. Only PDF or TXT documents allowed."}).encode('utf-8'))
                return
                
            content_length = int(self.headers.get('Content-Length', 0))
            file_data = self.rfile.read(content_length)
            
            try:
                p_dir = profile_dir()
                p_dir.mkdir(parents=True, exist_ok=True)
                
                extract_warn = None
                if lower_name.endswith('.pdf'):
                    # Save PDF as resume.pdf
                    target_pdf = p_dir / "resume.pdf"
                    target_pdf.write_bytes(file_data)
                    
                    # Auto-extract text from PDF to resume.txt
                    try:
                        from pypdf import PdfReader
                        reader = PdfReader(target_pdf)
                        text = ""
                        links = []
                        for page in reader.pages:
                            text += page.extract_text() or ""
                            if "/Annots" in page:
                                for annot in page["/Annots"]:
                                    try:
                                        annotation = annot.get_object()
                                        if annotation.get("/Subtype") == "/Link":
                                            if "/A" in annotation and "/URI" in annotation["/A"]:
                                                uri = annotation["/A"]["/URI"]
                                                if uri and uri not in links:
                                                    links.append(uri)
                                    except Exception:
                                        pass
                        
                        if links:
                            text += "\n\n--- Extracted Links ---\n"
                            for link in links:
                                text += f"- {link}\n"
                        
                        target_txt = p_dir / "resume.txt"
                        target_txt.write_text(text, encoding="utf-8")
                    except Exception as extract_err:
                        extract_warn = f"Saved PDF, but could not extract plain text: {extract_err}"
                else:
                    # Save TXT directly as resume.txt
                    target_txt = p_dir / "resume.txt"
                    target_txt.write_bytes(file_data)
                
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Access-Control-Allow-Origin', origin)
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                
                payload = {"ok": True, "filename": filename}
                if extract_warn:
                    payload["warning"] = extract_warn
                self.wfile.write(json.dumps(payload).encode('utf-8'))
            except Exception as e:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Access-Control-Allow-Origin', origin)
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode('utf-8'))
            return

        try:
            data = self._read_json_body()
        except (ValueError, json.JSONDecodeError):
            self._send_json(400, {"error": "invalid json"})
            return

        if self.path == '/api/config/save':
            from applypilot.config import profile_dir
            origin = self.headers.get("Origin", "*")
            try:
                p_dir = profile_dir()
                profile_path = p_dir / "profile.json"
                searches_path = p_dir / "searches.yaml"
                
                profile_data = data.get("profile_data")
                searches_data = data.get("searches_data")
                
                if profile_data is not None:
                    # Validate JSON
                    json.loads(profile_data)
                    profile_path.write_text(profile_data, encoding="utf-8")
                    
                if searches_data is not None:
                    searches_path.write_text(searches_data, encoding="utf-8")
                    
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Access-Control-Allow-Origin', origin)
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(json.dumps({"ok": True}).encode('utf-8'))
            except Exception as e:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Access-Control-Allow-Origin', origin)
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode('utf-8'))
            return

        if self.path == "/api/mark-applied":
            url = (data.get("url") or "").strip()
            if not url:
                self._send_json(400, {"error": "missing url"})
                return
            try:
                updated = _mark_applied(url)
            except sqlite3.Error as exc:
                self._send_json(500, {"error": f"db: {exc}"})
                return
            self._send_json(200, {"ok": True, "url": url, "updated": updated})
            return

        if self.path == "/api/profiles/switch":
            name = (data.get("name") or "").strip()
            try:
                set_active_profile(name)
                self._send_json(200, {"ok": True, "active": name})
            except (FileNotFoundError, ValueError) as exc:
                self._send_json(400, {"error": str(exc)})
            return

        if self.path == "/api/profiles/create":
            name = (data.get("name") or "").strip()
            clone_from = (data.get("clone_from") or "").strip() or None
            try:
                target = create_profile(name, clone_from=clone_from)
                self._send_json(200, {"ok": True, "name": name, "path": str(target)})
            except (FileExistsError, FileNotFoundError, ValueError) as exc:
                self._send_json(400, {"error": str(exc)})
            return

        if self.path == "/api/profiles/delete":
            name = (data.get("name") or "").strip()
            try:
                delete_profile(name)
                self._send_json(200, {"ok": True, "deleted": name})
            except (FileNotFoundError, ValueError) as exc:
                self._send_json(400, {"error": str(exc)})
            return

        self.send_response(404)
        self.end_headers()


def serve_dashboard(port: int = 8089, host: str = "localhost",
                    open_browser: bool = True) -> None:
    """Run the dashboard HTTP server until Ctrl+C.

    Args:
        port: TCP port to bind. Default 8089 (8088 is used by the fill server).
        host: Interface to bind. Defaults to localhost only.
        open_browser: Whether to open the dashboard in the default browser.
    """
    server = HTTPServer((host, port), _DashboardHandler)
    url = f"http://{host}:{port}/"
    console.print(f"[bold green]ApplyPilot Dashboard running at {url}[/bold green]")
    console.print("[dim]Clicking a job title or Apply link marks it as applied.[/dim]")
    console.print("[dim]Press Ctrl+C to stop.[/dim]")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        console.print("\nStopping dashboard server...")
    finally:
        server.server_close()


def open_dashboard(output_path: str | None = None, port: int = 8089) -> None:
    """Launch the dashboard server and open it in the default browser.

    Args:
        output_path: Deprecated. Kept for backward compatibility; ignored.
        port: TCP port for the dashboard server.
    """
    serve_dashboard(port=port, open_browser=True)
