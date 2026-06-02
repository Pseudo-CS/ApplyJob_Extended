# ApplyPilot (Enhanced Version)

This repository is an enhanced, optimized fork of the initial open-source [ApplyPilot](https://github.com/Pickle-Pixel/ApplyPilot) project. It adds web-based orchestration, safe multi-profile management, persistent rejection tracking, local LLM support, and a semi-automatic form-filling browser userscript.

---

## 🌟 Upgrades Compared to the Original Project

*   **Interactive Web Dashboard**: Replaces command-line execution with a visual control panel to upload resumes, switch or create profiles, generate configurations via AI parsing, and monitor pipeline statistics.
*   **Active Profile Protection**: Intelligently locks the currently active profile and the last remaining profile to prevent accidental deletion and database corruption.
*   **Persistent Rejection Tracking**: Saves **all evaluated jobs** to a local SQLite database, including those below the threshold score. Subsequent discovery runs check the DB and automatically skip previously evaluated URLs, saving significant API token costs.
*   **AI Rejection Summary**: Aggregates feedback from rejected postings and uses an LLM to generate an actionable, second-person summary of missing skills and resume gaps.
*   **Safe Execution Cap**: Restricts manual discovery loops to a default cap of 50 jobs per run to avoid rate limits and runaway API bills.

---

## 🖥️ Local LLM Support (Discovery & Scoring)

ApplyPilot supports running the discovery, details enrichment, and fit scoring stages completely offline and free. You can target any OpenAI-compatible API endpoint (such as a local **Ollama** or **llama.cpp** server) by adding the following to your `~/.applypilot/.env` configuration:

```ini
LLM_URL=http://localhost:11434/v1    # Your local endpoint URL
LLM_MODEL=llama3                     # Model tag (Ollama, etc.)
```

---

## 🧩 Semi-Automatic Applying (Tampermonkey Userscript)

For users who do not have the `claude` CLI installed, or prefer not to grant terminal/browser control to an autonomous agent, ApplyPilot provides a **semi-automatic application extension** via a Tampermonkey userscript ([userscript.js](file:///home/pseudo/ApplyPilot/userscript.js)).

### How it Works:
1.  **Install the Script**: Load `userscript.js` into a browser script manager (like Tampermonkey).
2.  **Start the Dashboard**: Launch `applypilot dashboard` to start the local backend server (listening on port `8089`).
3.  **Navigate & Fill**: Open any job application or Google Form. A floating **"AI Fill"** button will appear in the bottom-right corner.
4.  **Auto-Populate**: Click the button. The script gathers the page's input fields, sends them to the local server, and utilizes your active profile facts and LLM to fill out the form fields in real-time.
5.  **Review & Submit**: Review the auto-populated answers manually before clicking submit.
