"""Manual discovery mode: score-then-store, keep only high-fit candidates.

Flow per job:  fetch -> dedup -> score -> if score >= min_score store it
                                       else drop and continue.

The loop iterates a job source one item at a time and stops as soon as
``target_count`` qualifying jobs are collected, or when the source is
exhausted. Already-known URLs (in the DB or seen earlier in the same run)
are skipped before any LLM call so we never spend tokens on duplicates.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from datetime import datetime, timezone
from typing import Callable, Iterable, Iterator

from rich.console import Console

from applypilot import config
from applypilot.config import load_search_config
from applypilot.database import get_connection, init_db, url_exists

log = logging.getLogger(__name__)
console = Console()


# ---------------------------------------------------------------------------
# Default jobspy-backed source
# ---------------------------------------------------------------------------

def _row_to_job(row, query: str) -> dict | None:
    """Convert a jobspy DataFrame row into the dict shape used by manual mode."""
    def s(key: str, default=None) -> str | None:
        v = str(row.get(key, "") or "").strip()
        return v if v and v != "nan" else default

    url = s("job_url")
    if not url:
        return None

    description = s("description")
    full_desc = description if description and len(description) >= 200 else None

    salary = None
    try:
        min_amt = row.get("min_amount")
        max_amt = row.get("max_amount")
        interval = s("interval", "") or ""
        currency = s("currency", "") or ""
        if min_amt and str(min_amt) != "nan":
            if max_amt and str(max_amt) != "nan":
                salary = f"{currency}{int(float(min_amt)):,}-{currency}{int(float(max_amt)):,}"
            else:
                salary = f"{currency}{int(float(min_amt)):,}"
            if interval:
                salary += f"/{interval}"
    except (ValueError, TypeError):
        salary = None

    return {
        "url": url,
        "title": s("title"),
        "salary": salary,
        "description": description,
        "full_description": full_desc,
        "location": s("location"),
        "site": s("site", "jobspy"),
        "application_url": s("job_url_direct"),
        "query": query,
    }


def _jobspy_source(search_cfg: dict) -> Iterator[dict]:
    """Default job source: stream rows from jobspy across configured searches."""
    from jobspy import scrape_jobs  # local import: optional Tier-1 dep
    from applypilot.config import load_profile

    # Load active profile to determine default location/country
    profile_location = ""
    profile_country = ""
    try:
        profile = load_profile()
        personal = profile.get("personal", {})
        city = personal.get("city", "").strip()
        country = personal.get("country", "").strip()
        if city and country:
            profile_location = f"{city}, {country}"
        elif city:
            profile_location = city
        elif country:
            profile_location = country
        profile_country = country
    except Exception:
        pass

    queries = search_cfg.get("queries", []) or []
    locations = search_cfg.get("locations", []) or [{"location": "", "remote": False}]
    
    # Apply profile_location default if location string is empty
    for loc in locations:
        if isinstance(loc, dict) and not loc.get("location"):
            if profile_location:
                loc["location"] = profile_location

    sites = search_cfg.get("sites") or search_cfg.get("boards") \
        or ["indeed", "linkedin", "zip_recruiter"]
    defaults = search_cfg.get("defaults", {}) or {}
    results_per_site = int(defaults.get("results_per_site", 50))
    hours_old = int(defaults.get("hours_old", 168))
    
    # Determine default country_indeed
    country_indeed = defaults.get("country_indeed") or search_cfg.get("country")
    if not country_indeed:
        if profile_country:
            c_lower = profile_country.lower()
            if "india" in c_lower:
                country_indeed = "india"
            elif "united states" in c_lower or "usa" in c_lower or "us" in c_lower:
                country_indeed = "usa"
            elif "uk" in c_lower or "united kingdom" in c_lower:
                country_indeed = "uk"
            elif "canada" in c_lower:
                country_indeed = "canada"
            elif "australia" in c_lower:
                country_indeed = "australia"
            else:
                country_indeed = c_lower
        else:
            country_indeed = "usa"


    for q in queries:
        q_text = q.get("query") if isinstance(q, dict) else str(q)
        if not q_text:
            continue
        for loc in locations:
            loc_text = loc.get("location", "") if isinstance(loc, dict) else str(loc)
            remote = bool(loc.get("remote")) if isinstance(loc, dict) else False
            kwargs = {
                "site_name": [s for s in sites if s != "glassdoor"] or sites,
                "search_term": q_text,
                "location": loc_text,
                "results_wanted": results_per_site,
                "hours_old": hours_old,
                "description_format": "markdown",
                "country_indeed": country_indeed,
                "verbose": 0,
            }
            if remote:
                kwargs["is_remote"] = True
            if "linkedin" in kwargs["site_name"]:
                kwargs["linkedin_fetch_description"] = True
            try:
                df = scrape_jobs(**kwargs)
            except Exception as e:
                log.warning("jobspy %r @ %r failed: %s", q_text, loc_text, e)
                continue
            if df is None or len(df) == 0:
                continue
            for _, row in df.iterrows():
                job = _row_to_job(row, q_text)
                if job is not None:
                    yield job


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

_INSERT_SQL = """
INSERT INTO jobs (
    url, title, salary, description, location, site, strategy, discovered_at,
    full_description, application_url, detail_scraped_at,
    fit_score, score_reasoning, scored_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def _save_scored_job(conn: sqlite3.Connection, job: dict,
                     score: int, keywords: str, reasoning: str) -> bool:
    """Insert a high-fit job with its score in a single row. Returns True if inserted."""
    now = datetime.now(timezone.utc).isoformat()
    try:
        conn.execute(_INSERT_SQL, (
            job["url"], job.get("title"), job.get("salary"), job.get("description"),
            job.get("location"), job.get("site"), "manual", now,
            job.get("full_description"), job.get("application_url"), now,
            score, f"{keywords}\n{reasoning}", now,
        ))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        # Race: URL appeared between our pre-check and the insert
        return False


def generate_rejection_summary(conn: sqlite3.Connection, p_dir: Path) -> None:
    """Grab up to 10 random rejection reasons and summarize them using the LLM,
    saving the result to rejection_summary.txt in the profile directory.
    """
    from pathlib import Path
    try:
        from applypilot.config import load_env
        load_env()
    except Exception:
        pass

    # Query up to 10 random rejected jobs (score < 7)
    rows = conn.execute("""
        SELECT title, fit_score, score_reasoning 
        FROM jobs 
        WHERE fit_score IS NOT NULL AND fit_score < 7
        ORDER BY RANDOM() LIMIT 10
    """).fetchall()

    summary_path = p_dir / "rejection_summary.txt"
    if not rows:
        summary_path.write_text(
            "No rejection feedback available yet. Run a manual discovery scan to collect feedback.",
            encoding="utf-8"
        )
        return

    rejection_bullets = []
    for r in rows:
        title = r["title"] or "Unknown Job"
        score = r["fit_score"]
        reasoning = r["score_reasoning"] or ""
        # Strip keywords (first line) if present
        lines = reasoning.split("\n")
        if lines and len(lines) > 1 and "," in lines[0]:
            reasoning = "\n".join(lines[1:])
        rejection_bullets.append(f"- Job: {title} (Score: {score})\n  Reason: {reasoning.strip()}")

    prompt = (
        "You are an AI assistant helping a job seeker optimize their profile and resume.\n"
        "Below is a list of up to 10 random jobs that were recently evaluated and rejected for being a low fit, along with the AI evaluation reasoning:\n\n"
        + "\n".join(rejection_bullets) + "\n\n"
        "Analyze these rejection reasons and write a concise, actionable summary (max 3-4 sentences or bullet points) in the second person (e.g. 'You are getting rejected because...'). "
        "Focus on the main patterns of missing skills, experience gaps, or mismatches. Keep it encouraging but highly direct."
    )

    try:
        from applypilot.llm import get_client
        client = get_client()
        summary = client.ask(prompt)
        summary_path.write_text(summary.strip(), encoding="utf-8")
        log.info("Generated rejection feedback summary at %s", summary_path)
    except Exception as e:
        log.error("Failed to generate rejection summary: %s", e)
        # Fall back to bullet points if LLM fails
        bullets = "\n".join(f"• {r['title']}: {r['score_reasoning'].splitlines()[-1] if r['score_reasoning'] else ''}" for r in rows)
        summary_path.write_text(f"Failed to generate AI summary, here are recent rejections:\n{bullets}", encoding="utf-8")


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def run_manual_discovery(
    target_count: int = 10,
    min_score: int = 8,
    max_evaluated: int = 0,
    job_source: Iterable[dict] | None = None,
    score_fn: Callable[[str, dict], dict] | None = None,
    resume_text: str | None = None,
) -> dict:
    """Run the manual score-then-store discovery loop.

    Args:
        target_count: Stop after this many jobs reach ``min_score``.
        min_score: Minimum fit score (1-10) required to keep a job.
        max_evaluated: Hard cap on LLM calls per run (0 = no cap).
        job_source: Iterable of job dicts. Defaults to a jobspy-backed stream
            built from the user's search config. Each dict needs at least
            ``url`` and ``full_description``; ``title``, ``site``, ``location``,
            ``salary``, ``application_url`` are used when present.
        score_fn: ``(resume_text, job) -> {"score": int, "keywords": str,
            "reasoning": str}``. Defaults to ``scoring.scorer.score_job``.
        resume_text: Override the resume text used for scoring. Defaults to
            the contents of ``~/.applypilot/resume.txt``.

    Returns:
        Dict with keys: collected, evaluated, skipped_dup, skipped_no_desc,
        rejected, elapsed, jobs (list of saved entries).
    """
    init_db()
    conn = get_connection()

    if resume_text is None:
        resume_path = config.RESUME_PATH
        if not resume_path.exists():
            raise FileNotFoundError(
                f"Resume not found at {resume_path}. Run `applypilot init` first."
            )
        resume_text = resume_path.read_text(encoding="utf-8")

    if score_fn is None:
        from applypilot.scoring.scorer import score_job as _default_score
        score_fn = _default_score

    if job_source is None:
        job_source = _jobspy_source(load_search_config() or {})

    target_count = max(1, int(target_count))
    min_score = max(1, min(10, int(min_score)))

    console.print(
        f"\n[bold cyan]Manual discovery[/bold cyan]  "
        f"target=[bold]{target_count}[/bold] jobs  "
        f"min_score=[bold]{min_score}[/bold]"
    )
    if max_evaluated:
        console.print(f"  Max evaluations: {max_evaluated}")

    collected = 0
    evaluated = 0
    skipped_dup = 0
    skipped_no_desc = 0
    rejected = 0
    seen_urls: set[str] = set()
    saved_jobs: list[dict] = []

    t0 = time.time()

    for job in job_source:
        if not job or not job.get("url"):
            continue
        url = job["url"]

        # Session-level dedup (in case the source yields the same URL twice)
        if url in seen_urls:
            skipped_dup += 1
            continue
        seen_urls.add(url)

        # DB-level dedup -- never re-score a job we already know about
        if url_exists(conn, url):
            skipped_dup += 1
            continue

        if not job.get("full_description"):
            skipped_no_desc += 1
            continue

        if max_evaluated and evaluated >= max_evaluated:
            console.print(
                f"[yellow]Reached max_evaluated={max_evaluated}; stopping early.[/yellow]"
            )
            break

        evaluated += 1
        result = score_fn(resume_text, {
            "title": job.get("title"),
            "site": job.get("site"),
            "location": job.get("location"),
            "full_description": job.get("full_description"),
        })
        score = int(result.get("score", 0) or 0)
        keywords = result.get("keywords", "") or ""
        reasoning = result.get("reasoning", "") or ""

        inserted = _save_scored_job(conn, job, score, keywords, reasoning)
        if score >= min_score:
            if inserted:
                collected += 1
                saved_jobs.append({
                    **job, "fit_score": score, "score_reasoning": reasoning,
                })
                console.print(
                    f"  [green]+[/green] [{collected}/{target_count}]  "
                    f"score=[bold]{score}[/bold]  {(job.get('title') or '?')[:60]}"
                )
            else:
                skipped_dup += 1
            if collected >= target_count:
                break
        else:
            rejected += 1
            if inserted:
                log.debug("manual: saved rejected score=%d  %s", score, (job.get("title") or "?")[:60])
            else:
                skipped_dup += 1

    elapsed = time.time() - t0
    console.print(
        f"\n[bold]Manual run complete.[/bold]  "
        f"collected={collected}  evaluated={evaluated}  "
        f"skipped_dup={skipped_dup}  no_desc={skipped_no_desc}  rejected={rejected}  "
        f"({elapsed:.1f}s)"
    )
    if collected < target_count:
        console.print(
            f"[yellow]Note: only found {collected} job(s) with score >= {min_score}. "
            f"Source exhausted.[/yellow]"
        )

    # Generate and save rejection summary
    try:
        from applypilot.config import profile_dir
        generate_rejection_summary(conn, profile_dir())
    except Exception as e:
        log.error("Failed to generate rejection summary: %s", e)

    return {
        "collected": collected,
        "evaluated": evaluated,
        "skipped_dup": skipped_dup,
        "skipped_no_desc": skipped_no_desc,
        "rejected": rejected,
        "elapsed": elapsed,
        "jobs": saved_jobs,
    }
