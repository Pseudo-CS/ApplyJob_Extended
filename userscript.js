// ==UserScript==
// @name         ApplyPilot AI Form Filler
// @namespace    http://tampermonkey.net/
// @version      0.7
// @description  Trigger local LLM to fill visible form fields on the page with async polling. Supports standard forms and Google Forms.
// @author       Saandeep
// @match        *://*/*
// @grant        GM_xmlhttpRequest
// @grant        GM_addStyle
// @connect      localhost
// @run-at       document-idle
// ==/UserScript==

(function() {
    'use strict';

    // Globally accessible elements within this module scope
    const btn = document.createElement('button');
    const toast = document.createElement('div');
    let initialized = false;

    function start() {
        if (initialized) return;
        initialized = true;

        console.log("ApplyPilot: Initializing userscript elements...");

        try {
            // CSS Styling for the floating button and toast notification
            const cssText = `
                #applypilot-btn {
                    position: fixed !important;
                    bottom: 20px !important;
                    right: 20px !important;
                    z-index: 2147483647 !important;
                    background: linear-gradient(135deg, #6366f1, #4f46e5) !important;
                    color: white !important;
                    border: none !important;
                    border-radius: 50px !important;
                    padding: 12px 24px !important;
                    font-family: system-ui, -apple-system, sans-serif !important;
                    font-size: 14px !important;
                    font-weight: 600 !important;
                    box-shadow: 0 4px 15px rgba(99, 102, 241, 0.4) !important;
                    cursor: pointer !important;
                    display: flex !important;
                    align-items: center !important;
                    gap: 8px !important;
                    transition: all 0.2s ease-in-out !important;
                    outline: none !important;
                }
                #applypilot-btn:hover {
                    transform: translateY(-2px) !important;
                    box-shadow: 0 6px 20px rgba(99, 102, 241, 0.5) !important;
                    background: linear-gradient(135deg, #4f46e5, #4338ca) !important;
                }
                #applypilot-btn:active {
                    transform: translateY(1px) !important;
                }
                #applypilot-btn.loading {
                    background: #475569 !important;
                    box-shadow: none !important;
                    cursor: not-allowed !important;
                }
                .applypilot-spinner {
                    width: 16px !important;
                    height: 16px !important;
                    border: 2px solid rgba(255,255,255,0.3) !important;
                    border-top-color: white !important;
                    border-radius: 50% !important;
                    animation: applypilot-spin 0.8s linear infinite !important;
                    display: none !important;
                }
                #applypilot-btn.loading .applypilot-spinner {
                    display: inline-block !important;
                }
                #applypilot-btn.loading .applypilot-icon {
                    display: none !important;
                }
                @keyframes applypilot-spin {
                    to { transform: rotate(360deg); }
                }
                
                .applypilot-toast {
                    position: fixed !important;
                    bottom: 80px !important;
                    right: 20px !important;
                    z-index: 2147483647 !important;
                    background: #1e293b !important;
                    color: #f1f5f9 !important;
                    padding: 10px 18px !important;
                    border-radius: 8px !important;
                    font-family: system-ui, -apple-system, sans-serif !important;
                    font-size: 13px !important;
                    box-shadow: 0 4px 12px rgba(0,0,0,0.3) !important;
                    border-left: 4px solid #6366f1 !important;
                    opacity: 0 !important;
                    transform: translateY(10px) !important;
                    transition: all 0.3s ease !important;
                    pointer-events: none !important;
                }
                .applypilot-toast.show {
                    opacity: 1 !important;
                    transform: translateY(0) !important;
                    pointer-events: auto !important;
                }
                .applypilot-toast.error {
                    border-left-color: #ef4444 !important;
                }
                .applypilot-toast.success {
                    border-left-color: #10b981 !important;
                }
            `;

            // Inject styling using GM_addStyle to bypass strict CSP on sites like Google Docs/Forms.
            // Fall back to a standard style element if GM_addStyle is not available.
            if (typeof GM_addStyle !== 'undefined') {
                GM_addStyle(cssText);
            } else {
                const style = document.createElement('style');
                style.textContent = cssText; // Safe under Trusted Types (does not use innerHTML)
                document.head.appendChild(style);
            }
        } catch (styleErr) {
            console.warn("ApplyPilot: CSS injection failed, relying on inline styles:", styleErr);
        }

        // Setup the button content programmatically (safe under Trusted Types)
        btn.id = 'applypilot-btn';
        
        const spinner = document.createElement('span');
        spinner.className = 'applypilot-spinner';
        
        const icon = document.createElement('span');
        icon.className = 'applypilot-icon';
        icon.textContent = '⚡';
        
        const text = document.createElement('span');
        text.className = 'applypilot-text';
        text.textContent = 'AI Fill';

        btn.appendChild(spinner);
        btn.appendChild(icon);
        btn.appendChild(text);

        // Setup the toast content
        toast.className = 'applypilot-toast';

        // Apply inline style properties as a direct fallback to guarantee the elements are positioned
        // correctly and visible even if CSP blocks style sheets/style tags completely.
        btn.style.setProperty('position', 'fixed', 'important');
        btn.style.setProperty('bottom', '20px', 'important');
        btn.style.setProperty('right', '20px', 'important');
        btn.style.setProperty('z-index', '2147483647', 'important');
        btn.style.setProperty('background', 'linear-gradient(135deg, #6366f1, #4f46e5)', 'important');
        btn.style.setProperty('color', 'white', 'important');
        btn.style.setProperty('border', 'none', 'important');
        btn.style.setProperty('border-radius', '50px', 'important');
        btn.style.setProperty('padding', '12px 24px', 'important');
        btn.style.setProperty('font-family', 'system-ui, -apple-system, sans-serif', 'important');
        btn.style.setProperty('font-size', '14px', 'important');
        btn.style.setProperty('font-weight', '600', 'important');
        btn.style.setProperty('box-shadow', '0 4px 15px rgba(99, 102, 241, 0.4)', 'important');
        btn.style.setProperty('cursor', 'pointer', 'important');
        btn.style.setProperty('display', 'flex', 'important');
        btn.style.setProperty('align-items', 'center', 'important');
        btn.style.setProperty('gap', '8px', 'important');
        btn.style.setProperty('outline', 'none', 'important');

        toast.style.setProperty('position', 'fixed', 'important');
        toast.style.setProperty('bottom', '80px', 'important');
        toast.style.setProperty('right', '20px', 'important');
        toast.style.setProperty('z-index', '2147483647', 'important');
        toast.style.setProperty('background', '#1e293b', 'important');
        toast.style.setProperty('color', '#f1f5f9', 'important');
        toast.style.setProperty('padding', '10px 18px', 'important');
        toast.style.setProperty('border-radius', '8px', 'important');
        toast.style.setProperty('font-family', 'system-ui, -apple-system, sans-serif', 'important');
        toast.style.setProperty('font-size', '13px', 'important');
        toast.style.setProperty('box-shadow', '0 4px 12px rgba(0,0,0,0.3)', 'important');
        toast.style.setProperty('border-left', '4px solid #6366f1', 'important');
        toast.style.setProperty('opacity', '0', 'important');
        toast.style.setProperty('transform', 'translateY(10px)', 'important');
        toast.style.setProperty('pointer-events', 'none', 'important');

        // Attach button + toast to body, and re-attach if an SPA (e.g. Angular/React)
        // re-renders and removes them from the DOM.
        function attachToBody() {
            try {
                if (document.body) {
                    if (!document.body.contains(btn)) document.body.appendChild(btn);
                    if (!document.body.contains(toast)) document.body.appendChild(toast);
                }
            } catch (err) {
                console.error("ApplyPilot: attachToBody failed", err);
            }
        }
        attachToBody();

        // Watch for DOM mutations that detach the button (SPAs like Google Forms rebuild the body)
        try {
            const _reattachObserver = new MutationObserver(attachToBody);
            _reattachObserver.observe(document.body, { childList: true });
        } catch (err) {
            console.error("ApplyPilot: mutation observer setup failed", err);
        }
    }

    function startWhenReady() {
        if (document.body) {
            start();
        } else {
            console.log("ApplyPilot: document.body not ready, setting up listeners/observers...");
            // Use MutationObserver on documentElement
            const bodyObserver = new MutationObserver((mutations, obs) => {
                if (document.body) {
                    obs.disconnect();
                    start();
                }
            });
            bodyObserver.observe(document.documentElement, { childList: true, subtree: true });

            // DOMContentLoaded fallback
            window.addEventListener('DOMContentLoaded', () => {
                bodyObserver.disconnect();
                start();
            }, { once: true });

            // setInterval hard fallback
            const interval = setInterval(() => {
                if (document.body) {
                    clearInterval(interval);
                    bodyObserver.disconnect();
                    start();
                }
            }, 50);
        }
    }

    startWhenReady();

    // ── Google Forms detection + field helpers ──────────────────────────────

    function isGoogleForms() {
        return (
            (location.hostname === 'docs.google.com' && location.pathname.includes('/forms/')) ||
            location.hostname === 'forms.gle'
        );
    }

    // Build a field list from Google Forms' ARIA structure.
    // Each question lives in a div[role="listitem"]; answers are radio/checkbox
    // divs, text inputs with class "whsOnd", or textareas.
    function injectIdsAndGetFieldsGoogleForms() {
        const elements = [];
        let count = 0;
        
        // Find question container cards using multiple selector strategies for past/current/future layouts
        const questions = document.querySelectorAll(
            'div[role="listitem"], div.geS5ne, .M7eMe, div[role="question"], div[class*="question"]'
        );

        questions.forEach(qEl => {
            const titleEl = qEl.querySelector('[role="heading"], .freebirdFormviewerViewItemsItemItemTitle, [class*="title"], [class*="header"]');
            const labelText = titleEl ? titleEl.textContent.trim() : '';

            // Short answer (text input)
            const textInput = qEl.querySelector(
                'input.whsOnd, input[type="text"]:not([type="hidden"]), ' +
                'input[type="email"], input[type="number"], input[type="url"], ' +
                'input[type="tel"], input[type="date"]'
            );
            if (textInput) {
                textInput.setAttribute('data-ap-id', count);
                elements.push({ tagName: 'input', type: textInput.type || 'text', name: textInput.name || '',
                    id: textInput.id || '', placeholder: textInput.placeholder || '', label: labelText,
                    options: [], selector: `[data-ap-id="${count}"]`, _gforms: 'text' });
                count++; return;
            }

            // Paragraph (long answer)
            const textarea = qEl.querySelector('textarea');
            if (textarea) {
                textarea.setAttribute('data-ap-id', count);
                elements.push({ tagName: 'textarea', type: 'textarea', name: textarea.name || '',
                    id: textarea.id || '', placeholder: textarea.placeholder || '', label: labelText,
                    options: [], selector: `[data-ap-id="${count}"]`, _gforms: 'textarea' });
                count++; return;
            }

            // Multiple choice (radio)
            const radios = qEl.querySelectorAll('div[role="radio"]');
            if (radios.length > 0) {
                const options = Array.from(radios).map(r => ({ value: r.textContent.trim(), text: r.textContent.trim() }));
                qEl.setAttribute('data-ap-id', count);
                elements.push({ tagName: 'div', type: 'radio-group', name: '', id: '', placeholder: '',
                    label: labelText, options, selector: `[data-ap-id="${count}"]`, _gforms: 'radio' });
                count++; return;
            }

            // Checkboxes
            const checkboxes = qEl.querySelectorAll('div[role="checkbox"]');
            if (checkboxes.length > 0) {
                const options = Array.from(checkboxes).map(c => ({ value: c.textContent.trim(), text: c.textContent.trim() }));
                qEl.setAttribute('data-ap-id', count);
                elements.push({ tagName: 'div', type: 'checkbox-group', name: '', id: '', placeholder: '',
                    label: labelText, options, selector: `[data-ap-id="${count}"]`, _gforms: 'checkbox' });
                count++; return;
            }

            // Dropdown (select)
            const select = qEl.querySelector('select');
            if (select) {
                const options = Array.from(select.options).map(o => ({ value: o.value, text: o.text.trim() }));
                select.setAttribute('data-ap-id', count);
                elements.push({ tagName: 'select', type: 'select', name: select.name || '',
                    id: select.id || '', placeholder: '', label: labelText,
                    options, selector: `[data-ap-id="${count}"]`, _gforms: 'select' });
                count++;
            }
        });

        // Robust Fallback: If question containers could not be parsed, scan all input/textarea/select elements directly
        if (elements.length === 0) {
            console.log("ApplyPilot: Google Forms question-based grouping found 0 fields. Falling back to direct input scanning...");
            const rawInputs = document.querySelectorAll('input, textarea, select');
            rawInputs.forEach(el => {
                const type = el.type || '';
                const tagName = el.tagName.toLowerCase();

                // Skip utility/action/hidden elements
                if (el.disabled || type === 'hidden' || type === 'submit' || type === 'button' || type === 'file') {
                    return;
                }

                // Skip elements hidden from view (Google Forms sometimes renders off-screen widgets but we want the actual input)
                const style = window.getComputedStyle(el);
                if (tagName === 'textarea' || (tagName === 'input' && (type === 'text' || type === 'email' || type === 'tel' || type === 'number' || type === 'date'))) {
                    if (style.display === 'none' || style.visibility === 'hidden' || el.offsetParent === null) {
                        return;
                    }
                }

                el.setAttribute('data-ap-id', count);
                const selector = `[data-ap-id="${count}"]`;

                // Try to find a label or question text around the element
                let labelText = el.getAttribute('aria-label') || el.placeholder || el.name || el.id || '';
                if (!labelText) {
                    // Look up the DOM tree for potential heading or label elements
                    let parent = el.parentElement;
                    for (let depth = 0; depth < 4 && parent; depth++) {
                        const heading = parent.querySelector('[role="heading"], label, [class*="title"], [class*="question"]');
                        if (heading && heading !== el) {
                            labelText = heading.textContent.trim();
                            if (labelText) break;
                        }
                        parent = parent.parentElement;
                    }
                }
                if (!labelText) {
                    labelText = `Field ${count + 1}`;
                }

                let gtype = 'text';
                if (tagName === 'textarea') gtype = 'textarea';
                else if (type === 'radio' || el.getAttribute('role') === 'radio') gtype = 'radio';
                else if (type === 'checkbox' || el.getAttribute('role') === 'checkbox') gtype = 'checkbox';
                else if (tagName === 'select') gtype = 'select';

                elements.push({
                    tagName: tagName,
                    type: type,
                    name: el.name || '',
                    id: el.id || '',
                    placeholder: el.placeholder || '',
                    label: labelText,
                    options: [],
                    selector: selector,
                    _gforms: gtype
                });
                count++;
            });
        }

        return elements;
    }

    // Fill a single Google Forms field. Angular intercepts plain .value assignments,
    // so we call the native HTMLInputElement/TextAreaElement prototype setter.
    function fillGoogleFormsField(el, field, value) {
        if (!el || value == null) return false;
        const gtype = field._gforms;

        if (gtype === 'text' || gtype === 'textarea') {
            const proto = gtype === 'textarea' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
            Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, String(value));
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
            return true;
        }

        if (gtype === 'radio') {
            const target = String(value).toLowerCase();
            for (const radio of el.querySelectorAll('div[role="radio"]')) {
                const text = radio.textContent.trim().toLowerCase();
                if (text === target || text.includes(target) || target.includes(text)) {
                    radio.click(); return true;
                }
            }
            return false;
        }

        if (gtype === 'checkbox') {
            const wanted = (Array.isArray(value) ? value : [value]).map(v => String(v).toLowerCase());
            let hit = false;
            for (const cb of el.querySelectorAll('div[role="checkbox"]')) {
                const text = cb.textContent.trim().toLowerCase();
                const should = wanted.some(v => text === v || text.includes(v) || v.includes(text));
                const checked = cb.getAttribute('aria-checked') === 'true';
                if (should && !checked) { cb.click(); hit = true; }
            }
            return hit;
        }

        if (gtype === 'select') {
            el.value = value;
            el.dispatchEvent(new Event('change', { bubbles: true }));
            return true;
        }
        return false;
    }

    function showToast(message, type = 'info') {
        toast.textContent = message;
        toast.className = `applypilot-toast show ${type}`;
        setTimeout(() => {
            toast.classList.remove('show');
        }, 4000);
    }

    // Injects data-ap-id to establish unique selectors and returns field list.
    // Delegates to the Google Forms variant when on docs.google.com/forms.
    function injectIdsAndGetFields() {
        if (isGoogleForms()) return injectIdsAndGetFieldsGoogleForms();
        const elements = [];
        const inputs = document.querySelectorAll('input, select, textarea');
        let count = 0;
        
        inputs.forEach(el => {
            const type = el.type || '';
            const tagName = el.tagName.toLowerCase();
            const style = window.getComputedStyle(el);
            
            // Skip genuinely disabled inputs or buttons/submits/files
            if (el.disabled || type === 'hidden' || type === 'submit' || type === 'button' || type === 'file') {
                return;
            }
            
            // For text-like inputs, skip if they are hidden from view
            if (tagName === 'textarea' || (tagName === 'input' && (type === 'text' || type === 'email' || type === 'tel' || type === 'password' || type === 'url' || type === 'number' || type === 'date' || type === ''))) {
                if (style.display === 'none' || style.visibility === 'hidden' || el.offsetParent === null) {
                    return;
                }
            }
            // For selects, checkboxes, and radio buttons, we do NOT skip even if display: none or offsetParent === null,
            // because LinkedIn and other job boards routinely hide raw inputs to render custom styled widgets.
            
            // Set unique ID attribute
            el.setAttribute('data-ap-id', count);
            const selector = `[data-ap-id="${count}"]`;
            count++;
            
            // Look for associated label text
            let labelText = "";
            if (el.id) {
                const label = document.querySelector(`label[for="${el.id}"]`);
                if (label) labelText = label.textContent.trim();
            }
            if (!labelText) {
                const label = el.closest('label');
                if (label) labelText = label.textContent.trim();
            }
            if (!labelText) {
                // Search parent container hierarchy (common in LinkedIn Easy Apply forms)
                let parent = el.parentElement;
                for (let depth = 0; depth < 3 && parent; depth++) {
                    const potentialLabel = parent.querySelector('label, legend, [class*="label"], [class*="title"], [class*="question"]');
                    if (potentialLabel && potentialLabel !== el) {
                        labelText = potentialLabel.textContent.trim();
                        if (labelText) break;
                    }
                    parent = parent.parentElement;
                }
            }
            if (!labelText && el.placeholder) {
                labelText = el.placeholder;
            }
            if (!labelText && el.name) {
                labelText = el.name;
            }
            
            // Get select dropdown options
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

    // Clean up temporary attributes from inputs
    function clearTemporaryAttributes() {
        document.querySelectorAll('[data-ap-id]').forEach(el => {
            el.removeAttribute('data-ap-id');
        });
    }

    // Automatically fill fields using the returned mapping.
    // For Google Forms fields that carry _gforms metadata, delegates to fillGoogleFormsField.
    function fillFormFields(mapping, fields) {
        let filledCount = 0;

        for (const [selector, value] of Object.entries(mapping)) {
            if (!selector || value === undefined || value === null) continue;

            try {
                // Check if this field is a Google Forms ARIA element
                const originalField = fields ? fields.find(f => f.selector === selector) : null;
                if (originalField && originalField._gforms) {
                    const el = document.querySelector(selector);
                    if (el && fillGoogleFormsField(el, originalField, value)) {
                        filledCount++;
                    }
                    continue;
                }

                // 1. Try to find the element using the selector directly (e.g. [data-ap-id="X"])
                let els = document.querySelectorAll(selector);
                
                // 2. Fallback: If not found, try using other unique attributes from the original fields definition
                if (els.length === 0 && fields) {
                    const originalField = fields.find(f => f.selector === selector);
                    if (originalField) {
                        const fallbacks = [];
                        if (originalField.id) {
                            fallbacks.push(`#${CSS.escape(originalField.id)}`);
                        }
                        if (originalField.name) {
                            fallbacks.push(`${originalField.tagName}[name="${CSS.escape(originalField.name)}"]`);
                        }
                        if (originalField.placeholder) {
                            fallbacks.push(`${originalField.tagName}[placeholder="${CSS.escape(originalField.placeholder)}"]`);
                        }
                        
                        for (const fbSelector of fallbacks) {
                            els = document.querySelectorAll(fbSelector);
                            if (els.length > 0) {
                                console.log(`ApplyPilot: Fallback matched element using selector: ${fbSelector}`);
                                break;
                            }
                        }
                    }
                }
                
                if (els.length === 0) {
                    console.warn(`ApplyPilot: Could not find any element matching selector ${selector}`);
                    continue;
                }
                
                els.forEach(el => {
                    const tagName = el.tagName.toLowerCase();
                    const type = el.type || '';
                    
                    if (tagName === 'select') {
                        el.value = value;
                        el.dispatchEvent(new Event('change', { bubbles: true }));
                        filledCount++;
                    } else if (type === 'checkbox' || type === 'radio') {
                        const shouldCheck = (value === true || value === 'true' || value === '1' || value === 'yes');
                        if (el.checked !== shouldCheck) {
                            el.click();
                            // If clicking didn't change the checked state (e.g., hidden inputs), force it and dispatch
                            if (el.checked !== shouldCheck) {
                                el.checked = shouldCheck;
                                el.dispatchEvent(new Event('change', { bubbles: true }));
                            }
                            filledCount++;
                        }
                    } else {
                        el.value = value;
                        el.dispatchEvent(new Event('input', { bubbles: true }));
                        el.dispatchEvent(new Event('change', { bubbles: true }));
                        filledCount++;
                    }
                });
            } catch (ex) {
                console.error(`ApplyPilot: failed to fill ${selector}`, ex);
            }
        }
        return filledCount;
    }

    // Poll the status API endpoint until the Qwen task completes
    function pollTaskStatus(taskId, startTime, fields) {
        let elapsed = Math.round((Date.now() - startTime) / 1000);
        btn.querySelector('.applypilot-text').textContent = `Thinking (${elapsed}s)...`;

        GM_xmlhttpRequest({
            method: 'GET',
            url: `http://localhost:8089/api/status?task_id=${taskId}`,
            onload: function(response) {
                try {
                    const res = JSON.parse(response.responseText);
                    if (res.status === 'completed' && res.mapping) {
                        btn.classList.remove('loading');
                        btn.querySelector('.applypilot-text').textContent = 'AI Fill';
                        
                        const filled = fillFormFields(res.mapping, fields);
                        clearTemporaryAttributes();
                        showToast(`Successfully filled ${filled} fields!`, 'success');
                    } else if (res.status === 'error') {
                        btn.classList.remove('loading');
                        btn.querySelector('.applypilot-text').textContent = 'AI Fill';
                        clearTemporaryAttributes();
                        showToast(`Error: ${res.message || 'Processing failed'}`, 'error');
                    } else {
                        // Still processing, poll again in 2 seconds
                        setTimeout(() => pollTaskStatus(taskId, startTime, fields), 2000);
                    }
                } catch (e) {
                    btn.classList.remove('loading');
                    btn.querySelector('.applypilot-text').textContent = 'AI Fill';
                    clearTemporaryAttributes();
                    showToast('Failed to parse response during polling.', 'error');
                    console.error(e);
                }
            },
            onerror: function(err) {
                btn.classList.remove('loading');
                btn.querySelector('.applypilot-text').textContent = 'AI Fill';
                clearTemporaryAttributes();
                showToast('Polling error. Lost connection to local ApplyPilot server.', 'error');
                console.error(err);
            }
        });
    }

    // Trigger AI Fill click handler
    btn.addEventListener('click', function() {
        if (btn.classList.contains('loading')) return;
        
        try {
            console.log("ApplyPilot: Button clicked. Scanning fields...");
            // Setup data-ap-id mapping and get visible fields
            const fields = injectIdsAndGetFields();
            console.log("ApplyPilot: Detected fields:", fields);
            
            if (fields.length === 0) {
                showToast('No fillable form fields detected.', 'error');
                return;
            }
            
            btn.classList.add('loading');
            btn.querySelector('.applypilot-text').textContent = 'Submitting...';
            showToast(`Submitting ${fields.length} fields to ApplyPilot...`);

            console.log("ApplyPilot: Submitting payload to local server...");
            
            GM_xmlhttpRequest({
                method: 'POST',
                url: 'http://localhost:8089/api/fill',
                headers: {
                    'Content-Type': 'application/json'
                },
                data: JSON.stringify({ fields: fields }),
                onload: function(response) {
                    try {
                        console.log("ApplyPilot: Server response status:", response.status);
                        const res = JSON.parse(response.responseText);
                        console.log("ApplyPilot: Server parsed response:", res);
                        
                        if (res.status === 'processing' && res.task_id) {
                            // Start polling the server
                            pollTaskStatus(res.task_id, Date.now(), fields);
                        } else {
                            btn.classList.remove('loading');
                            btn.querySelector('.applypilot-text').textContent = 'AI Fill';
                            clearTemporaryAttributes();
                            showToast(`Error starting task: ${res.message || 'Unknown error'}`, 'error');
                        }
                    } catch (e) {
                        btn.classList.remove('loading');
                        btn.querySelector('.applypilot-text').textContent = 'AI Fill';
                        clearTemporaryAttributes();
                        showToast('Failed to parse server response.', 'error');
                        console.error("ApplyPilot parser error:", e, response.responseText);
                    }
                },
                onerror: function(err) {
                    btn.classList.remove('loading');
                    btn.querySelector('.applypilot-text').textContent = 'AI Fill';
                    clearTemporaryAttributes();
                    showToast('Cannot connect to local ApplyPilot server. Run "applypilot dashboard".', 'error');
                    console.error("ApplyPilot network error:", err);
                }
            });
            
        } catch (clickErr) {
            console.error("ApplyPilot Click Error:", clickErr);
            alert("ApplyPilot Click Error: " + clickErr.message + "\nStack: " + clickErr.stack);
        }
    });
})();
