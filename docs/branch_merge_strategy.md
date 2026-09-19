# Branch merge strategy

## Current branches

The fork has three important long-lived/custom branches:

- `customization` - main WGC/custom behavior branch.
- `disco` - branding/product variant that should receive the same core fixes as `customization`.
- `add-traffic-reward-referral-system` - referral reward system branch that should be merged before the later upstream/main update.

`main` has already been updated from upstream and includes a large Remnawave 3.0.0 migration. The custom branches are still based on the older pre-Remnawave-3.0.0 code.

## Decision

The referral reward system may be merged and deployed before merging the updated `main`.

This is acceptable because the core referral attribution data uses local bot user ids:

- `users.referred_by_id`
- `referral_attributions.referral_id`
- `referral_attributions.referrer_id`
- `referral_attributions.mode`

The Remnawave-dependent part is only the first-connected traffic reward qualification/audit path.

## First-connected recovery in referral rewards

The `add-traffic-reward-referral-system` branch now includes recovery for missed Remnawave
`user.first_connected` webhooks.

The primary source of qualification is still the webhook:

```text
event: user.first_connected
path:  /remnawave-webhook
```

Full Remnawave user sync is a recovery path only. During `/api/users/stream` sync, the bot reads:

```text
userTraffic.firstConnectedAt
```

and, when it is present, queues a recovery candidate. After the normal sync batch commits are finished, recovery candidates are processed through the same idempotent service:

```text
process_first_connected(..., event='sync.first_connected_recovery')
```

This keeps the transaction boundary of the normal sync predictable: referral reward processing does not commit half-finished sync batches from inside the user update loop.

Recovery candidates must store only scalar identifiers and panel payload data, not live SQLAlchemy ORM objects. Process recovery candidates in a separate `AsyncSessionLocal` session and reload `User` and `Subscription` with async session methods. This avoids `MissingGreenlet` failures after a duplicate qualification performs an internal rollback and expires ORM instances in either the recovery flow or the main sync session.

Do not replace this with `usedTrafficBytes > 0`. Recovery must be based on `firstConnectedAt`, otherwise the feature effectively returns to the old traffic-consumption model.

Duplicate safety remains:

```text
referral_traffic_qualifications.UNIQUE(referral_id)
```

So a late webhook after sync recovery, or repeated full syncs, must not grant the same referral qualification twice.

If qualification already exists but the reward grant is missing, processing must continue to count qualified referrals and create the missing grant when the threshold is satisfied. This covers partial failures where the qualification insert committed but the later grant/extension step did not run or failed.

## Migration strategy for referral rewards

Production referral migration state:

```text
0094 -> c001_referral_traffic_rewards
```

The referral reward migration must use a custom revision that cannot collide with upstream's `0095...0104` chain:

```python
revision = 'c001_referral_traffic_rewards'
down_revision = '0094'
```

Filename:

```text
migrations/alembic/versions/c001_referral_traffic_rewards.py
```

After this migration is applied in production, do not rename it and do not change its `revision` or `down_revision`.

The migration is already squashed: it creates `referral_attributions`, `referral_traffic_qualifications`,
`referral_traffic_reward_grants`, and includes `reward_cycle` plus the unique constraint
`UNIQUE(referrer_id, reward_cycle)` from the initial schema. There is no separate temporary
`c002_referral_reward_cycles` migration in the production chain.

When updated `main` is merged later, upstream brings its own line:

```text
0094 -> 0095 -> 0096 -> ... -> 0104
```

The resulting Alembic graph will have two heads:

```text
          c001_referral_traffic_rewards
         /
0094
         \
          0095 -> ... -> 0104
```

Resolve that with an empty Alembic merge migration:

```python
revision = 'c002_merge_referral_rewards_remnawave_3'
down_revision = ('c001_referral_traffic_rewards', '0104')
```

This migration should not change schema or data. It only tells Alembic that both already-applied migration lines are valid and the history is joined again.

Do not edit upstream migrations to make `0095.down_revision = 'c001_referral_traffic_rewards'`. That increases future upstream merge conflicts and rewrites upstream migration history.

## Merge order

Preferred order for the current priority:

