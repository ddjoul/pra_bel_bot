# Database migration tracking

## Applied security patch

The migration `20261014203000_restrict_comment_identity_and_web_session_grants.sql` records the exact SQL applied to production project `vqcjtkruncqslrxeekmz` on 2026-10-14 under the name `restrict_comment_identity_and_web_session_grants`. The filename timestamp is an archival identifier, not a verified Supabase migration-history version. Reconcile the actual history version before using Supabase CLI migration commands.

It restricts public comment reads to non-identifying fields and removes public privileges on web sessions. Existing guest INSERT privileges and RLS policies remain unchanged. Assertions validate privilege changes; they are not end-to-end browser tests.

## Scope and limitations

This directory is not yet a complete database baseline and cannot initialize an empty database. Older schema definitions, triggers, lookup seed data, Storage policies and migration history still need to be exported, reviewed and reconciled. Do not run old setup scripts over production to reconstruct missing history.

No user records, session tokens or environment secrets are included here. Webhook fail-closed hardening and server-side guest anti-spam remain separate pending tasks.
