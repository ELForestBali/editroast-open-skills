# Editorial cast and restrained report artwork

Status: **approved art direction; asset-import pending** (28 October 2026). Functional editorial PRs must not depend on portraits. Tracking: [ER-068](https://github.com/ELForestBali/editroast-open-skills/issues/10), [StoryForge recovery ER-065](https://github.com/ELForestBali/editroast-open-skills/issues/6).

## Product identity

The Editorial Board is a **set of virtual editorial perspectives**, not employed human editors. Existing stable persona IDs and historical names remain unchanged:

| Stable ID | RU label | EN label | Art / editorial role | Lifecycle |
|---|---|---|---|---|
| `reader_market` | Лёня Тираж | Leo Shelf | Curious, optimistic reader/market lens | Current |
| `voice_emotion` | Вера Зоркая | Vera Sharp | Insightful literary voice and emotional truth | Current |
| `structure_logic` | Борис Точка | Boris Marker | Methodical structure, causality and continuity | Current |
| `editorial_board` | Редсовет; СанСаныч as chair | Editorial Board; Chief Sterling as chair | Experienced chief-editor visual; synthesis and arbitration | Current |
| `proofreader` | Корректор | Proofreader | Meticulous spelling, punctuation and consistency | Next reports |

**Important:** СанСаныч / Chief Sterling is the localized *chair* of `editorial_board`, not an undocumented fourth independent reviewer. Proofreader is a different task from developmental analysis and should be shown only where proofreading is actually performed.

## Future expansion (characters only — not capabilities)

`translator`, `book_designer`, `illustrator`, `literary_agent`, `marketing`, `submissions`, `outreach_agent`.

The visual team can depict a larger hypothetical AI publishing studio, but site/report texts must not imply that these seven workflows ship already. Track future functional permissions separately from illustrations in [ER-066](https://github.com/ELForestBali/editroast-open-skills/issues/7). External outreach, forms and publishing submissions require explicit user approval, truthful representation and compliance with third-party platform rules.

## Art direction approved

The owner approved a colorful, mildly comic caricature style with relatable facial expressions, coherent warm indoor-studio backgrounds, distinct silhouettes and expressions; no generic robots, glowing gradients, anime clichés or human-photo implications. Use a modern sans-serif document style and a clean off-white report background. Portable assets should be PNG and/or WebP with labels generated as accessible localizable text. Do not bake translated names into portrait images.

The **original historical StoryForge images were not located**. A new ChatGPT-generated group poster from 2026-10-28 was approved as visual direction; a derived preview asset package `EditRoast-Character-Pack-v0.1.zip` with source, provenance manifest, five small circular editorial/proofreading portraits, seven concept-only future portraits and a demo report layout has been prepared in the product-owner conversation for local handoff. It must be **downloaded and explicitly imported** into the actual repository through a separate tested PR; this document is not a substitute for committed assets and cannot be used as a download URL.

## Layout rules

- **Cover:** one restrained editorial-team illustration **only if** clear of title, book metadata, privacy note and report version.
- **Individual editor sections:** a small portrait, approximately 15–22 mm diameter, beside a true text heading/name and specialty. Editorial citations, strengths, problems, conflicting assessments and revision priorities occupy the main page.
- **Proofreading report:** the proofreader's portrait may introduce its independent section. Corrections themselves remain text/table-first.
- **Corrections table:** **no large portraits**. If source role needs marking, use plain, accessible labels; never rely on color alone.
- **Revision comparison:** same modest identity cues across rounds without images inside text diffs.
- **PDF brand:** edits and evidence are professionally typeset; page headers, running folios, accessible contrast and printable grayscale readability take priority over visual decoration.
- **Web:** responsive icons, short alt text, no captions embedded in artwork, optimized image size, lazy load outside hero, link to the relevant live skill.
- **Graceful fallback:** If an asset is missing or the user requests no art, render a complete, legible text-only output rather than failing.
- **Rights:** record asset provenance and ensure safe public redistribution before shipping. Do not represent virtual avatars as credentialed human editors.

## Implementation gates

1. Import only approved/current assets, inspect image resolution and crop quality, and ship a machine-readable asset manifest with stable persona IDs and status.
2. Check image dimensions, WebP/PNG decode, role/locale coverage, duplicate IDs and referenced assets in automated tests.
3. Use dedicated report templates/components, not per-LLM ad hoc inline image URLs. Never include binary base64 in every prompt.
4. Run visual QA on English/Russian full-book sample reports, including very long correction tables; test print rendering and contrast.
5. Test output using text-only mode and unavailable assets, ensuring results remain complete.
6. Keep report art independent of the ER-012 source-diff and ER-013 author-approval logic. **Do not delay functional development**.
7. Do not auto-deploy new character artwork or advertise concept-only roles on EditRoast.com / Nadi's site.

### Suggested use in reports

```text
┌──────────────────────────────────────────────────────────────────┐
│ EditRoast  ·  Editorial Board Review            Rev 02 / date    │
│ Book title  /  Author                                            │
│ [small ensemble illustration, optional]                         │
├──────────────────────────────────────────────────────────────────┤
│ [Leo portrait]  LEO SHELF — Reader & market                       │
│ Evidence-led observations, positives, issues, and action plan    │
├──────────────────────────────────────────────────────────────────┤
│ [Vera portrait]  VERA SHARP — Voice & emotion                     │
│ Specific citations and protected author choices                 │
├──────────────────────────────────────────────────────────────────┤
│ [Boris portrait] BORIS MARKER — Structure & logic                │
│ Independent editorial findings                                   │
├──────────────────────────────────────────────────────────────────┤
│ [SanSanych portrait] EDITORIAL BOARD — Synthesis                │
│ Disagreements, root causes, severity and revision priorities    │
├──────────────────────────────────────────────────────────────────┤
│ Correction list: plain rows, anchors, author decisions           │
│ [No portraits in rows; no invented professional certifications] │
└──────────────────────────────────────────────────────────────────┘
```

The visual family is for recognition and storytelling, **not a substitute for exact quotes, critical reasoning or verified revision history**.
