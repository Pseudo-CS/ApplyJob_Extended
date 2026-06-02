"""API server for the Tampermonkey userscript form-filling assistant.

Receives visible form fields from the userscript, queries the local Qwen LLM
using the candidate's profile/resume, and returns the field-filling mapping.

Uses an asynchronous background thread architecture with polling endpoints to
prevent socket timeouts.
"""

import json
import logging
import threading
import uuid
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from playwright.sync_api import sync_playwright

from applypilot import config
from applypilot.config import load_profile, load_env
from applypilot.llm import get_client

log = logging.getLogger(__name__)

# Global dictionary to track background tasks: task_id -> {"status": "...", "mapping": ..., "message": ...}
_tasks = {}
_tasks_lock = threading.Lock()

FILL_PROMPT = """You are a browser form-filling assistant.
Given the candidate's profile data and resume, and a list of form fields on a webpage, map each field to the correct value to fill.

CANDIDATE PROFILE:
{profile_data}

CANDIDATE RESUME:
{resume_data}

FORM FIELDS ON WEBPAGE:
{fields_data}

INSTRUCTIONS:
1. Determine the appropriate value to fill for each field using the candidate's profile and resume.
2. For text inputs, textareas, and password fields, provide the string value.
3. For dropdown selects, choose the matching option or the closest valid value from the profile.
4. For radio buttons and checkboxes, specify the exact option or boolean value (true/false) to set.
5. Return your response in EXACTLY the following JSON format (no introductory or concluding text, code blocks, or markdown):
{{
  "selector_or_id": "value_to_fill"
}}
Ensure the keys match the 'selector' (e.g. [data-ap-id="X"]) provided for each field.
"""

def clean_llm_json(s: str) -> str:
    """Extract and sanitize a JSON object from an LLM response string."""
    import re
    # 1. Find the first '{' and last '}' to extract the JSON block
    first_brace = s.find('{')
    last_brace = s.rfind('}')
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        s = s[first_brace:last_brace+1]
    else:
        s = s.strip()

    # 2. Fix invalid backslash escapes (double any backslash that isn't a valid JSON escape)
    result = []
    i = 0
    n = len(s)
    while i < n:
        if s[i] == '\\':
            if i + 1 < n:
                next_char = s[i+1]
                if next_char in '"\\/bfnrt':
                    result.append(s[i:i+2])
                    i += 2
                    continue
                elif next_char == 'u':
                    if i + 5 < n and all(c in '0123456789abcdefABCDEF' for c in s[i+2:i+6]):
                        result.append(s[i:i+6])
                        i += 6
                        continue
            result.append('\\\\')
            i += 1
        else:
            result.append(s[i])
            i += 1
    s = "".join(result)

    # 3. Strip trailing commas before closing braces/brackets
    s = re.sub(r',\s*([\]}])', r'\1', s)
    
    return s


def sanitize_mapping_keys(mapping: dict) -> dict:
    """Sanitize keys of mapping dictionary to normalize data-ap-id selectors."""
    import re
    sanitized = {}
    for key, val in mapping.items():
        match = re.search(r'data-ap-id=["\\]*(\d+)', key)
        if match:
            new_key = f'[data-ap-id="{match.group(1)}"]'
            sanitized[new_key] = val
        else:
            sanitized[key] = val
    return sanitized


def match_field_by_rules(field: dict, profile: dict) -> str | None:
    """Try to match a field to a profile value using rules. Returns value if matched, else None."""
    import re
    label = field.get("label", "").lower()
    name = field.get("name", "").lower()
    placeholder = field.get("placeholder", "").lower()
    field_id = field.get("id", "").lower()
    
    # Helper to check if any pattern matches with word boundaries in any field property
    def is_match(patterns):
        for pattern in patterns:
            pattern_regex = rf'\b{pattern}\b'
            if (re.search(pattern_regex, label) or 
                re.search(pattern_regex, name) or 
                re.search(pattern_regex, placeholder) or 
                re.search(pattern_regex, field_id)):
                return True
        return False

    personal = profile.get("personal", {})
    
    # 1. Email
    if is_match(["email", "e-mail", "email_address", "emailaddress"]):
        return personal.get("email")
        
    # 2. Phone
    if is_match(["phone", "telephone", "mobile", "cell", "phone_number", "phonenumber"]):
        return personal.get("phone")
        
    # 3. LinkedIn
    if is_match(["linkedin"]):
        return personal.get("linkedin_url") or personal.get("linkedin")
        
    # 4. GitHub
    if is_match(["github"]):
        return personal.get("github_url") or personal.get("github")
        
    # 5. Portfolio/Website
    if is_match(["portfolio", "website", "portfolio_url", "website_url", "portfoliourl", "websiteurl"]):
        return personal.get("portfolio_url") or personal.get("website_url") or personal.get("portfolio")
        
    # 6. First Name
    if is_match(["first name", "first_name", "firstname", "given name", "fname"]):
        if personal.get("preferred_name"):
            return personal.get("preferred_name")
        full_name = personal.get("full_name", "")
        if full_name:
            return full_name.split()[0]
        return ""
        
    # 7. Last Name
    if is_match(["last name", "last_name", "lastname", "family name", "lname", "surname"]):
        full_name = personal.get("full_name", "")
        if full_name:
            parts = full_name.split()
            if len(parts) > 1:
                return " ".join(parts[1:])
        return ""
        
    # 8. Full Name (ensure it does not false-positive match first/last names or other generic identifiers)
    if is_match(["name", "fullname", "full_name"]) and not is_match(["first", "last", "given", "family", "company", "employer", "school", "university", "reference", "emergency", "contact", "spouse", "friend"]):
        return personal.get("full_name")
        
    # 9. City
    if is_match(["city", "town"]):
        return personal.get("city")
        
    # 10. State
    if is_match(["state", "province", "region", "province_state", "provincestate"]):
        return personal.get("province_state")
        
    # 11. Country
    if is_match(["country"]):
        return personal.get("country")
        
    # 12. Postal/Zip
    if is_match(["postal", "zip", "zipcode", "postal_code", "postalcode"]):
        return personal.get("postal_code")

    return None


