# Install in Claude Code

Claude Code discovers project skills in `.claude/skills/<skill-name>/SKILL.md` and personal skills in `~/.claude/skills`. Skills can be invoked with `/skill-name` or selected automatically when their description matches the task.

## Easiest project installation

1. Install Claude Code using Anthropic's current instructions and verify it with `claude --version`.
2. Download and unzip the EditRoast release.
3. Open a terminal in the extracted folder.
4. Run, replacing the example project path:

```bash
python install.py --target claude --scope project --skill all --project-dir /path/to/your-book-project
```

Windows example:

```powershell
python install.py --target claude --scope project --skill all --project-dir "C:\Users\You\Documents\My Novel"
```

This creates:

```text
My Novel/
  .claude/skills/
    manuscript-roast/
    manuscript-proofreading/
    literary-translation/
    book-production/
```

Start Claude Code from the manuscript project:

```bash
cd /path/to/your-book-project
claude
```

## Run a skill

```text
/manuscript-roast Critique my complete manuscript at manuscript.docx.
```

or:

```text
/manuscript-proofreading Proofread my complete manuscript at manuscript.docx.
```

Claude Code will request command permission according to your configuration. Review the command before approving it.

## Install for every project

```bash
python install.py --target claude --scope user --skill all
```

Use `--skill roast`, `--skill proofreading`, `--skill translation`, or `--skill production` to install only one skill. Translation and production are BETA foundations, not finished end-to-end engines.

## Cost and privacy

The skills are free. Claude Code requires an eligible Claude plan, Anthropic Console billing, or a supported third-party provider. Provider limits and charges are separate from EditRoast. The workflow makes no EditRoast network request, but text processed by Claude is handled under the terms of your configured Claude provider.

Official references: [Anthropic — Skills](https://code.claude.com/docs/en/skills) and [Anthropic — Claude Code setup](https://code.claude.com/docs/en/getting-started).

