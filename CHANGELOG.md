# Current candidate notes

Only notes for the declared candidate remain here for release preparation. Older release notes and audit narratives are archived locally;
Git history retains previously committed releases. These notes do not attest
uncommitted changes or current CI, packaging or physical qualification.

[README](README.md) covers current usage; [ROADMAP](ROADMAP.md) covers pending work.

## [0.5.0-rc.2] - 2026-10-01

### Distribution and documentation
- Make the Windows x64 ZIP the primary installation path, with executable,
  checksums, exact-source manifest and independent Windows rebuild evidence.
- Refresh English, Spanish and Portuguese project pages with verified badges,
  native application screenshots and a user-oriented setup flow.
- Continue the documented unsigned prerelease policy; physical qualification
  and signed stable distribution remain pending.
- Preserve the existing KACE runtime, installer, bootstrap and image pins.

## [0.5.0-rc.1] — 2026-09-17

Candidate for controlled hardware qualification with KACE 0.9.4-rc.2.
Physical qualification and signed stable distribution remain separate gates.

### Fixed
- Bind image approval, elevated writing, volume protection, readback, injection and
  eject to the selected physical device and the same operation identity.
- Preserve approved image bytes through the writer; validate pinned images and
  pre-baked capabilities before destructive operations.
- Bound ZIP/XZ extraction, decoder memory and cancellation latency; reject
  incomplete archives and extra streams before publishing raw images.
- Authenticate local SFTP listings; bind listing/download and firmware recovery to
  the originating SSH generation; complete partial terminal sends.
- Keep relay authority tied to the current printer and require explicit Moonraker
  client enrollment instead of trusting entire networks.
- Keep terminal failures, manual action and reconnect recovery distinct from
  successful KACE completion in the UI.
- Bind distribution to committed KACE runtime files, installer and bootstrap bytes;
  retain clean-tree, exact-toolchain, PE metadata and bundled-resource checks.
- Retry transient release-contract download failures at most three times, always
  checking fresh bytes; checksum/size mismatches and permanent HTTP errors remain
  terminal. Make simulated Windows tests portable to standalone Linux checkouts.
