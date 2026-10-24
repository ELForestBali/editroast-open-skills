# Install in Codex

Codex discovers repository skills in `.agents/skills` and user-level skills in `~/.agents/skills`. A skill is a folder containing `SKILL.md`; supporting scripts and templates may live beside it.

## Easiest project installation

1. Download and unzip the EditRoast release.
2. Open PowerShell, Terminal, or your IDE terminal in the extracted folder.
3. Run the installer, replacing the example project path:

```bash
python install.py --target codex --scope project --skill all --project-dir /path/to/your-book-project
```

Windows example:

```powershell
python install.py --target codex --scope project --skill all --project-dir "C:\Users\You\Documents\My Novel"
```

This creates:

```text
My Novel/
  .agents/skills/
    manuscript-roast/
    manuscript-proofreading/
```

Open `My Novel` in Codex. If Codex was already open and the skills do not appear, restart it. Type `$` or use `/skills` to confirm that `manuscript-roast` and `manuscript-proofreading` are available.

## Run a skill

Place your TXT, Markdown, or DOCX manuscript in the project, then ask:

```text
Use $manuscript-roast to critique the complete manuscript at manuscript.docx.
```

or:

```text
Use $manuscript-proofreading to proofread the complete manuscript at manuscript.docx.
```

Codex will ask for command approval if your current permission profile requires it. The included script reads the manuscript and writes only to the local workspace you select.

## Install for every project

Use user scope:

```bash
python install.py --target codex --scope user --skill all
```

This copies the skills to `~/.agents/skills`. Project installation is easier to audit and is recommended for confidential manuscripts.

## Install only one skill

Use `--skill roast` or `--skill proofreading` instead of `--skill all`.

## Cost and privacy

The download and local workflow are free. Codex access, model usage, and plan limits are controlled by OpenAI and may require a paid plan or API billing. The workflow does not upload data to EditRoast, but manuscript text processed by Codex is handled under the terms of the OpenAI account and environment you use.

Official reference: [OpenAI — Build skills](https://learn.chatgpt.com/docs/build-skills).

