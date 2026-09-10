import asyncio
import base64
import time
import urllib.parse
import httpx


def generate_catalog_toml(
    name: str,
    title: str,
    description: str,
    repo_url: str,
    website_url: str = "",
    docs_url: str = "",
    tags: list[str] | None = None,
    categories: list[str] | None = None,
) -> str:
    tags_str = ", ".join([f'"{t}"' for t in (tags or ["utility"])])
    cats_str = ", ".join([f'"{c}"' for c in (categories or ["utility"])])
    website_url = website_url or repo_url
    docs_url = docs_url or f"{repo_url}#readme"

    toml_content = f"""[app]
name = "{name}"
title = "{title}"
description = "{description}"
repo_url = "{repo_url}"
website_url = "{website_url}"
docs_url = "{docs_url}"
tags = [{tags_str}]
categories = [{cats_str}]
"""
    return toml_content


async def submit_to_catalog(
    token: str,
    name: str,
    catalog_toml: str,
) -> dict:
    """Fork cloud-in-a-bottle/app-manifest, commit apps/<name>/app.toml, and create a Pull Request."""
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "Authorization": f"token {token}",
        "User-Agent": "AppBottler/1.0",
    }
    upstream_owner = "cloud-in-a-bottle"
    upstream_repo = "app-manifest"
    file_path = f"apps/{name}/app.toml"

    # Pre-calculated fallback web URL
    encoded_toml = urllib.parse.quote(catalog_toml)
    web_fallback_url = f"https://github.com/{upstream_owner}/{upstream_repo}/new/main?filename={file_path}&value={encoded_toml}"

    async with httpx.AsyncClient(timeout=20) as client:
        # 1. Get authenticated user
        user_res = await client.get("https://api.github.com/user", headers=headers)
        if user_res.status_code != 200:
            return {"success": False, "error": "Invalid GitHub token", "web_url": web_fallback_url}
        username = user_res.json()["login"]

        # 2. Fork repo
        fork_res = await client.post(f"https://api.github.com/repos/{upstream_owner}/{upstream_repo}/forks", headers=headers)
        if fork_res.status_code not in (200, 202):
            return {
                "success": False,
                "error": f"Failed to fork repository ({fork_res.status_code}): {fork_res.text}",
                "web_url": web_fallback_url,
            }

        # Wait briefly for fork to initialize if newly created
        branch_name = f"add-app-{name}"
        await asyncio.sleep(2)

        # 3. Get main branch SHA of the fork
        ref_res = await client.get(f"https://api.github.com/repos/{username}/{upstream_repo}/git/ref/heads/main", headers=headers)
        if ref_res.status_code != 200:
            # Fork might still be cloning
            await asyncio.sleep(3)
            ref_res = await client.get(f"https://api.github.com/repos/{username}/{upstream_repo}/git/ref/heads/main", headers=headers)
            if ref_res.status_code != 200:
                return {
                    "success": False,
                    "error": "Fork created, but git ref not immediately ready. Please try again in a moment.",
                    "web_url": web_fallback_url,
                }
        base_sha = ref_res.json()["object"]["sha"]

        # 4. Create new branch in user's fork
        create_ref = await client.post(
            f"https://api.github.com/repos/{username}/{upstream_repo}/git/refs",
            headers=headers,
            json={"ref": f"refs/heads/{branch_name}", "sha": base_sha},
        )
        if create_ref.status_code == 422:
            # Branch already exists, append timestamp suffix
            branch_name = f"add-app-{name}-{int(time.time()) % 10000}"
            create_ref = await client.post(
                f"https://api.github.com/repos/{username}/{upstream_repo}/git/refs",
                headers=headers,
                json={"ref": f"refs/heads/{branch_name}", "sha": base_sha},
            )

        if create_ref.status_code not in (200, 201):
            return {
                "success": False,
                "error": f"Failed to create branch in fork: {create_ref.text}",
                "web_url": web_fallback_url,
            }

        # 5. Commit/Update app.toml
        # Check existing file on branch
        check_file = await client.get(
            f"https://api.github.com/repos/{username}/{upstream_repo}/contents/{file_path}?ref={branch_name}",
            headers=headers,
        )
        sha = check_file.json().get("sha") if check_file.status_code == 200 else None

        put_payload = {
            "message": f"feat(apps): add {name} catalog manifest",
            "content": base64.b64encode(catalog_toml.encode("utf-8")).decode("ascii"),
            "branch": branch_name,
        }
        if sha:
            put_payload["sha"] = sha

        put_res = await client.put(
            f"https://api.github.com/repos/{username}/{upstream_repo}/contents/{file_path}",
            headers=headers,
            json=put_payload,
        )
        if put_res.status_code not in (200, 201):
            return {
                "success": False,
                "error": f"Failed to commit file to branch: {put_res.text}",
                "web_url": web_fallback_url,
            }

        # 6. Create Pull Request against cloud-in-a-bottle/app-manifest:main
        pr_payload = {
            "title": f"Add {name} app manifest",
            "head": f"{username}:{branch_name}",
            "base": "main",
            "body": f"Automated submission of {name} app manifest generated by App Bottler.\n\nTarget App: `{name}`\nConfiguration:\n```toml\n{catalog_toml}\n```",
        }
        pr_res = await client.post(
            f"https://api.github.com/repos/{upstream_owner}/{upstream_repo}/pulls",
            headers=headers,
            json=pr_payload,
        )

        if pr_res.status_code in (200, 201):
            pr_data = pr_res.json()
            return {
                "success": True,
                "pr_url": pr_data.get("html_url"),
                "pr_number": pr_data.get("number"),
                "web_url": web_fallback_url,
            }
        else:
            # Check if PR already exists
            err_text = pr_res.text
            return {
                "success": False,
                "error": f"PR creation response: {err_text}",
                "web_url": web_fallback_url,
            }
