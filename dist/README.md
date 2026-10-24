# Release archives

Versioned ZIP archives are attached to GitHub Releases and mirrored on
editroast.com. Build and verify them locally with:

```bash
python scripts/build_release.py
```

The generated archives are intentionally ignored by Git so releases stay the
single canonical binary distribution channel.
