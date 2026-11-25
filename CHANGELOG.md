# Current candidate notes

Only notes for the declared candidate remain here for release preparation. Older release notes and audit narratives are archived locally;
Git history retains previously committed releases. These notes do not attest
uncommitted changes or current CI, packaging or physical qualification.

[README](README.md) covers current usage; [ROADMAP](ROADMAP.md) covers pending work.

## [0.6.0-rc.1] - 2026-11-16 (preparation; unpublished)

- The November 25 bench candidate pins the Core runtime that generates plain
  ASCII comments and starter macro descriptions in English, Spanish and
  Portuguese. The release contract records the exact runtime and bootstrap.
- Add explicit configuration-only launch and bounded discovery/recovery of
  interrupted operations. Reconnection alone never resumes or completes them.
- Acquire the runtime update lock before official Core launches, identity reads
  and result verification; reject a pending update journal.
- Publish SFTP downloads only for the originating session, preserve destination
  files on failure, and isolate malformed discovery responses.
- Bound Moonraker requests and response bodies; reject redirects and never retry
  a power POST automatically. Normal OFF requires fresh device protection,
  permitted printer state, finite temperatures, zero targets and a one-use
  confirmation. Emergency stop remains a logical Klipper request, not proof of
  electrical disconnection. Explain persistent IP authorization in EN/ES/PT.
- Select setuptools 84.1.0 in the development requirement and hashed lock.
  Existing environments are not upgraded; clean installation and rebuild remain
  pending. Keep storage refresh and network probes isolated in unit tests.
- Pin Core 0.10.0-rc.1 runtime and bootstrap to published immutable commits.
  Remote runtime files, installer and bootstrap have been verified by SHA-256;
  runtime_status=pinned records input identity, not CI, package or hardware
  qualification.
- Bundle the corrected Core preflight engine: readable container input copies
  remain under a private host-side parent without adding Docker capabilities.
  Require the real loader and cross-project contract gate before packaging.
- Bench executables share this prerelease version. Match the Studio commit
  and executable SHA-256 to the build manifest and qualification report.
  Manual hardware E2E remains pending for the November 25 candidate.

- Stage new SSH host keys in memory and publish trust through the locked atomic
  writer, preserving previous keys on temporary-write or replacement failure.
- Persist original pre-baked account identity before rename and repair its legacy
  home alias on retry; preserve conflicts for explicit manual recovery.
- Show operation-bound image readback as verified or skipped after successful
  provisioning; reject missing/stale evidence and keep SSH errors independent.
- Validate durable checkpoint structure against producer-generated fixtures and
  require saved MCU verification evidence before displaying completion.
- Localize critical reconnect, readback, eject and accessible help in EN/ES/PT;
  preserve live state on language changes and discard obsolete eject replies.
- Move keyboard focus into Credentials after Imager Next while preserving
  sidebar, validation and terminal focus behavior.

## [0.5.0-rc.3] - 2026-10-03

- Detect silent SSH transport loss with acknowledged, bounded idle channel probes; retain installation progress and disconnect controls after bootstrap as well as during it.
- Reject SSH login success if the configuration probe closed its transport, and preserve the final connection error after retries for diagnosis.
- Mark recoverable SSH losses disconnected, invalidate stale file/power requests and preserve visible installation progress until a verified reconnect. Bundle bounded persistent Linux diagnostics for new bootstrap runs.
- Pin the KACE runtime that offers automatic/minimum/maximum homing direction
  per axis and leaves `homing_positive_dir` commented in automatic mode.
- Deliver custom-probe selection based on the effective board sockets and
  selected TMC drivers, with probe/Z input priority, advanced declared pins,
  explicit signal options and preserved collision/firmware reservation checks.
- Bundle a bootstrap that preserves verified relay settings on later launches
  after first-boot options were removed, rejecting inconsistent saved state.
- Align Imager fields and spacing in paired columns, adapting to narrow windows;
  expand GPIO relay configuration immediately when enabled.
- Pause first-boot discovery when new devices respond. Offer explicit Connect,
  Keep searching and Stop actions; continuing past reviewed addresses does not
  pause again for the same device. Discard cancelled scan replies and retain
  serialization and the ten-minute search bound.
- Localize discovery actions, candidate and pending states in EN/ES/PT.
- Distinguish an empty/offline disk's partition list from provider errors during
  Windows eject verification and preserve localized error text.
- Pin KACE 0.9.4-rc.3 runtime, installer and bootstrap bytes, including reviewed
  TMC socket mapping and monotonic first-boot readiness checks.
- Continue unsigned candidate distribution with independent Windows rebuild
  evidence. Physical qualification and signed stable distribution remain pending.

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