1. Merge `add-traffic-reward-referral-system` into `customization`.
2. Keep the squashed custom referral migration as `c001_referral_traffic_rewards`.
3. Keep `REFERRAL_TRAFFIC_REWARDS_ENABLED=false` unless the traffic reward mode is intentionally ready for production.
4. Merge updated `customization` into `disco`.
5. Later merge updated `main` into `customization`.
6. Resolve Remnawave 3.0.0 conflicts once in `customization`.
7. Add the empty merge migration after upstream `0104` and local `c001_referral_traffic_rewards` are both present.
8. Merge updated `customization` into `disco` again.

## Remnawave 3.0.0 identity change

Upstream `main` changes the primary Remnawave user identity from string `uuid` to numeric panel `id`.

Before:

```text
users.remnawave_uuid
subscriptions.remnawave_uuid
DELETE /api/users/{uuid}
PATCH /api/users/{uuid}
```

After Remnawave 3.0.0:

```text
users.remnawave_id
subscriptions.remnawave_id
DELETE /api/users/{id}
PATCH /api/users/{id}
```

`remnawave_uuid` is retained as historical/audit data, not the active panel user key.

The fork-specific safety rule remains conceptually the same:

```text
Do not bind bot users to panel users by unsafe email/username guessing.
```

But the technical key changes:

```text
old stable key: remnawave_uuid
new stable key: remnawave_id
secondary exact key: remnawave_short_uuid
```

When merging Remnawave 3.0.0, preserve this fork behavior by adapting the strict identity checks to `remnawave_id`/`remnawave_short_uuid`, not by trying to keep old UUID-only logic unchanged.

## Backfill and data safety

Upstream migration `0104_remnawave_numeric_id.py` only adds nullable `remnawave_id` columns. It does not remove old UUID columns.

Old production rows receive numeric `remnawave_id` through the one-shot backfill:

```bash
python -m scripts.backfill_remnawave_ids
python -m scripts.backfill_remnawave_ids --apply
```

Run dry-run first and inspect unresolved/conflicts before applying.

For this fork, review the backfill strategies before apply. Prefer exact and safe matches:

- `subscriptions.remnawave_short_uuid`
- strict unambiguous panel identity cases

Avoid automatically trusting weak email/username matches unless explicitly reviewed.

## Referral traffic reward after Remnawave 3.0.0

The referral relationship itself is safe because it uses local `users.id`.

Traffic reward grants are recurring cycles:

```text
rewarded_qualified_count = last_grant.qualified_count_at_grant
unrewarded_qualified_count = qualified_referrals_count - rewarded_qualified_count
new_cycles = unrewarded_qualified_count // REFERRAL_TRAFFIC_REWARD_REQUIRED_REFERRALS
UNIQUE(referrer_id, reward_cycle)
```

So with `REFERRAL_TRAFFIC_REWARD_REQUIRED_REFERRALS=1`, every newly qualified referral can create the next
grant cycle. With `REFERRAL_TRAFFIC_REWARD_REQUIRED_REFERRALS=2`, every two newly qualified referrals create
the next grant cycle. Already rewarded referrals must not be reused after changing
`REFERRAL_TRAFFIC_REWARD_REQUIRED_REFERRALS`; the last grant's `qualified_count_at_grant` is the paid progress
watermark.

The current traffic qualification table stores:

```text
referral_traffic_qualifications.remnawave_uuid
```

That field is audit/debug context, while idempotency is enforced by:

```text
UNIQUE(referral_id)
```

During the Remnawave 3.0.0 merge, adapt the traffic reward code so `user.first_connected` qualification no longer requires `remnawave_uuid`. Recommended schema follow-up:

```text
referral_traffic_qualifications.remnawave_id nullable
referral_traffic_qualifications.remnawave_short_uuid nullable
```

or a single neutral audit field such as `panel_user_id`.

The current branch can recover missed webhooks through `userTraffic.firstConnectedAt`, so a missed webhook is no longer automatically fatal before the Remnawave 3.0.0 merge.

However, during the Remnawave 3.0.0 merge, review the recovery path together with the webhook path:

- sync must still read the panel's first-connected timestamp;
- the audit field must no longer require legacy `remnawave_uuid`;
- qualification idempotency must stay keyed by local `referral_id`;
- recovery must still run after normal sync commits, not inside the update loop.
- recovery must pass a temporary bot instance into the shared first-connected reward logic when
  `REFERRAL_TRAFFIC_REWARD_NOTIFY=true`, so rewards granted by full sync notify the referrer just like webhook grants.

Until the Remnawave 3.0.0 identity adaptation is merged and verified, keep `REFERRAL_TRAFFIC_REWARDS_ENABLED=false` during that transition window.
