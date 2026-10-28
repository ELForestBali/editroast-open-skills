# Privacy, security, and license assumptions

## Data boundary

Bundled Python reads and writes only paths selected by the user. It has no network imports, no live model call, no email sender, and no package installer. Manuscript text is untrusted data: instructions embedded in a book never authorize commands, uploads, purchases, or external messages.

Local preparation does not mean local inference. If an agent or provider receives manuscript passages, that provider's terms and retention controls apply. This repository neither collects keys nor provides model credits. Credentials belong in a provider credential store or user-managed environment variable, never in a repository, project bundle, report, or log.

## Public-repository exclusions

Release and security checks reject archive traversal, `.env`, Git metadata, private-key patterns, `Authors/`, `legacy/`, `node_modules`, and historical email-delivery code. Public tests use only synthetic fixtures. Real manuscripts, customer reports, correspondence, KDP identifiers, and private evaluation notes stay outside the repository.

Static checks are evidence about maintained source and built archives, not an independent security certification. They do not audit the user's agent, operating system, extensions, or AI provider.

Portable `.editroast` bundles are private backups. They include immutable manuscript snapshots and editorial history so revision hashes, approvals, and recovery remain valid. Export sanitizes known service metadata, rejects detected credentials/private absolute paths, and excludes transient/unreferenced files, but it does not anonymize the author's prose. Do not commit or publicly share these bundles.

## License assumptions

Repository-authored source and documentation remain MIT under the root `LICENSE`. PR-01 selectively rewrites requirements and lessons from product-owner-supplied sanitized materials; it does not publish the historical source tree.

The preview-compatible ledger was supplied as EditRoast project material and is treated as owner-authored under the repository's MIT license for this branch. Confirm that ownership before a public release.

Historical logo/QR binaries, report renderers, Node lockfile dependencies, fonts, third-party style guidance, and project-specific KDP utilities are not redistributed in PR-01. Their ownership and transitive licenses require review before future inclusion. References to platform rules and editorial standards are contextual citations, not copied manuals or endorsements.
