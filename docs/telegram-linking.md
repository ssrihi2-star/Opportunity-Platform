# Telegram channel linking

How a Telegram chat is bound to an account, how to configure it, and what an
operator has to do when upgrading an existing deployment.

Audience: whoever deploys and runs the backend. Everything here is setup
instruction — following it does not require sending a live Telegram message
until the final smoke test, which is optional and clearly marked.

---

## 1. What the link has to prove

A notification channel link says "this Telegram chat belongs to this account".
Everything else depends on that claim being true: `services/alerts.py` only
delivers to links where `verified` is true, so a false claim is a direct route
to another person's opportunity alerts.

Proving it means establishing the chat id from something the account holder
cannot simply assert.

**The old flow could not do this.** `POST /me/channels` issued a `link_code` to
the caller, and `POST /me/channels/verify` accepted that same code back
alongside an `external_id` — a chat id — that the caller typed in themselves.
Both halves of the exchange originated from the same authenticated request, so
redeeming a code demonstrated only that the user could copy and paste. Anyone
could bind any chat id, including a chat belonging to somebody else, and start
receiving that chat's alerts.

**What replaces it is a direction of travel.** The code is issued to the user,
the user sends it *into the chat*, and Telegram delivers an update to our
webhook carrying the chat id **it** observed. The chat id is established by
Telegram and never by the caller. Someone who knows a victim's chat id still
cannot bind it, because they cannot make Telegram send us an update from a chat
they do not control.

```
  user                     API                      Telegram            webhook
   │  POST /me/channels     │                          │                   │
   ├───────────────────────►│  issue code, TTL 15m     │                   │
   │◄───────────────────────┤  (pending, unverified)   │                   │
   │                        │                          │                   │
   │  /link <code>  ── sent inside the chat ──────────►│                   │
   │                        │                          │  update + secret  │
   │                        │                          ├──────────────────►│
   │                        │       chat id comes from HERE, not the user  │
   │                        │◄─── atomic claim: code → verified binding ───┤
   │  GET /me/channels  ────►  verified: true                              │
```

---

## 2. Configuration

Two new settings, both in `backend/environment.example`:

| Variable | Default | Meaning |
| --- | --- | --- |
| `TELEGRAM_WEBHOOK_SECRET` | *(blank)* | Shared secret Telegram echoes back in the `X-Telegram-Bot-Api-Secret-Token` header on every update. **The webhook refuses to process anything while this is blank.** |
| `TELEGRAM_LINK_CODE_TTL_MINUTES` | `15` | How long a `/link` code stays usable. Codes are also single-use. |

Alongside the existing `TELEGRAM_BOT_TOKEN` (the bot credential) and
`TELEGRAM_API_BASE`.

Generate a secret with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Telegram permits 1–256 characters from `A-Z a-z 0-9 _ -` for this value.

### Fail-closed behaviour

With no secret configured, every caller who can reach the URL is
indistinguishable from Telegram. There is nothing the endpoint could safely do,
so it does nothing:

| Condition | Response |
| --- | --- |
| `TELEGRAM_WEBHOOK_SECRET` unset, empty, or whitespace | `503 Telegram webhook is not configured.` |
| Header missing, wrong, or a prefix of the real secret | `401 Unauthorized.` |
| Authenticated | `200` — see below |

The secret is compared with `hmac.compare_digest`, so the comparison cannot be
timed character by character.

> This is **shared-secret header authentication, not a digital signature.** It
> proves the caller knows the secret; it says nothing about the body. It is what
> Telegram offers for webhooks, and it is only as good as the transport, so the
> webhook URL must be HTTPS.

---

## 3. Registering the webhook

The endpoint is:

```
POST /api/v1/integrations/telegram/webhook
```

It is deliberately excluded from the OpenAPI schema — it is machine-to-machine
and not part of the public API surface.

Register it with Telegram **once per deployment**, passing the same secret you
configured:

```bash
curl -sS -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
  -H 'Content-Type: application/json' \
  -d '{
        "url": "https://YOUR-HOST/api/v1/integrations/telegram/webhook",
        "secret_token": "YOUR-TELEGRAM_WEBHOOK_SECRET",
        "allowed_updates": ["message"]
      }'
```

