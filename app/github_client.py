import base64
import httpx


async def fetch_repo_summary(repo: str, token: str = "") -> dict:
    """Fetch repo info, latest release, manifests, and classify repo archetype."""
    repo = repo.strip().rstrip("/")
    if "github.com/" in repo:
        repo = repo.split("github.com/")[-1]

    headers = {"Accept": "application/vnd.github.v3+json", "User-Agent": "AppBottler/1.0"}
    if token:
        headers["Authorization"] = f"token {token}"

    async with httpx.AsyncClient(timeout=12) as client:
        # 1. Base Repo Info
        res = await client.get(f"https://api.github.com/repos/{repo}", headers=headers)
        if res.status_code == 403 and "rate limit" in res.text.lower():
            raise ValueError(
                "GitHub API rate limit exceeded. Please configure your GitHub Personal Access Token in Settings to increase your quota to 5,000 req/hour."
            )
        if res.status_code != 200:
            raise ValueError(f"GitHub repo not found or inaccessible ({res.status_code})")
        info = res.json()

        # 2. Latest Release
        rel_res = await client.get(f"https://api.github.com/repos/{repo}/releases/latest", headers=headers)
        latest_tag = ""
        if rel_res.status_code == 200:
            latest_tag = rel_res.json().get("tag_name", "")

        # 3. Read key manifests and files
        candidate_files = [
            "Dockerfile",
            "docker-compose.yml",
            "docker-compose.yaml",
            "README.md",
            "pyproject.toml",
            "setup.py",
            "requirements.txt",
            "package.json",
            "go.mod",
            "Cargo.toml",
        ]
        files = {}
        for fname in candidate_files:
            f_res = await client.get(f"https://api.github.com/repos/{repo}/contents/{fname}", headers=headers)
            if f_res.status_code == 200:
                raw_content = f_res.json().get("content", "")
                try:
                    max_len = 3500 if fname.endswith(".md") else 1500
                    files[fname] = base64.b64decode(raw_content).decode("utf-8", errors="ignore")[:max_len]
                except Exception:
                    pass

        # 4. Check for examples / tutorials / notebooks directories
        example_dirs = []
        for dname in ["examples", "tutorials", "notebooks", "demo"]:
            d_res = await client.get(f"https://api.github.com/repos/{repo}/contents/{dname}", headers=headers)
            if d_res.status_code == 200 and isinstance(d_res.json(), list):
                example_dirs.append(dname)

        has_examples = bool(example_dirs)

        # 5. Classify Archetype & Security Recommendation
        has_docker = "Dockerfile" in files or "docker-compose.yml" in files or "docker-compose.yaml" in files
        is_python_lib = ("setup.py" in files or "pyproject.toml" in files or "requirements.txt" in files) and not has_docker
        
        repo_lower = (repo + " " + (info.get("description") or "")).lower()
        is_dev_or_admin = any(k in repo_lower for k in [
            "jupyter", "notebook", "admin", "dashboard", "manager", "database",
            "terminal", "shell", "editor", "ide", "pgadmin", "redis", "mysql", "workbench"
        ])

        if is_python_lib:
            detected_archetype = "library_framework"
            archetype_label = "Python Library / Framework (Packaged with JupyterLab Workbench)"
            recommended_access = "private"
        elif is_dev_or_admin:
            detected_archetype = "developer_admin_tool"
            archetype_label = "Developer / Admin Tool (Requires Owner Authentication)"
            recommended_access = "private"
        else:
            detected_archetype = "web_app"
            archetype_label = "Web Application"
            recommended_access = "public"

        return {
            "owner": info.get("owner", {}).get("login", ""),
            "name": info.get("name", ""),
            "full_name": info.get("full_name", ""),
            "description": info.get("description", "") or "",
            "stars": info.get("stargazers_count", 0),
            "latest_tag": latest_tag,
            "default_branch": info.get("default_branch", "main"),
            "files": files,
            "has_examples": has_examples,
            "example_dirs": example_dirs,
            "detected_archetype": detected_archetype,
            "archetype_label": archetype_label,
            "recommended_access": recommended_access,
        }


async def get_authenticated_user(token: str) -> str:
    headers = {"Accept": "application/vnd.github.v3+json", "Authorization": f"Bearer {token}", "User-Agent": "AppBottler/1.0"}
    async with httpx.AsyncClient(timeout=10) as client:
        res = await client.get("https://api.github.com/user", headers=headers)
        if res.status_code != 200:
            raise ValueError("Invalid GitHub Token or expired")
        return res.json().get("login", "")


async def create_or_update_repo(token: str, repo_name: str, description: str, files: dict[str, str]) -> str:
    """Create a repo under the authenticated user (if not exists) and commit all files."""
    if token.startswith("github_pat_"):
        raise ValueError(
            "GitHub Fine-grained tokens (github_pat_...) cannot create personal repositories via API due to a GitHub platform limitation. "
            "Please use a Classic Token (starts with 'ghp_') with 'repo' scope (generate at: https://github.com/settings/tokens/new?scopes=repo&description=AppBottler)."
        )

    headers = {"Accept": "application/vnd.github.v3+json", "Authorization": f"Bearer {token}", "User-Agent": "AppBottler/1.0"}
    async with httpx.AsyncClient(timeout=15) as client:
        # Check user
        user = await get_authenticated_user(token)

        # Check if repo exists
        check = await client.get(f"https://api.github.com/repos/{user}/{repo_name}", headers=headers)
        if check.status_code == 404:
            create_payload = {
                "name": repo_name,
                "description": description,
                "private": False,
                "auto_init": True,
            }
            create_res = await client.post("https://api.github.com/user/repos", headers=headers, json=create_payload)
            if create_res.status_code not in (200, 201):
                err_text = create_res.text
                if "resource not accessible by personal access token" in err_text.lower():
                    raise ValueError(
                        "GitHub Token permission error: Your token lacks repository creation permission. "
                        "Please use a Classic Token (starts with 'ghp_') with 'repo' scope: "
                        "https://github.com/settings/tokens/new?scopes=repo&description=AppBottler"
                    )
                raise ValueError(f"Failed to create repo: {err_text}")

        # Commit files one by one (or update if exists)
        for filepath, content in files.items():
            f_check = await client.get(f"https://api.github.com/repos/{user}/{repo_name}/contents/{filepath}", headers=headers)
            sha = None
            if f_check.status_code == 200:
                sha = f_check.json().get("sha")

            payload = {
                "message": f"feat: bottle application ({filepath})",
                "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            }
            if sha:
                payload["sha"] = sha

            put_res = await client.put(f"https://api.github.com/repos/{user}/{repo_name}/contents/{filepath}", headers=headers, json=payload)
            if put_res.status_code not in (200, 201):
                raise ValueError(f"Failed to write {filepath}: {put_res.text}")

        return f"https://github.com/{user}/{repo_name}"
