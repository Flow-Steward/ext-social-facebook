# Facebook Pages for Flow Steward

Standalone `flowsteward.facebook` tool provider, version 0.1.0, host contract
`extension_host_v2`, public extension SDK 0.5.0 or later. The generic callback
profile requires extension runtime capability 1.9.0, delivered with app release
0.9.18 or later; these are separate version numbers.

## Connect a Page

Create a project connection of type **Facebook Page**, one per Page. Enter the
numeric Page ID, Meta app ID and an explicit supported Graph API version. The
contract fixtures use **v27.0**; no provider account has been tested. There is no
auto-latest version selection. Store a **Page access token** as a connection secret.
The **Meta app secret** is optional for publishing and reading; store it before
receiving comment webhooks. Never put tokens in workflow inputs or URLs.

The token's app user needs appropriate Page tasks and permissions for the calls:
`pages_manage_posts` to publish, `pages_manage_engagement` to reply,
`pages_read_engagement` and the parent object's permissions to read engagement,
and `pages_read_user_content` where Meta requires user-content access. Check the
current Meta documentation and app review requirements for your app mode and audience. A token for another Page is
not authorization to access that Page through this connection.

**Verify connection** only reads the configured Page identity and compares its ID;
it never publishes a probe post or reply. No connection or credentials produces
`not_configured`, rather than a claim of verified access.

## Operations

- `publish_post(text, link_url?)` creates a Page feed post.
- `publish_photo(image_url, text)` publishes a remote HTTPS photo and caption.
- `list_posts(limit?, cursor?)` and `get_post(post_id)` read Page content.
- `list_comments(post_id, limit?, cursor?)` reads a Page post's comments.
- `list_replies(comment_id, limit?, cursor?)` reads a comment's replies.
- `reply_to_comment(comment_id, text)` replies as the selected Page.

Every visible action has its same-ID V2 operation and step form. Select each
operation's intended Page connection explicitly. Reads use bounded pages (limit
1–100) and opaque `next_cursor` tokens; they never follow provider `paging.next`
URLs. Posts and comments must belong to the selected Page. Publishing and replies
declare required host external-effect idempotency and Test Run suppression. An
ambiguous write timeout must not be blindly retried: inspect Facebook and the
host effect history first.

## Receive new comments

After saving the first Page connection, open **Webhooks**, select the project's
intended anchor connection and create its manual callback registration. Copy the
one-time **Callback URL** and **Verify Token** from that setup result. Keep them
secure: a later redacted registration list does not reveal the token again.
Connection creation stays on its own page and works before any account exists.

Expose the generated callback URL through **public HTTPS**. In the Meta app
dashboard configure the Page webhook with that URL and its generated verify token.
The token is the callback registration token, not the Meta app secret or Page token.
Subscribe the app to Page `feed` events and subscribe the intended Pages to the app
(`/{page-id}/subscribed_apps` with `subscribed_fields=feed` as the current Meta
setup requires). `pages_manage_metadata` and appropriate Page moderation rights
are required. App review, Live mode and provider-side Page subscriptions are
operator setup; saving a Flow Steward callback alone does not subscribe Meta.

The host handles `hub.mode=subscribe`, `hub.verify_token` and `hub.challenge` without
invoking extension code. POST bodies use exact raw-body HMAC-SHA256 verification
with `X-Hub-Signature-256: sha256=<hex>` and this anchor's stored `app_secret`.
A missing signing key rejects incoming POST signatures. No unauthenticated
request reaches event normalization.

A Meta app has one callback configuration for this object. For several Pages on
the same app, choose **one webhook anchor connection** for intake. Configure
the **Webhook Page allowlist** by adding each accepted Page ID with **Add** or
Enter. Leave this field untouched when creating a connection to omit
`webhook_page_ids` and accept only its `page_id`. Removing the final added Page
saves an explicit empty list, which accepts none. Adding the connection's Page ID
restores own-Page intake. Editing an existing list preserves its saved values.
Each Page must belong to that app and be subscribed provider-side.
Different apps use their own anchors and app secrets. Do not share an anchor
across customer projects: callbacks keep the host's authenticated project and
anchor `connection_id`, and cannot discover or remap another connection.

A workflow's **New Facebook Page comment** trigger can filter `page_id` and
`post_id`. Its payload includes provider, project_id, connection_id, page_id,
post_id, comment_id, author_id, text, created_time and dedupe_key. The default is
root comments only: replies (`parent_id != post_id`), comments authored by the
configured anchor Page or any allowed Page, edits, removals, unrelated objects
and foreign Pages/posts are ignored. This prevents own reply loops. Every matching event includes stable
top-level `source_id` and `correlation_id`, namespaced by provider, anchor
connection, Page/post asset, comment and the created action. Delivery timestamps,
comment creation-time formatting and message text do not alter this identity.
The existing durable host provider-event trigger delivery ledger deduplicates repeated deliveries across
process restarts; no process-local cache is claimed as durable protection.

## Verification

Run from a matching Flow Steward checkout with runtime capability 1.9.0 and its
managed Docker audit environment. Replace the absolute path with this standalone
checkout. The development SDK wheel is bundled under `dev-wheels/`; these checks
install development requirements offline and provider networking stays blocked.
Schedule them through the repository's normal coordinated gate lease:

```sh
./core/entrypoints/cli/scripts/lint.sh --checks extension-static,extension-tests --extension-root /absolute/path/to/flowsteward_facebook
./core/entrypoints/cli/scripts/lint.sh --checks extension-tests --extension-root /absolute/path/to/flowsteward_facebook --test-paths tests/test_host_contracts.py
```

The complete extension suite includes stdin/stdout and credential-free subprocess
health checks, operation contracts, callback normalization and the real pinned
adapter through a fake SDK opener. `tests/test_host_contracts.py` runs only when
`/app/core` exists inside managed `fs-audit`; it explicitly skips elsewhere.
It feeds actual provider output into the actual host ingestion, trigger replay
and external-effect confirmation consumers, replacing persistence and queue
boundaries with fakes. This characterizes routing, replay identity and metadata;
it does not establish PostgreSQL atomicity, durable storage or live job execution.
Validate the descriptor and package only after the selected checks pass. No live
Meta account, permission, Graph-version or publishing acceptance is implied by
offline results.

## Official references

- [Pages posts](https://developers.facebook.com/docs/pages-api/posts/)
- [Object comments and permissions](https://developers.facebook.com/docs/graph-api/reference/object/comments)
- [Page webhook fields](https://developers.facebook.com/docs/graph-api/webhooks/reference/page/)
- [Page app subscriptions](https://developers.facebook.com/docs/graph-api/reference/page/subscribed_apps/)
- [Webhook verification](https://developers.facebook.com/docs/graph-api/webhooks/getting-started/)
- [Meta Business SDK Graph request encoding](https://github.com/facebook/facebook-python-business-sdk/blob/main/facebook_business/api.py)

See `PRIVACY.md` for data handling and `BRANDING.md` for artwork provenance.
