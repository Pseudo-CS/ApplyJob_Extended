"""ApplyPilot configuration: paths, platform detection, user data."""

import os
import platform
import re
import shutil
from pathlib import Path

# User data directory — all user-specific files live here
APP_DIR = Path(os.environ.get("APPLYPILOT_DIR", Path.home() / ".applypilot"))

# Core paths (shared across profiles)
ENV_PATH = APP_DIR / ".env"

# Per-profile data lives under PROFILES_DIR/<name>/
PROFILES_DIR = APP_DIR / "profiles"
ACTIVE_PROFILE_FILE = APP_DIR / "active_profile"
DEFAULT_PROFILE_NAME = "default"
_PROFILE_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,39}$")

# Generated output (shared)
TAILORED_DIR = APP_DIR / "tailored_resumes"
COVER_LETTER_DIR = APP_DIR / "cover_letters"
LOG_DIR = APP_DIR / "logs"

# Chrome worker isolation (shared)
CHROME_WORKER_DIR = APP_DIR / "chrome-workers"
APPLY_WORKER_DIR = APP_DIR / "apply-workers"

# Package-shipped config (YAML registries)
PACKAGE_DIR = Path(__file__).parent
CONFIG_DIR = PACKAGE_DIR / "config"

# Files belonging to a profile (relative names under PROFILES_DIR/<name>/)
_PROFILE_FILE_NAMES = ("profile.json", "resume.txt", "resume.pdf", "searches.yaml")

# Per-profile path attributes resolved dynamically via __getattr__
_PROFILE_PATH_ATTRS = {
    "PROFILE_PATH": "profile.json",
    "RESUME_PATH": "resume.txt",
    "RESUME_PDF_PATH": "resume.pdf",
    "SEARCH_CONFIG_PATH": "searches.yaml",
    "DB_PATH": "applypilot.db",
}


def get_chrome_path() -> str:
    """Auto-detect Chrome/Chromium executable path, cross-platform.

    Override with CHROME_PATH environment variable.
    """
    env_path = os.environ.get("CHROME_PATH")
    if env_path and Path(env_path).exists():
        return env_path

    system = platform.system()

    if system == "Windows":
        candidates = [
            Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
        ]
    elif system == "Darwin":
        candidates = [
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
        ]
    else:  # Linux
        candidates = []
        for name in ("google-chrome", "google-chrome-stable", "chromium-browser", "chromium"):
            found = shutil.which(name)
            if found:
                candidates.append(Path(found))

    for c in candidates:
        if c and c.exists():
            return str(c)

    # Fall back to PATH search
    for name in ("google-chrome", "google-chrome-stable", "chromium-browser", "chromium", "chrome"):
        found = shutil.which(name)
        if found:
            return found

    raise FileNotFoundError(
        "Chrome/Chromium not found. Install Chrome or set CHROME_PATH environment variable."
    )


def get_chrome_user_data() -> Path:
    """Default Chrome user data directory, cross-platform."""
    system = platform.system()
    if system == "Windows":
        return Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "User Data"
    elif system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "Google" / "Chrome"
    else:
        return Path.home() / ".config" / "google-chrome"


def ensure_dirs():
    """Create all required directories and migrate legacy single-profile layout."""
    for d in [APP_DIR, TAILORED_DIR, COVER_LETTER_DIR, LOG_DIR, CHROME_WORKER_DIR, APPLY_WORKER_DIR, PROFILES_DIR]:
        d.mkdir(parents=True, exist_ok=True)
    _maybe_migrate_legacy_profile()
    # Ensure the default profile directory exists so attribute access never fails
    (PROFILES_DIR / DEFAULT_PROFILE_NAME).mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Multi-profile support
# ---------------------------------------------------------------------------

def _validate_profile_name(name: str) -> str:
    """Return a sanitized profile name or raise ValueError."""
    name = (name or "").strip()
    if not _PROFILE_NAME_RE.match(name):
        raise ValueError(
            "Profile name must be 1-40 chars, start with a letter or digit, "
            "and contain only letters, digits, '-' and '_'."
        )
    return name


