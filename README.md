# App Bottler 🍾

> Automated Migration Studio from GitHub Open-Source Projects to **Cloud in a Bottle (OpenHost)** Applications.

App Bottler analyzes any open-source GitHub repository and uses an LLM to generate turnkey Cloud in a Bottle application configurations (`cloudinabottle.toml`, `Dockerfile`, rootless Podman entrypoints, and documentation). With integrated GitHub API authentication, it pushes the bottled project directly to your GitHub account, monitors upstream releases, and lets you submit apps to the official app catalog in 1 click.

---

## ✨ Features

- 🔍 **Automated Upstream Analysis**: Inspects repository metadata, stars, latest release tags, Dockerfile, and docker-compose configurations.
- 🤖 **LLM-Powered Architecture Generator**: Generates custom `cloudinabottle.toml`, Podman container wrappers, data volume bindings (`$BOTTLE_APP_DATA_DIR`), and health checks compliant with Cloud in a Bottle standards.
- ✏️ **Interactive In-Browser Studio**: Review and tweak every generated file before publishing.
- 🚀 **1-Click GitHub Repository Creation**: Automatically creates a `bottled-<name>` repository under your GitHub account and commits all assets.
- 📥 **One-Command Cloud in a Bottle Install**: Generates copy-paste `openhost app install` commands for instant deployment to your personal cloud.
- 🏪 **Official Catalog Submission**: 1-click submission to `cloud-in-a-bottle/app-manifest` (`apps/<name>/app.toml`) via GitHub Pull Request.
- 🔄 **Upstream Version Tracking**: Monitors upstream releases and alerts you when new versions are available to package.

---

## 🛠️ Deploy on Cloud in a Bottle

Run on your Cloud in a Bottle host:

```bash
openhost app install https://github.com/accelizero/app-bottler
```

---

## ⚙️ Configuration

In the App Bottler UI (gear icon in the top right):
1. **GitHub Personal Access Token**: Token with `repo` scope to create repositories and commit files.
2. **LLM Base URL & API Key**: Compatible with OpenAI (`https://api.openai.com/v1`), DeepSeek (`https://api.deepseek.com/v1`), OpenRouter, or local LLM gateways.
3. **LLM Model**: e.g., `gpt-4o`, `deepseek-chat`, etc.

---

## 📄 License

MIT License.