def generate_fill_mapping(fields: list[dict]) -> dict:
    """Load candidate profile and resume, prompt local LLM, and return mapping."""
    load_env()
    
    # Load user data (resolved against the currently active profile)
    if not config.PROFILE_PATH.exists() or not config.RESUME_PATH.exists():
        return {"status": "error", "message": "Profile or resume missing. Run applypilot init first."}

    profile = load_profile()
    resume = config.RESUME_PATH.read_text(encoding="utf-8")
    
    mapping = {}
    unmatched_fields = []
    
    # --- Layer 1: Rule-based matching ---
    for field in fields:
        matched_val = match_field_by_rules(field, profile)
        if matched_val is not None:
            mapping[field["selector"]] = matched_val
        else:
            unmatched_fields.append(field)
            
    log.info("Layered filling: %d fields matched directly, %d fields routed to LLM.", len(mapping), len(unmatched_fields))
    
    if not unmatched_fields:
        log.info("All fields successfully matched directly via Layer 1 rules!")
        log.info("Generated mapping: %s", mapping)
        return {"status": "ok", "mapping": mapping}
        
    # --- Layer 2: LLM Fallback ---
    try:
        # Prepare data for prompt containing only unmatched fields
        profile_str = json.dumps(profile, indent=2)
        fields_str = json.dumps(unmatched_fields, indent=2)
        
        prompt = FILL_PROMPT.format(
             profile_data=profile_str,
             resume_data=resume,
             fields_data=fields_str
        )
        
        # Send to LLM
        client = get_client()
        response = client.ask(prompt, temperature=0.1, max_tokens=4096)
        
        # Parse JSON mapping
        clean_response = response.strip()
        if clean_response.startswith("```"):
            clean_response = clean_response.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        if clean_response.startswith("json"):
            clean_response = clean_response.split("json", 1)[1].strip()
            
        try:
            llm_mapping = json.loads(clean_response)
        except json.JSONDecodeError as initial_err:
            log.warning("Initial JSON parsing failed: %s. Cleaning and retrying...", initial_err)
            cleaned_response = clean_llm_json(response)
            try:
                llm_mapping = json.loads(cleaned_response, strict=False)
                log.info("Successfully parsed cleaned LLM JSON response.")
            except Exception as final_err:
                log.error("Failed to parse cleaned LLM response: %s", final_err)
                log.error("Raw LLM response was:\n%s", response)
                log.error("Cleaned response was:\n%s", cleaned_response)
                raise final_err
                
        # Normalize selector keys to be robust against LLM syntax/backslash errors in selectors
        llm_mapping = sanitize_mapping_keys(llm_mapping)
        
        # Merge LLM completions into direct mapping
        mapping.update(llm_mapping)
        
        log.info("Generated mapping: %s", mapping)
        return {"status": "ok", "mapping": mapping}
        
    except Exception as e:
        log.error("Error generating fill mapping: %s", e)
        return {"status": "error", "message": str(e)}


def _async_fill_worker(task_id: str, fields: list[dict]):
    """Background worker thread to run LLM scoring."""
    with _tasks_lock:
        _tasks[task_id] = {"status": "processing"}
        
    try:
        result = generate_fill_mapping(fields)
        with _tasks_lock:
            if result.get("status") == "ok":
                _tasks[task_id] = {"status": "completed", "mapping": result.get("mapping")}
            else:
                _tasks[task_id] = {"status": "error", "message": result.get("message", "Processing error")}
    except Exception as e:
        with _tasks_lock:
            _tasks[task_id] = {"status": "error", "message": str(e)}


# ── CDP Native fill implementation ───────────────────────────────────────────