def _maybe_migrate_legacy_profile() -> None:
    """If files exist at APP_DIR root and no profiles dir is populated, move them.

    Handles installs predating multi-profile support. Idempotent.
    """
    default_dir = PROFILES_DIR / DEFAULT_PROFILE_NAME
    legacy_paths = [APP_DIR / fname for fname in _PROFILE_FILE_NAMES]
    
    # Also migrate the legacy database file if it exists at root
    legacy_db = APP_DIR / "applypilot.db"
    if legacy_db.exists():
        legacy_paths.append(legacy_db)
        
    has_legacy = any(p.exists() for p in legacy_paths)
    default_populated = any((default_dir / fname).exists() for fname in _PROFILE_FILE_NAMES) or (default_dir / "applypilot.db").exists()
    if not has_legacy or default_populated:
        if not ACTIVE_PROFILE_FILE.exists() and default_dir.exists():
            ACTIVE_PROFILE_FILE.write_text(DEFAULT_PROFILE_NAME, encoding="utf-8")
        return
    default_dir.mkdir(parents=True, exist_ok=True)
    for src in legacy_paths:
        if src.exists():
            dst = default_dir / src.name
            if not dst.exists():
                shutil.move(str(src), str(dst))
    if not ACTIVE_PROFILE_FILE.exists():
        ACTIVE_PROFILE_FILE.write_text(DEFAULT_PROFILE_NAME, encoding="utf-8")


def list_profiles() -> list[str]:
    """Return all profile names (directory names under PROFILES_DIR), sorted."""
    if not PROFILES_DIR.exists():
        return []
    return sorted(p.name for p in PROFILES_DIR.iterdir() if p.is_dir())


def get_active_profile_name() -> str:
    """Return the name of the currently active profile.

    Falls back to DEFAULT_PROFILE_NAME (creating the dir/active file if needed).
    """
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    if ACTIVE_PROFILE_FILE.exists():
        name = ACTIVE_PROFILE_FILE.read_text(encoding="utf-8").strip()
        if name and (PROFILES_DIR / name).is_dir():
            return name
    (PROFILES_DIR / DEFAULT_PROFILE_NAME).mkdir(parents=True, exist_ok=True)
    ACTIVE_PROFILE_FILE.write_text(DEFAULT_PROFILE_NAME, encoding="utf-8")
    return DEFAULT_PROFILE_NAME


def set_active_profile(name: str) -> str:
    """Switch the active profile. Returns the name on success.

    Raises FileNotFoundError if the profile directory does not exist.
    """
    name = _validate_profile_name(name)
    target = PROFILES_DIR / name
    if not target.is_dir():
        raise FileNotFoundError(f"Profile '{name}' not found at {target}")
    ACTIVE_PROFILE_FILE.parent.mkdir(parents=True, exist_ok=True)
    ACTIVE_PROFILE_FILE.write_text(name, encoding="utf-8")
    return name


def profile_dir(name: str | None = None) -> Path:
    """Return the directory holding files for the given profile (default: active)."""
    if name is None:
        name = get_active_profile_name()
    else:
        name = _validate_profile_name(name)
    return PROFILES_DIR / name


def create_profile(name: str, clone_from: str | None = None) -> Path:
    """Create a new empty profile directory. If clone_from is set, copy its files in.

    Raises FileExistsError if the profile already exists.
    """
    name = _validate_profile_name(name)
    target = PROFILES_DIR / name
    if target.exists():
        raise FileExistsError(f"Profile '{name}' already exists at {target}")
    target.mkdir(parents=True, exist_ok=False)
    if clone_from:
        src_dir = PROFILES_DIR / _validate_profile_name(clone_from)
        if not src_dir.is_dir():
            raise FileNotFoundError(f"Source profile '{clone_from}' not found")
        for fname in _PROFILE_FILE_NAMES:
            src = src_dir / fname
            if src.exists():
                shutil.copy2(src, target / fname)
    return target


def delete_profile(name: str) -> None:
    """Delete a profile directory. Refuses to delete the active profile or the
    last remaining profile.
    """
    name = _validate_profile_name(name)
    target = PROFILES_DIR / name
    if not target.is_dir():
        raise FileNotFoundError(f"Profile '{name}' not found")
    if name == get_active_profile_name():
        raise ValueError(f"Cannot delete the active profile '{name}'. Switch first.")
    if len(list_profiles()) <= 1:
        raise ValueError("Cannot delete the only remaining profile.")
    shutil.rmtree(target)


def profile_files_status(name: str) -> dict[str, bool]:
    """Return which profile files are present for the given profile."""
    d = profile_dir(name)
    return {fname: (d / fname).exists() for fname in _PROFILE_FILE_NAMES}


def __getattr__(attr: str):
    """Resolve per-profile path constants dynamically against the active profile."""
    fname = _PROFILE_PATH_ATTRS.get(attr)
    if fname is None:
        raise AttributeError(f"module 'applypilot.config' has no attribute {attr!r}")
    return profile_dir() / fname


def load_profile() -> dict:
    """Load the active profile from ~/.applypilot/profiles/<active>/profile.json."""
    import json
    p = profile_dir() / "profile.json"
    if not p.exists():
        raise FileNotFoundError(
            f"Profile not found at {p}. Run `applypilot init` first."
        )
    return json.loads(p.read_text(encoding="utf-8"))


