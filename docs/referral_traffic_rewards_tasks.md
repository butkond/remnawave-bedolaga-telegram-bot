# Referral VPN Usage Rewards: design and implementation tasks

## Goal

Add a permanent referral reward on top of the existing referral program:

> A user invites new users. When a configured number of invited users first really connects to the VPN, the inviter receives a configured number of free subscription days.

The feature must be low overhead, idempotent, compatible with the current referral/balance commission system, and resistant to obvious fraud.

Important: usage rewards are not an extra condition on top of the existing monetary referral system. The current system is based on real top-ups. The new system is based on the first real VPN connection signal from Remnawave and should work even when invited users never top up.

Usage mode is one-shot:

- Each invited user can qualify only once.
- Qualification happens when Remnawave sends `user.first_connected` for that invited user.
- The referrer receives the configured free time only once, when the configured number of invited users has qualified.
- Do not implement repeated reward cycles unless the product requirement changes explicitly.

## Current implementation summary

- Referrer relation: `users.referred_by_id`.
- Referral code: `users.referral_code`.
- Pending referral before registration: Redis keys in `app/services/referral_service.py`.
- Registration event: `process_referral_registration()` creates a zero `ReferralEarning` with reason `referral_registration_pending`.
- Monetary rewards: `process_referral_topup()` is called by payment providers after successful top-up and writes `ReferralEarning`.
- Purchase commissions from balance are intentionally unused to avoid double commission.
- Referral contests exist (`ReferralContest`, `ReferralContestEvent`), but they are event/leaderboard oriented and time-bound. They can inspire admin reporting, but should not be the primary storage for this permanent reward.
- Real VPN usage is signaled by Remnawave webhook `user.first_connected`, currently handled by `RemnaWaveWebhookService._handle_first_connected()` in `app/services/remnawave_webhook_service.py`. The log line is `Webhook: user first VPN connection`, and the endpoint is `POST /remnawave-webhook`.
- Fork-specific constraint: Remnawave user matching must stay UUID-based.

## Recommended architecture

Use a separate reward subsystem, not extra overload inside `ReferralEarning` only.

Recommended compatibility model:

- Add a referral attribution mode.
- A referrer can have an active mode, for example:
  - `balance_commission`: existing behavior, rewards depend on successful top-ups.
  - `traffic_reward`: new behavior, rewards depend on invited users reaching first real VPN connection.
- Available modes are fixed by env. `REFERRAL_PROGRAM_ENABLED=false` disables the whole referral program. `REFERRAL_REWARD_MODES=balance_commission`, `REFERRAL_REWARD_MODES=traffic_reward`, or `REFERRAL_REWARD_MODES=balance_commission,traffic_reward` controls which systems exist.
- Store the referrer's current active mode on the user (`users.referral_reward_mode`). If only one mode is available, the user should not choose and the system falls back to that single mode.
- Capture the active mode immutably when the invited user applies the referral code/registers.
- The invited user's later qualification logic follows the captured mode, not the referrer's current mode.
- Do not allow one referral to generate rewards in both systems unless the business explicitly enables a hybrid mode.
- UI mode selection is shown only when both `balance_commission` and `traffic_reward` are enabled. If only one system is enabled, users should not choose anything; only explanatory text may differ.

This is better than checking the referrer's current mode at reward time, because reward-time checks allow mode switching after seeing which referrals became valuable.

Proposed tables:

- `referral_attributions` or extra fields on `users`
  - `referral_id`
  - `referrer_id`
  - `referral_code`
  - `mode`
  - `mode_captured_at`
  - unique `referral_id`
- `users.referral_reward_mode`
  - nullable current active mode for the referrer.
  - If null or unavailable under current env, use the configured default/only available mode.
- `referral_traffic_qualifications`
  - `id`
  - `referrer_id`
  - `referral_id`
  - `subscription_id`
  - `remnawave_uuid`
  - `source_event` (for example `user.first_connected`)
  - `qualified_at`
  - unique `referral_id` because the product uses one env-managed reward rule.
- `referral_traffic_reward_grants`
  - `id`
  - `referrer_id`
  - `qualified_count_at_grant`
  - `reward_days`
  - `subscription_id`
  - `granted_at`
  - unique `referrer_id` because the usage reward is granted once under the env-managed rule.

Not recommended:

- Reuse `ReferralContestEvent` for the permanent reward. It has useful uniqueness semantics, but its contest lifecycle and leaderboard assumptions will leak into a different product rule.
- Scan all referrals/subscriptions in a frequent background job. It is simpler to code, but grows linearly with users and duplicates data already arriving through Remnawave webhooks.
- Use the referrer's current mode at reward time. It creates fraud and accounting ambiguity because old referrals can be moved between reward schemes after the fact.
- Require top-ups for usage-mode qualification. That would turn the new system back into a variant of the existing money-based referral program.
- Use traffic counters (`Subscription.traffic_used_gb`, `user.modified`, sync jobs, or bandwidth threshold webhooks) for qualification. The qualifying signal is only the first VPN connection webhook.

## Event flow

