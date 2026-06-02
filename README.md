# ApplyPilot (Enhanced Version)

**An autonomous job application pipeline that discovers, scores, tailors, and auto-submits applications for you.**

This repository is an enhanced fork of the original open-source [ApplyPilot](https://github.com/Pickle-Pixel/ApplyPilot) project. It introduces several major stability, UI/UX, and architectural upgrades that make manual and autonomous job hunting more reliable, rate-limit friendly, and visually stunning.

---

## ⚡ What is ApplyPilot?

ApplyPilot is a 6-stage autonomous job application agent:
1. **Discover**: Scrapes 5+ job boards (Indeed, LinkedIn, Glassdoor, ZipRecruiter, Google Jobs) and 48+ Workday/employer portals.
2. **Enrich**: Automatically extracts full job descriptions via cascading scraper rules or LLM extraction.
3. **Score**: Evaluates every job against your resume and profile facts using AI, rating it 1–10.
4. **Tailor**: Re-writes your resume per job, emphasizing matching skills and experience without fabricating facts.
5. **Cover Letter**: Generates targeted, custom cover letters for each job.
6. **Auto-Apply**: Navigates application forms, uploads tailored documents, answers screening questions, and submits.

---

## 🚀 Key Enhancements (What the Original Didn't Have)

This fork introduces key features and bug fixes that solve major usability bottlenecks in the original codebase:

### 🎨 Custom Aesthetic UI Elements (No More Native Popups)
* **Custom Toast Notifications**: Native browser `alert()` popups have been replaced with non-blocking, beautifully animated custom Toast notifications. They are color-coded (Green for success, Red for errors, Blue for info) and automatically fade out.
* **Custom Confirm Modals**: Browser `confirm()` prompts have been replaced with custom promise-based Confirm Modals styled directly into the dashboard theme, creating a premium web application experience.

### 👥 Safe Profile Deletion & Management
* **Active Profile Protection**: Fixed a major bug in the original UI where deleting a profile was impossible because the active profile dropdown always selected the active profile (and clicking delete tried to delete the active profile, which the backend blocks).
* **Profile Deletion Modal**: Added a "Delete Profile" modal that intelligently filters out the active profile, letting you select and delete any of your other profiles securely.

### 💾 Rejection Feedback & Token Savings (Manual Discovery)
* **Persistent Rejections**: In the original repo, manual discovery dropped rejected jobs (score < min_score) immediately. This fork writes **all evaluated jobs** to the SQLite database, preserving their fit scores and rejection reasoning.
* **Smart Deduplication**: Since rejected jobs are now saved in the database, subsequent discovery runs skip these URLs. This prevents the LLM from wasting API tokens re-scoring the same positions on subsequent runs.
* **Detailed Dashboard Labels**: The dashboard now displays all scored jobs. Low-scoring jobs are categorized under `"Low Fit" (3-4)` and `"Poor Match" (1-2)` sections, allowing you to expand cards and inspect the rejection reasons.

### 🤖 AI-Powered Rejection Summary
* **Feedback Aggregator**: After every manual discovery run, the pipeline automatically selects 10 random rejection reasons and uses the LLM to write an aggregated, actionable summary of your missing skills or gaps (written in the second person).
* **Profile Integration**: This summary is saved directly inside your profile folder and displayed inside the **Profile Configuration** tab under the *"Rejection Reasons Summary"* card, giving you clear guidance on how to optimize your resume.
* **Real-time Logging**: Real-time logs now print detailed rejection summaries (`[Rejected] Score=3 | Reason: ...`) so you know exactly why the agent skipped a job during execution.

### ⚙️ Safe Execution Defaults
* **Evaluation Cap**: Set the default manual discovery cap to **50 jobs** per session. This protects against API rate limits (e.g. Gemini Free Tier's 15 RPM cap) and prevents run-away token costs.

---

## 🛠️ Requirements & Installation

1. **Python 3.11+**
2. **Node.js 18+** (Required for Playwright form auto-filling)
3. **Gemini API key** (Free tier from Google AI Studio is sufficient)
4. **Google Chrome**

### Installation

```bash
# Clone the repository
git clone https://github.com/YOUR_USERNAME/ApplyPilot.git
cd ApplyPilot

# Set up virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install .
pip install --no-deps python-jobspy && pip install pydantic tls-client requests markdownify regex
```

### Quick Start

```bash
applypilot init          # Run the configuration wizard to set up your profile, resume, and API keys
applypilot doctor        # Verify your environment is set up properly
applypilot dashboard     # Start the dashboard HTTP server and view progress
```

---

## 📄 Licensing

This project is licensed under the **GNU Affero General Public License v3.0 (AGPL-3.0)**, preserving the copyleft terms of the original ApplyPilot project. For details, see the [LICENSE](LICENSE) file.