def load_search_config() -> dict:
    """Load search configuration for the active profile, falling back to the example."""
    import yaml
    p = profile_dir() / "searches.yaml"
    if not p.exists():
        # Fall back to package-shipped example
        example = CONFIG_DIR / "searches.example.yaml"
        if example.exists():
            return yaml.safe_load(example.read_text(encoding="utf-8"))
        return {}
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def load_sites_config() -> dict:
    """Load sites.yaml configuration (sites list, manual_ats, blocked, etc.)."""
    import yaml
    path = CONFIG_DIR / "sites.yaml"
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def is_manual_ats(url: str | None) -> bool:
    """Check if a URL routes through an ATS that requires manual application."""
    if not url:
        return False
    sites_cfg = load_sites_config()
    domains = sites_cfg.get("manual_ats", [])
    url_lower = url.lower()
    return any(domain in url_lower for domain in domains)


def load_blocked_sites() -> tuple[set[str], list[str]]:
    """Load blocked sites and URL patterns from sites.yaml.

    Returns:
        (blocked_site_names, blocked_url_patterns)
    """
    cfg = load_sites_config()
    blocked = cfg.get("blocked", {})
    sites = set(blocked.get("sites", []))
    patterns = blocked.get("url_patterns", [])
    return sites, patterns


def load_blocked_sso() -> list[str]:
    """Load blocked SSO domains from sites.yaml."""
    cfg = load_sites_config()
    return cfg.get("blocked_sso", [])


def load_base_urls() -> dict[str, str | None]:
    """Load site base URLs for URL resolution from sites.yaml."""
    cfg = load_sites_config()
    return cfg.get("base_urls", {})


# ---------------------------------------------------------------------------
# Default values — referenced across modules instead of magic numbers
# ---------------------------------------------------------------------------

DEFAULTS = {
    "min_score": 7,
    "max_apply_attempts": 3,
    "max_tailor_attempts": 5,
    "poll_interval": 60,
    "apply_timeout": 300,
    "viewport": "1280x900",
}


def load_env():
    """Load environment variables from ~/.applypilot/.env if it exists."""
    from dotenv import load_dotenv
    if ENV_PATH.exists():
        load_dotenv(ENV_PATH)
    # Also try CWD .env as fallback
    load_dotenv()


# ---------------------------------------------------------------------------
# Tier system — feature gating by installed dependencies
# ---------------------------------------------------------------------------

TIER_LABELS = {
    1: "Discovery",
    2: "AI Scoring & Tailoring",
    3: "Full Auto-Apply",
}

TIER_COMMANDS: dict[int, list[str]] = {
    1: ["init", "run discover", "run enrich", "status", "dashboard"],
    2: ["run score", "run tailor", "run cover", "run pdf", "run"],
    3: ["apply"],
}


def get_tier() -> int:
    """Detect the current tier based on available dependencies.

    Tier 1 (Discovery):            Python + pip
    Tier 2 (AI Scoring & Tailoring): + LLM API key
    Tier 3 (Full Auto-Apply):       + Claude Code CLI + Chrome
    """
    load_env()

    has_llm = any(os.environ.get(k) for k in ("GEMINI_API_KEY", "OPENAI_API_KEY", "LLM_URL"))
    if not has_llm:
        return 1

    has_claude = shutil.which("claude") is not None
    try:
        get_chrome_path()
        has_chrome = True
    except FileNotFoundError:
        has_chrome = False

    if has_claude and has_chrome:
        return 3

    return 2


def check_tier(required: int, feature: str) -> None:
    """Raise SystemExit with a clear message if the current tier is too low.

    Args:
        required: Minimum tier needed (1, 2, or 3).
        feature: Human-readable description of the feature being gated.
    """
    current = get_tier()
    if current >= required:
        return

    from rich.console import Console
    _console = Console(stderr=True)

    missing: list[str] = []
    if required >= 2 and not any(os.environ.get(k) for k in ("GEMINI_API_KEY", "OPENAI_API_KEY", "LLM_URL")):
        missing.append("LLM API key — run [bold]applypilot init[/bold] or set GEMINI_API_KEY")
    if required >= 3:
        if not shutil.which("claude"):
            missing.append("Claude Code CLI — install from [bold]https://claude.ai/code[/bold]")
        try:
            get_chrome_path()
        except FileNotFoundError:
            missing.append("Chrome/Chromium — install or set CHROME_PATH")

    _console.print(
        f"\n[red]'{feature}' requires {TIER_LABELS.get(required, f'Tier {required}')} (Tier {required}).[/red]\n"
        f"Current tier: {TIER_LABELS.get(current, f'Tier {current}')} (Tier {current})."
    )
    if missing:
        _console.print("\n[yellow]Missing:[/yellow]")
        for m in missing:
            _console.print(f"  - {m}")
    _console.print()
    raise SystemExit(1)
