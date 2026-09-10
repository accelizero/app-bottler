import json
import logging
import re
import httpx

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are an expert systems engineer and packaging specialist for Cloud in a Bottle (OpenHost), an open-source personal cloud and self-hosting platform.

Your task is to take an upstream open-source GitHub repository summary (README, Dockerfile, docker-compose, release tag, repo info) and generate a turnkey "Cloud in a Bottle" app wrapper repository.

### Cloud in a Bottle Specification & Architecture:
1. Manifest: `cloudinabottle.toml`
   ```toml
   [app]
   name = "slug-name" # lowercase alphanumeric and hyphen only (e.g. memos, uptime-kuma)
   version = "1.0.0"  # semantic version matching upstream or 0.1.0
   title = "App Title"
   description = "Clear, concise description of the application"

   [runtime.container]
   image = "Dockerfile"
   port = 8080 # Internal listening port of the container

   [routing]
   public_paths = ["/"] # Routes accessible without authentication or base routing
   health_check = "/"   # Endpoint returning 200 OK for health probe

   [resources]
   memory_mb = 512      # Reasonable memory limit based on app footprint
   cpu_cores = 1

   [data]
   app_data = true      # Mounts persistent data directory to container environment variable $BOTTLE_APP_DATA_DIR
   ```

2. Container Runtime & Rootless Podman Best Practices:
   - Cloud in a Bottle runs rootless containers with Podman.
   - Persistent storage is mounted from host into `$BOTTLE_APP_DATA_DIR` (fallback to `/data/app_data/<name>`).
   - If upstream publishes official container images (e.g. `ghcr.io/...` or Docker Hub), prefer using a wrapper `Dockerfile`:
     ```dockerfile
     FROM <upstream_image>:<tag>
     USER root
     COPY entrypoint-openhost.sh /entrypoint-openhost.sh
     RUN chmod +x /entrypoint-openhost.sh
     ENTRYPOINT ["/entrypoint-openhost.sh"]
     ```
   - Provide an `entrypoint-openhost.sh` that prepares persistent directories:
     ```bash
     #!/bin/sh
     set -e

     DATA_DIR="${BOTTLE_APP_DATA_DIR:-/data}"
     mkdir -p "$DATA_DIR"
     # Symlink or copy any required upstream app data paths to $DATA_DIR so data survives restarts
     # Example: if upstream stores data in /var/opt/app, symlink /var/opt/app to $DATA_DIR
     # Ensure appropriate permissions if container runs as non-root user

     # Finally, invoke the original container command or start the service
     exec <original_entrypoint_or_cmd>
     ```
   - If no prebuilt image exists, write a clean multi-stage `Dockerfile`.

3. Official App Catalog Manifest (`apps/<name>/app.toml`):
   ```toml
   [app]
   name = "slug-name"
   title = "App Title"
   description = "Self-hosted ..."
   repo_url = "https://github.com/<user>/<slug-name>"
   website_url = "https://upstream-website.org"
   docs_url = "https://github.com/<user>/<slug-name>#readme"
   tags = ["tag1", "tag2"]
   categories = ["utility"]
   ```

4. Output Requirements:
   You MUST output valid JSON ONLY, with no extra markdown code block wrapping around the JSON, matching this schema:
   {
     "name": "app-slug",
     "title": "Human Readable Title",
     "version": "1.0.0",
     "description": "Short description",
     "tags": ["tag1", "tag2"],
     "categories": ["tools"],
     "summary": "Explanation of architectural decisions: port used, volume strategy, permissions handled.",
     "files": {
       "cloudinabottle.toml": "file content string",
       "Dockerfile": "file content string",
       "entrypoint-openhost.sh": "file content string",
       "README.md": "file content string"
     },
     "catalog_toml": "[app]\\nname = ...\\n"
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

    user_content = f"""Here is the upstream repository to package for Cloud in a Bottle:

Repository: {repo_summary.get('full_name')}
Description: {repo_summary.get('description')}
Latest Tag / Release: {repo_summary.get('latest_tag') or 'None detected'}
Default Branch: {repo_summary.get('default_branch', 'main')}

Key Upstream Files:
{files_summary or 'No Dockerfile or compose file detected in root.'}
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

    # If the model supports response_format json_object, attempt it
    if "gpt" in model or "deepseek" in model or "gemini" in model:
        payload["response_format"] = {"type": "json_object"}

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=60) as client:
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
            # Fallback regex search for { ... }
            match = re.search(r"(\{.*\})", raw_text, re.DOTALL)
            if match:
                return json.loads(match.group(1))
            raise ValueError(f"Failed to parse LLM JSON response: {e}\nRaw output: {raw_text[:500]}")