1. Remnawave webhook `user.first_connected` arrives at `POST /remnawave-webhook`.
2. `RemnaWaveWebhookService` resolves the user/subscription by Remnawave UUID as it already does for user-scoped webhooks.
3. `_handle_first_connected()` calls the reward service with `(db, user, subscription, data, bot)`.
4. If feature disabled, no-op.
5. If no resolved user or no stable Remnawave UUID, no-op.
6. Check `user.referred_by_id`; if absent, no-op.
7. Load captured attribution and continue only if its mode is `traffic_reward`.
8. Apply anti-fraud eligibility checks.
9. Insert qualification idempotently for `referral_id`.
10. If inserted, check referrer progress using an indexed count or maintained aggregate.
11. If the required qualified-referral count is reached and no grant exists for `referrer_id`, insert the one-time grant and extend the selected referrer subscription by `reward_days`.
12. Sync/update Remnawave through existing subscription service paths only after a grant is created.
13. Notify referrer only after the grant commit succeeds.
14. Continue existing first-connected user notification behavior independently from reward processing.

## Performance requirements

- The hot path must be O(1) or O(log n) per first-connected webhook.
- Add indexes for:
  - `referral_traffic_qualifications(referral_id)` unique.
  - `referral_traffic_qualifications(referrer_id)`.
  - `referral_traffic_reward_grants(referrer_id)` unique.
- Avoid joining all referrals on webhook. One user lookup, one attribution lookup, one qualification insert, one indexed count/aggregate is acceptable.
- Do not call Remnawave API unless a reward is actually granted.
- Prefer DB idempotency over Redis locks. Redis can throttle notifications, but DB uniqueness must be the source of truth.

## Anti-fraud rules

Default recommended checks:

- Block self-referral (`referral_id == referrer_id`).
- Do not require a real paid signal in `traffic_reward` mode. The qualifying signal is real VPN usage proven by `user.first_connected`.
- Capture referral mode at registration/referral-code application and never recalculate it from the referrer's current mode.
- Count a referral only once, regardless of number of subscriptions, traffic resets, or repeated webhook delivery.
- Grant the usage reward to each referrer only once under the env-managed rule.
- Ignore users/subscriptions without stable Remnawave UUID.
- Ignore traffic reset, bandwidth threshold, `user.modified`, and sync events for qualification.
- Add admin audit visibility for qualifications/grants before enabling automatic large rewards.
- Optional: require the referral account age to be above a small threshold before it can qualify.
- Optional: limit how often a referrer can switch active mode, for example once per day/week, and show the current mode in admin/cabinet.
- Optional: require the first-connected event to belong to a normal user subscription type, not service/test/tunnel UUIDs.

## Settings to add

Add to `app/config.py` and `.env.example`:

- `REFERRAL_PROGRAM_ENABLED=true`
- `REFERRAL_REWARD_MODES=balance_commission` (CSV: `balance_commission`, `traffic_reward`, or both)
- `REFERRAL_DEFAULT_REWARD_MODE=balance_commission`
- `REFERRAL_TRAFFIC_REWARDS_ENABLED=false`
- `REFERRAL_TRAFFIC_REWARD_REQUIRED_REFERRALS=3`
- `REFERRAL_TRAFFIC_REWARD_DAYS=7`
- `REFERRAL_TRAFFIC_REWARD_QUALIFICATION_WINDOW_DAYS=0`
- `REFERRAL_TRAFFIC_REWARD_NOTIFY=true`
- `REFERRAL_MODE_SWITCH_COOLDOWN_HOURS=24`

The free-days rule is intentionally env-managed. There is no separate program table.

## Implementation checklist

1. Add models and Alembic migration for attribution, qualification, and grant tables.
2. Add attribution storage for captured referral mode. Prefer a separate table if auditability matters; use `users` fields only for a smaller first cut.
3. Add CRUD module, for example `app/database/crud/referral_traffic_reward.py`.
4. Add service, for example `app/services/referral_traffic_reward_service.py`.
5. Add a single public method like `process_first_connected(db, user, subscription, data=None, bot=None)`.
6. At referral registration/application time, persist the referrer's current mode from `users.referral_reward_mode` into attribution; if unavailable, use the env default/only available mode.
7. Call the service from `RemnaWaveWebhookService._handle_first_connected()` before or after the existing user notification. Keep reward processing idempotent so repeated `user.first_connected` delivery is harmless.
8. Do not hook `update_subscription_usage()`, `user.modified`, Remnawave sync, traffic reset, or bandwidth threshold paths for this reward.
9. Extend the referrer subscription via existing subscription service/CRUD and sync Remnawave only after creating a grant.
10. Add UI for active-mode selection only if both systems are enabled. If exactly one system is enabled, keep the current UI shape and adjust only explanatory texts if needed.
11. Add localized notification strings in both `app/localization/locales/{ru,en}.json` and root `locales/{ru,en,ua,fa,zh}.json`; also check `app/localization/texts.py`.
12. Add admin/cabinet/API visibility only after the core service is tested.
13. Add tests for immutable mode attribution, one-time referral qualification, one-time referrer grant, required-referral count reached, no-top-up qualification, repeated webhook idempotency, ignored traffic updates, single-system UI, dual-system UI, multi-subscription behavior, and Remnawave `user.first_connected` webhook integration.
14. Run `make fix` and targeted tests, at minimum:
    - `uv run pytest tests/services/ -k "referral"`
    - `uv run pytest tests/webserver/test_remnawave_webhook.py`

## Open decisions before coding

- Should one invited user qualify once globally or once per active paid subscription? Recommended: once globally.
- Which referrer subscription receives free days when the referrer has multiple active subscriptions? Recommended: primary/last active subscription, not all active subscriptions.
- Should old referrals created before feature launch be eligible if they receive `user.first_connected` after launch?
- Should referrers be allowed to switch modes themselves, or only admins can change the active referral mode?
- If switching is allowed, what cooldown and UI warning are required?
- Should hybrid mode ever be allowed, or must each invitation belong to exactly one reward mode?
