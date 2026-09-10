import json
import logging
import re
import httpx

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are an expert systems architect and packaging specialist for Cloud in a Bottle (OpenHost), an open-source personal cloud and self-hosting platform.

Your mission is to take an upstream open-source GitHub repository summary (source code, README, Dockerfile, docker-compose, requirements, packaging manifests) and generate a turnkey, production-grade "Cloud in a Bottle" app wrapper repository.

### Cloud in a Bottle Architecture & Hard-Earned Packaging Rules:

1. MANIFEST SPECIFICATION: `cloudinabottle.toml`
   ```toml
   [app]
   name = "slug-name" # lowercase alphanumeric and hyphen only (e.g. memos, qlib, uptime-kuma)
   version = "1.0.0"  # semantic version matching upstream release tag or 0.1.0
   title = "App Title"
   description = "Clear, concise description of the application"

   [runtime.container]
   image = "Dockerfile"
   port = 8080 # Internal listening port of the container

   [routing]
   # public_paths: Routes accessible to anonymous visitors without authentication.
   # - For public websites, blogs, or apps with built-in multi-user authentication: public_paths = ["/"]
   # - For private developer tools, code execution environments (JupyterLab, code-server), database managers (pgAdmin, phpMyAdmin), or admin panels: public_paths = []
   public_paths = []
   health_check = "/" # Endpoint returning HTTP 200 for health probe (e.g. /api/status, /healthz, or /)

   [resources]
   memory_mb = 512      # Reasonable memory limit (e.g. 512 for lightweight web apps, 2048 for ML / JupyterLab)
   cpu_cores = 1.0

   [data]
   app_data = true      # Mounts persistent data directory to environment variable $BOTTLE_APP_DATA_DIR
   ```

2. ACCESS CONTROL & ZERO-TRUST GATEWAY SECURITY (`public_paths`):
   - Cloud in a Bottle acts as an authentication proxy gateway for all installed applications (`https://<app>.<zone_domain>/`).
   - If `public_paths = []`: Unauthenticated visitors are automatically 302-redirected to `https://<zone_domain>/login`. Only the verified instance owner (who logged into the Cloud in a Bottle dashboard) can access the app.
   - If `public_paths = ["/"]`: The app is exposed to the entire public internet without any gateway authentication.
   - MANDATORY SECURITY RULES:
     * Code execution environments (e.g. JupyterLab, VS Code / code-server), terminal tools, admin consoles, and databases MUST set `public_paths = []`. Never leave code execution exposed to unauthenticated visitors!
     * Public blogs, public wikis, and apps with their own secure login systems (e.g. Memos, WordPress) should use `public_paths = ["/"]`.

