# пра.бел — Telegram bot (starter implementation)

Python 3.11 or newer. Run ONE instance. This is generated starter code,
not integration-tested against your live Supabase or Telegram account.

## Files
- bot.py: Belarusian dialogue, durable drafts, multi-message poems.
- setup.sql: new protected tables + transactional submission function.
- admin_examples.sql: manual linking, approval and publication examples.
- .env.example: configuration template, no real credentials.

## Setup
1. Back up the database and review setup.sql. Run it ONCE in Supabase SQL Editor.
   It enables RLS on posts and creates telegram_users, telegram_drafts and
   telegram_submit. Existing posts/authors/policies are not deleted.
   The provided SELECT policy exposes only status=published when RLS is enabled.
   Do not apply a second time: tables/functions already exist after success.
2. Create a bot with @BotFather: /newbot. Keep the token private.
3. Copy .env.example to .env and enter the token and Supabase SERVER secret key
   (or legacy service_role key). Not an anon/publishable key.
4. Run commands in a separate bot folder, not your website's deployed assets:

   python -m venv .venv

   Git Bash on Windows:
   source .venv/Scripts/activate

   Linux/macOS:
   source .venv/bin/activate

   python -m pip install -r requirements.txt
   python bot.py

   If python is not recognized on Windows, try py -3 instead.

5. In Telegram send /id. Link the actual numeric ID to the existing author
   using admin_examples.sql after verifying identity.
   Test using both approved and unapproved accounts before real submissions.

## Dialogue
/start resumes the current draft, /new replaces it (old unfinished draft lost),
/cancel cancels, /id displays numeric user ID.

Poem: type -> language -> title or *** -> text chunks -> Done -> review -> Send.
Translation: type -> original language -> target language -> original author
(or unknown author) -> title -> text -> review -> Send.
Language options come from your languages table. Original_language stores the
language CODE, e.g. en. Adapt the website display if it expects a human label.
Each text chunk is joined with TWO newlines; within chunks whitespace is preserved.
Maximum title/original author 200 characters, display name 100, poem 60000.
Review is plain text: no Markdown parsing, so *** and special characters survive.

## Author identity
Existing authors are NEVER linked automatically by @username or claimed name.
Administrator links them using numeric Telegram ID. Do this before the first
submission to avoid creating an extra profile.
For an unlinked user the bot asks for a display name and creates a NEW authors
row at submission, with is_approved=false by default. This does NOT verify the
person's identity. Such a profile is publicly readable under your existing
Read public authors policy, even while its post is draft. If you require fully
private profile review, change this workflow before deployment.
The translator is posts.author_id; original author is posts.original_author.

## Submission safety
The RPC reads is_approved at final submission, within the same database transaction
as the post insert. It uses lookup tables for ids, not hardcoded status ids.
Approved: published + timestamp. Everyone else: draft + null published_at.
Draft UUID is also post UUID. Repeating Send returns the existing result instead
of inserting twice. This only deduplicates one draft, not separately recreated poems.
Drafts survive restarts. Both new bot tables deny anon/authenticated access;
RPC execute is restricted to service_role. Secret credentials never enter HTML.
Existing database triggers were not provided; test the insert behavior in staging.

## Moderation and hosting
Moderation is MANUAL in Supabase using the examples, not Telegram admin buttons.
There are no moderator notifications or publication notifications in this version.
Users can submit drafts even when not approved.
Long polling needs a running process; closing the terminal stops the bot.
GitHub Pages cannot run this Python service. Use an always-on worker/server.
Generated code has no per-user quota/spam protection, so add it before a large
public launch. Logs deliberately omit submitted text, tokens and request dumps.

## Secrets
Never commit .env. If placing this folder in an existing repository, merge the
provided ignore patterns into the root .gitignore. .gitignore does not remove
secrets already tracked: rotate any leaked credentials.

## Minimum test checklist
- unapproved: post is draft, published_at null, absent from anon SELECT.
- approved: post published immediately with correct author.
- translation: source and target codes correct, translator and original distinct.
- untitled: title exactly ***; stanza spacing intact.
- restart midway: /start resumes.
- double Send: one posts row.
- anon cannot read telegram_users/drafts or invoke telegram_submit.
- a different user's username/name cannot claim an existing author profile.
