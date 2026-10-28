# Production safety and historical KDP notes

Historical EditRoast work found useful implementation warnings, but those notes are experiential evidence rather than universal platform rules.

- Bleed is conditional on artwork and the selected platform profile. The old blanket rule to avoid bleed and mirrored margins is not a current product rule.
- If any interior page requires bleed, verify the complete interior geometry against the current platform specification. Do not infer platform acceptance from local dimensions alone.
- Inside/outside margins and image parity depend on physical page order. Check recto/verso after rendering.
- A page containing only a footer number can appear nonempty to naive text extraction; visual proofing remains necessary.
- Prefer a chapter heading's `page_break_before` over an empty break-only paragraph when the renderer otherwise creates blank pages.
- Never use the historical `SWAP_TAIL_BLOCKS` workaround to move prose around illustrations. It changes narrative order even if no words are deleted.
- A native image's PPI and licensing matter; software upscaling does not create new source quality.
- Keep report branding separate from publishable interiors and ebooks.

Platform rules can change. Verify current official specifications and run the provider's own preview or validation before claiming acceptance.