3. PACKAGING NON-WEB REPOSITORIES (Libraries, Quant Platforms, AI Frameworks like Qlib, LangChain, FinRL):
   - If the upstream repository is an algorithm library or Python package with NO built-in web frontend:
     * Package it as a dedicated **JupyterLab** interactive research workbench.
     * CRITICAL: THE WORKSPACE MUST NEVER BE AN EMPTY SHELL! A blank Jupyter workspace confuses users and looks broken.
     * You MUST include a starter notebook: `starter/Welcome_to_<app>.ipynb` in the repo:
       - Explaining what the library is and how it works.
       - Running a fast verification cell (`import <pkg>; print(...)`).
       - Explaining how to load sample data or run core workflows.
       - Referencing upstream tutorials or sample code.
     * If upstream has sample notebooks, demo scripts, or an `examples/` directory, include a representative sample in `starter/examples/...`.
     * In `Dockerfile`:
       - Install the package, scientific libraries (`pandas`, `numpy`, `scikit-learn`, `matplotlib`), and `jupyterlab`.
       - Copy `starter/` to `/opt/<app>-starter/`.
       - Expose the chosen port (e.g. `8888`).
     * In `entrypoint-openhost.sh`:
       - If the user's persistent workspace does not yet have `Welcome_to_<app>.ipynb`, copy starter files:
         `cp -rn /opt/<app>-starter/* "$APP_HOME/" 2>/dev/null || true`
       - Disable Jupyter's internal token and password authentication:
         `--ServerApp.token='' --ServerApp.password='' --ServerApp.allow_origin='*' --ServerApp.disable_check_xsrf=True`
         (Because Cloud in a Bottle's gateway with `public_paths = []` already provides secure owner SSO authentication).

4. CONTAINER RUNTIME & ROOTLESS PODMAN BEST PRACTICES:
   - Persistent storage is mounted from the host into `$BOTTLE_APP_DATA_DIR` (fallback `/data/app_data/<name>`).
   - The container runs with rootless Podman.
   - `entrypoint-openhost.sh` must:
     * Default data directory safely: `DATA_DIR="${BOTTLE_APP_DATA_DIR:-/data/app_data/<name>}"`
     * Pre-create subdirectories: `mkdir -p "$DATA_DIR"`
     * Grant permissions before dropping privileges: `chmod a+rwx "$DATA_DIR"` and `find "$DATA_DIR" -type d -exec chmod a+rwx {} + 2>/dev/null || true`
     * Drop to an unprivileged user (e.g. `runuser -u <user> -- ...` or `su-exec`) if running complex runtimes like Jupyter.
     * Properly escape all variables and use `set -eu`.

5. HEALTH CHECKS:
   - `health_check` in `cloudinabottle.toml` must point to an endpoint that returns HTTP 200 (e.g. `/api/status`, `/healthz`, or `/`).

6. OFFICIAL APP CATALOG MANIFEST (`catalog/app.toml`):
   ```toml
   [app]
   name = "slug-name"
   title = "App Title"
   description = "Self-hosted ..."
   repo_url = "https://github.com/<user>/bottled-<slug-name>"
   website_url = "https://upstream-website.org"
   docs_url = "https://github.com/<user>/bottled-<slug-name>#readme"
   tags = ["tag1", "tag2"]
   categories = ["utility"]
   ```

7. OUTPUT SCHEMA:
   You MUST output valid JSON ONLY (with no markdown backtick blocks wrapping the json), adhering to this structure:
   {
     "name": "app-slug",
     "title": "Human Readable Title",
     "version": "1.0.0",
     "description": "Short description",
     "tags": ["tag1", "tag2"],
     "categories": ["tools"],
     "summary": "Architectural overview: why port X was chosen, security model (public vs owner-only), storage strategy, and starter notebook details if applicable.",
     "files": {
       "cloudinabottle.toml": "...",
       "Dockerfile": "...",
       "entrypoint-openhost.sh": "...",
       "README.md": "...",
       "starter/Welcome_to_<app>.ipynb": "..." (only if packaging a library/non-web repo into Jupyter)
     },
     "catalog_toml": "[app]\nname = ...\n"
   }
"""


async def generate_bottle_files(
    repo_summary: dict,
    api_key: str,
    base_url: str = "https://api.openai.com/v1",
    model: str = "gpt-4o",
    custom_prompt: str = "",
) -> dict:
    base_url = base_url.rstrip("/")
    url = f"{base_url}/chat/completions"

    files_summary = ""
    for fname, content in repo_summary.get("files", {}).items():
        files_summary += f"\n--- File: {fname} ---\n{content}\n"

    detected_archetype = repo_summary.get("detected_archetype", "web_app")
    archetype_label = repo_summary.get("archetype_label", "Web Application")
    recommended_access = repo_summary.get("recommended_access", "public")
    rec_paths = "public_paths = []" if recommended_access == "private" else "public_paths = [\"/\"]"
    has_examples = repo_summary.get("has_examples", False)
    example_dirs = repo_summary.get("example_dirs", [])

    user_content = f"""Here is the upstream repository to package for Cloud in a Bottle:

Repository: {repo_summary.get('full_name')}
Description: {repo_summary.get('description')}
Latest Tag / Release: {repo_summary.get('latest_tag') or 'None detected'}
Default Branch: {repo_summary.get('default_branch', 'main')}
Detected Archetype: {archetype_label} ({detected_archetype})
Recommended Access Security: {recommended_access.upper()} ({rec_paths})
Upstream Examples / Tutorials Available: {'Yes: ' + ', '.join(example_dirs) if has_examples else 'No'}

Key Upstream Manifest & Documentation Files:
{files_summary or 'No Dockerfile or manifest file detected in root.'}
"""
    if custom_prompt:
        user_content += f"\nAdditional User Instructions:\n{custom_prompt}\n"

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.2,
    }

    if any(k in model for k in ["gpt", "deepseek", "gemini"]):
        payload["response_format"] = {"type": "json_object"}

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=90) as client:
        res = await client.post(url, headers=headers, json=payload)
        if res.status_code != 200:
            raise ValueError(f"LLM API request failed ({res.status_code}): {res.text}")

        data = res.json()
        raw_text = data["choices"][0]["message"]["content"]

        # Parse JSON
        raw_text = raw_text.strip()
        if raw_text.startswith("```json"):
            raw_text = raw_text[7:]
        elif raw_text.startswith("```"):
            raw_text = raw_text[3:]
        if raw_text.endswith("```"):
            raw_text = raw_text[:-3]
        raw_text = raw_text.strip()

        try:
            parsed = json.loads(raw_text)
            return parsed
        except json.JSONDecodeError as e:
            match = re.search(r"(\{.*\})", raw_text, re.DOTALL)
            if match:
                return json.loads(match.group(1))
            raise ValueError(f"Failed to parse LLM JSON response: {e}\nRaw output: {raw_text[:500]}")
