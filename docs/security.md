# Security

## 1. Threat model (STRIDE, abbreviated)

| Threat | Vector | Control |
|---|---|---|
| Spoofing | stolen JWT | 15-minute access token + rotating httpOnly refresh cookie; `jti` denylist on logout |
| Tampering | direct DB edit of predictions | immutable tables + append-only outcomes; audit log; DB trigger planned Phase 6 |
| Repudiation | "I never rejected that" | `user_decisions` append-only + `system_audit_logs` with actor, ip, before/after |
| Information disclosure | leaked source API keys | Fernet-encrypted at rest; the API returns only a masked hint (`sup...lue`), never the value; audit log records the change, not the secret |
| Denial of service | expensive endpoints, LLM spend | per-IP and per-user rate limits; per-source token buckets; per-run request ceiling; AI daily/monthly hard caps |
| Elevation of privilege | viewer calling admin routes | RBAC dependency on every mutating router; permission tests in CI |
| **Prompt injection** | hostile page instructs the model | sanitiser + nonce envelope + no tools + JSON schema + citation verification |
| **XML denial of service** | feed with a billion-laughs DTD | `defusedxml` when available; otherwise DOCTYPE refused and bodies capped at 8 MB |
| **Data poisoning** | attacker floods a forum to fake a signal | multi-source gate, forum reliability prior 0.40, proxy flags, manipulation probability |
| SSRF | user-supplied source URL or feed | scheme/port allowlist, RFC1918 + link-local + metadata blocked, DNS resolution checked, no redirect following |
| Supply chain | malicious dependency | pinned lockfiles, `pip-audit` + `npm audit` in CI |

## 2. Implemented

* Argon2id password hashing with configurable cost.
* JWT access + rotating refresh, HS256, refusing to start without a secret.
* RBAC: `admin` / `analyst` / `viewer`, enforced by FastAPI dependencies and tested.
* Pydantic validation on every request body.
* Rate limiting middleware (Redis token bucket; in-memory fallback for tests).
* Security headers: HSTS (when `COOKIE_SECURE`), `X-Content-Type-Options`,
  `X-Frame-Options: DENY`, restrictive CSP, `Referrer-Policy`.
* CORS restricted to configured origins; credentials via httpOnly cookies with
  `SameSite=Lax`.
* Audit logging for every mutating request and for every neutralised injection marker.
* Fernet-encrypted `source_credentials` with rotation timestamps and masked hints.
* SSRF guard on every outbound call, applied inside the shared `Fetcher` so no
  adapter can skip it.
* Hardened feed parsing (see XML row above).
* CSV upload: 8 MB cap, UTF-8 validation, header validation before storage,
  50,000-row limit.
* The access token is held in browser memory only — never `localStorage` — and
  re-obtained from the refresh cookie on reload.

## 3. Not implemented yet (honest list)

* MFA / TOTP.
* Database-level immutability triggers (ORM-level only today).
* Automated backup/restore job — documented in `deployment.md`, not automated.
* KMS/Vault-backed key management; secrets come from environment variables.
* `scripts/rotate_credentials.py` for re-encrypting after a key rotation.
* Row-level security for multi-tenant use; the schema carries `user_id` but the
  first deployment is single-user.
* Live-source verification: adapters are proven against recorded fixtures, not
  against the live endpoints (see `data-source-guide.md` §1).

## 4. Reporting

Security issues: do not open a public issue; see `contributing.md`.