Notes:

- The URL must be **HTTPS** and publicly reachable; Telegram will not deliver to
  plain HTTP or to a private address.
- `secret_token` must be byte-for-byte the value of `TELEGRAM_WEBHOOK_SECRET`.
  If they differ, every update is rejected with `401` and no one can link.
- `allowed_updates: ["message"]` keeps the traffic to what is actually used.
- Set the environment variable and restart the backend **before** calling
  `setWebhook`, so the first update does not arrive at a fail-closed endpoint.

Check the registration (this reveals no secrets):

```bash
curl -sS "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getWebhookInfo"
```

`pending_update_count` climbing along with `last_error_message` is the symptom
of a mismatched secret or an unreachable URL.

To rotate the secret: update `TELEGRAM_WEBHOOK_SECRET`, restart the backend,
then call `setWebhook` again with the new value. Briefly, updates in flight are
rejected; Telegram retries them.

To stop receiving updates entirely: `deleteWebhook`.

---

## 4. Linking, as the user experiences it

1. `POST /api/v1/me/channels` with `{"channel": "telegram"}` → `201` carrying
   `link_code` and human instructions. The link exists but is unverified, and
   nothing is delivered to it.
2. The user opens a **direct message** with the bot and sends `/link <code>`.
   `/link@yourbot <code>` also works, which is how Telegram addresses commands
   in some clients.
3. Telegram calls the webhook. The code is consumed and the chat is bound.
4. `GET /api/v1/me/channels` now shows `verified: true`.

The bot answers with one of four fixed replies:

| Situation | Reply |
| --- | --- |
| Bound successfully | `Linked. Alerts for your account will arrive in this chat.` |
| Unknown, expired, **or already used** code | `That link code is not valid, has expired, or has already been used.` |
| Chat already belongs to an account | `This chat is already linked to an account. One chat, one account.` |
| Sent in a group, supergroup or channel | `Linking only works in a direct message with the bot, not in a group or channel.` |

The first three of those situations share one reply on purpose. Distinguishing
"never existed" from "expired" from "already used" would turn the bot into an
oracle confirming whether a guessed code was ever real.

### Rules enforced

- **Private chats only.** A group binding would deliver one person's private
  opportunity alerts to every member, so `group`, `supergroup` and `channel`
  are refused.
- **Single use, atomically consumed.** The claim is one `UPDATE ... WHERE
  link_code = ? AND verified IS false AND link_code_expires_at > now`. The test
  and the write are the same statement, so two concurrent redemptions of one
  code cannot both succeed — the first clears `link_code`, the second matches
  no rows.
- **One chat, one account.** The `ux_channel_external` unique constraint on
  `(channel, external_id)` is the backstop for two *different* codes racing for
  the same chat. The loser gets an `IntegrityError`, which is rolled back and
  answered with `This chat is already linked` rather than a `500`.
- **Nothing sensitive is logged.** Link attempts log only an outcome label
  (`linked`, `rejected_code`, `chat_already_linked`, `not_private_chat`). Codes,
  chat ids, bot tokens and the webhook secret are never written to logs.
- **Malformed updates are ignored, not crashed on.** Anything that is not a
  readable `/link` command returns `200 {"ok": true, "handled": false}`. A
  non-2xx would make Telegram retry the same broken update indefinitely.

### `POST /me/channels/verify` is now status-only

The route still exists so existing clients get a truthful answer instead of a
`404`, but it **cannot grant verification**, and the `external_id` in the
request body is **ignored**. It reports the current `verified` state and
nothing more. `GET /me/channels` is the better way to poll.

Unlinking is unchanged and remains owner-only:
`DELETE /api/v1/me/channels/{link_id}`, which 404s for anyone else's link. After
unlinking, the chat is free and can be linked again with a fresh code.

---

## 5. Upgrading an existing deployment

### Migration `0006_telegram_secure_binding`

Revision `0006_telegram_secure_binding`, on top of `0005_risk_moderate`.

```bash
cd backend && alembic upgrade head
```

It does two things:

1. Adds `notification_channel_links.link_code_expires_at` (nullable timestamp).
   The add is guarded, so re-running is safe.
