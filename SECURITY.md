# Security

## Scope

KACE-studio is a prerelease project. A version label or passing source test does not
establish a stable security-supported channel. Consult the exact release notes
and artifact identity; no blanket support promise is made for every older prerelease
or development build. Fix status must be tied to the affected commit/artifact.

## 🔒 Reporting a vulnerability

Do not put exploit details, credentials, private keys or sensitive logs in a
public issue. Use GitHub private vulnerability reporting from the repository's
Security tab **if enabled**. Otherwise, use a maintainer's published private
contact; if none is available, request a private reporting channel without
publishing vulnerability details. This document does not invent an email address
or promise a response deadline.

Include the repository, exact commit/version and artifact identity, affected
platform, minimal reproduction, impact and sanitized evidence. Do not reproduce
against real disks or printers when mocks or temporary files can demonstrate it.

A fix still requires the normal validation and release identity gates. Never
bypass checksums, host-key validation, target identity or rollback to work around
an incident. Retain evidence and follow the documented recovery procedure.
