# Changelog

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

### Documentation
- Add platform/firmware badges, a current capability table, immutable release
  instructions and the controlled hardware qualification path.
- Reorganize the README around the guided desktop journey, supported Pi/image
  matrices, concise safety guidance and direct KACE ecosystem navigation.
- Separate unsigned local test-candidate evidence from signed, independently
  reproduced release evidence. Each executable carries its own external manifest.

## Earlier development

Earlier 0.5.0-dev work is recorded in Git history. Existing binaries and manifests
identify their original commits and do not attest this release candidate.