2. **Invalidates every existing Telegram link**, in one statement:

   ```sql
   UPDATE notification_channel_links
      SET verified = false,
          verified_at = NULL,
          link_code = NULL,
          link_code_expires_at = NULL,
          external_id = 'revoked:' || CAST(id AS VARCHAR)
    WHERE channel = 'telegram';
   ```

Every Telegram binding in an existing database was created through the insecure
flow, so none of them is evidence of anything. They are all revoked rather than
audited, because there is no stored information that could distinguish an honest
link from a hijacked one. Outstanding unredeemed codes are cleared at the same
time, since they were issued under the old rules.

Rewriting `external_id` to `revoked:<id>` frees the chat slot under
`ux_channel_external`, so the rightful owner can immediately relink that same
chat without colliding with the dead row.

**Other channels are untouched.** The `WHERE channel = 'telegram'` clause means
email and in-app links keep their verification and their history.

The downgrade drops the column only. It deliberately does **not** restore the
revoked bindings — that data was not trustworthy, and recreating it would
reintroduce the vulnerability.

### What operators must tell users

After deploying, **every Telegram user must relink**. Until they do, their
Telegram links show `verified: false` and receive nothing; in-app and email
alerts continue normally.

Relinking:

1. `POST /api/v1/me/channels` with `{"channel": "telegram"}` for a fresh code.
2. Send `/link <code>` to the bot in a direct message, within 15 minutes.
3. Confirm `verified: true` via `GET /api/v1/me/channels`.

The stale `revoked:*` rows can be left in place — they are inert, unverified,
and never delivered to. They can also be deleted with
`DELETE /api/v1/me/channels/{link_id}` by their owner, or cleaned up in bulk:

```sql
DELETE FROM notification_channel_links
 WHERE channel = 'telegram' AND external_id LIKE 'revoked:%' AND verified = false;
```

### Deployment checklist

- [ ] Set `TELEGRAM_WEBHOOK_SECRET` to a fresh random value.
- [ ] Optionally set `TELEGRAM_LINK_CODE_TTL_MINUTES` (default `15`).
- [ ] `alembic upgrade head`.
- [ ] Restart the backend so the new settings are loaded.
- [ ] Call `setWebhook` with the matching `secret_token`.
- [ ] Confirm with `getWebhookInfo` that there is no `last_error_message`.
- [ ] Notify Telegram users that they must relink.

---

## 6. Known limitations

- **The webhook shares the global `/api/` rate limiter.** Telegram delivers from
  a small pool of source addresses, so a busy bot could be throttled per-IP
  under `RateLimitMiddleware`. Changing the middleware was out of scope for this
  fix. If updates start being dropped under load, exempt this path or raise the
  limit for it.
- **Header authentication only.** Telegram does not sign webhook bodies, so
  anyone who learns the secret can post convincing updates. The secret must be
  treated as a credential: HTTPS only, never logged, rotated with `setWebhook`.
- **No delivery-side confirmation.** A binding proves the chat sent us the code.
  If a user hands their code to somebody else and that person pastes it into
  *their* chat first, that chat wins. The short TTL and single use limit the
  window; the instructions tell users not to share the code.
- **Concurrency is proven on SQLite in tests.** The atomic-claim behaviour is
  covered by the test suite running on SQLite, which serialises writers. The
  same single-statement claim plus the unique constraint is what makes it safe
  on PostgreSQL, but genuinely parallel PostgreSQL contention is not exercised
  in CI unless `TEST_POSTGRES_URL` is set.
- **The bot does not reply in-chat.** The webhook returns its reply text in the
  HTTP response body, which Telegram discards. Users confirm success in the app
  rather than in the chat. Sending a real message back would need an outbound
  `sendMessage` call, which was out of scope here.

---

## 7. Tests

`backend/tests/test_telegram_binding.py` — 40 tests, all Telegram traffic
synthetic; no network call is made and no live message is sent. They cover the
removed bypass, secret-header authentication and fail-closed behaviour,
code expiry, replay and single use, group/channel refusal, one-chat-one-account,
concurrent redemption, malformed updates, delivery gating on `verified`,
owner-only unlinking, and the migration's effect on existing rows (by executing
the real `upgrade()` from revision `0006`, not a copy of its SQL).