def run_fill(port: int = 9222) -> dict:
    """Fallback / CLI connection method: Connect over CDP, inject data-ap-id, fill."""
    load_env()
    
    # Load user data
    if not config.PROFILE_PATH.exists() or not config.RESUME_PATH.exists():
        return {"status": "error", "message": "Profile or resume missing. Run applypilot init first."}
        
    try:
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(f"http://localhost:{port}")
            context = browser.contexts[0]
            
            if not context.pages:
                return {"status": "error", "message": "No active tabs found in Chrome."}
                
            page = context.pages[0]
            log.info("CDP Active Tab: '%s'", page.title())
            
            # Inject data-ap-id attributes and extract fields
            inject_js = """
            () => {
                const elements = [];
                const inputs = document.querySelectorAll('input, select, textarea');
                let count = 0;
                
                inputs.forEach(el => {
                    const style = window.getComputedStyle(el);
                    if (
                        style.display === 'none' || 
                        style.visibility === 'hidden' || 
                        el.disabled || 
                        el.type === 'hidden' ||
                        el.type === 'submit' ||
                        el.type === 'button' ||
                        el.type === 'file'
                    ) return;
                    
                    // Assign unique ID attribute
                    el.setAttribute('data-ap-id', count);
                    const selector = `[data-ap-id="${count}"]`;
                    count++;
                    
                    let labelText = "";
                    if (el.id) {
                        const label = document.querySelector(`label[for="${el.id}"]`);
                        if (label) labelText = label.textContent.trim();
                    }
                    if (!labelText) {
                        const label = el.closest('label');
                        if (label) labelText = label.textContent.trim();
                    }
                    if (!labelText && el.placeholder) {
                        labelText = el.placeholder;
                    }
                    
                    let options = [];
                    if (el.tagName.toLowerCase() === 'select') {
                        options = Array.from(el.options).map(opt => ({
                            value: opt.value,
                            text: opt.text.trim()
                        }));
                    }
                    
                    elements.push({
                        tagName: el.tagName.toLowerCase(),
                        type: el.type || '',
                        name: el.name || '',
                        id: el.id || '',
                        placeholder: el.placeholder || '',
                        label: labelText,
                        options: options,
                        selector: selector
                    });
                });
                return elements;
            }
            """
            fields = page.evaluate(inject_js)
            if not fields:
                log.info("No visible form fields found.")
                return {"status": "ok", "filled": 0}
                
            # Perform blocking LLM completion
            res = generate_fill_mapping(fields)
            if res.get("status") != "ok":
                return res
                
            mapping = res.get("mapping", {})
            
            # Write changes back into Chrome
            filled_count = 0
            for selector, value in mapping.items():
                if not selector or value is None:
                    continue
                try:
                    el = page.locator(selector).first
                    if el.count() > 0:
                        tag_name = el.evaluate("node => node.tagName.toLowerCase()")
                        input_type = el.evaluate("node => node.type || ''")
                        
                        if tag_name == "select":
                            el.select_option(value=value)
                        elif input_type in ("checkbox", "radio"):
                            should_check = (value is True or value == "true" or value == "1" or value == "yes")
                            el.set_checked(should_check)
                        else:
                            el.fill(str(value))
                        filled_count += 1
                except Exception as ex:
                    log.warning("Could not fill %s: %s", selector, ex)
                    
            log.info("Successfully filled %d fields via CDP", filled_count)
            return {"status": "ok", "filled": filled_count}
    except Exception as e:
        log.error("CDP Fill Error: %s", e)
        return {"status": "error", "message": str(e)}


# ── Local HTTP Server ───────────────────────────────────────────────────────

class FillServerHandler(BaseHTTPRequestHandler):
    """CORS-enabled API server for the Tampermonkey userscript."""
    
    def log_message(self, format, *args):
        # Log to Python logger
        log.info(format, *args)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_GET(self):
        # CORS support
        origin = self.headers.get('Origin', '*')
        
        parsed_path = urlparse(self.path)
        if parsed_path.path == '/api/status':
            query = parse_qs(parsed_path.query)
            task_ids = query.get("task_id", [])
            task_id = task_ids[0] if task_ids else ""
            
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', origin)
            self.end_headers()
            
            with _tasks_lock:
                task = _tasks.get(task_id)
                
            if task:
                self.wfile.write(json.dumps(task).encode('utf-8'))
            else:
                self.wfile.write(json.dumps({"status": "error", "message": "Task not found"}).encode('utf-8'))
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        origin = self.headers.get('Origin', '*')
        
        if self.path == '/api/fill':
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
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', origin)
                self.end_headers()
                
                # Instantly respond with the Task ID to prevent browser timeouts
                self.wfile.write(json.dumps({"status": "processing", "task_id": task_id}).encode('utf-8'))
            except Exception as e:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', origin)
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": str(e)}).encode('utf-8'))
        else:
            self.send_response(404)
            self.end_headers()


def start_fill_server(server_port: int = 8088) -> HTTPServer:
    """Start the fill trigger server in a background daemon thread."""
    server = HTTPServer(('localhost', server_port), FillServerHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server
