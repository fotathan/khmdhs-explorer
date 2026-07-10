# Attachments on object storage — enablement runbook

Act attachments are **off in prod** and store to the local disk in dev. Render
disks are ephemeral, so to use attachments in prod, point them at an
**S3-compatible object store** (AWS S3, Cloudflare R2, or Supabase Storage's S3
endpoint). The code backend is already built and tested (`ATTACHMENTS_BACKEND=s3`
in `app/attachments.py`); this is the config to turn it on.

> `storage_ref` is backend-agnostic, so you can start on `s3` cleanly (there's
> nothing to migrate off local disk in prod — the feature was never on there).

## 1. Create the table on prod (one-off)

`proc.act_attachment` exists locally but **not on prod** — it was a local-only
migration, and the migration baseline marked it "applied" on prod without
running it (the tracker checks file checksums, not whether DB objects exist, so
`migrate.py status` can't catch this). Create it (idempotent):

```bash
zsh -c 'source ~/.zshrc; psql "$KHMDHS_PROD_DB_URL" -f attachment_migration.sql'
```

## 2. Create a private bucket + S3 credentials

Pick one; the app streams files through its own authenticated download route, so
the **bucket must be PRIVATE** (no public read):

- **Supabase Storage** → create a bucket (private); Project Settings → Storage →
  S3 connection gives you the endpoint + access key/secret + region.
- **Cloudflare R2** → create a bucket; make an R2 API token (S3 keys); endpoint is
  `https://<accountid>.r2.cloudflarestorage.com`, region `auto`.
- **AWS S3** → create a private bucket + an IAM user with put/get/delete on it;
  no endpoint needed, set the real region.

## 3. Set env vars on the Render web service

```
ATTACHMENTS_ENABLED=1
ATTACHMENTS_BACKEND=s3
ATTACH_S3_BUCKET=<bucket>
ATTACH_S3_ACCESS_KEY_ID=<key>
ATTACH_S3_SECRET_ACCESS_KEY=<secret>
ATTACH_S3_ENDPOINT=<url>        # set for R2/Supabase; OMIT for AWS S3
ATTACH_S3_REGION=auto           # or e.g. eu-west-3 on AWS
ATTACH_S3_PREFIX=acts           # optional
ATTACH_MAX_MB=80                # optional (default 80)
```

Then redeploy / restart.

## 4. Verify

- On an act page (as admin), the attachments panel appears; upload a file →
  it should store and be listed.
- Download it back (`/act/<adam>/attachment/<id>`); the bytes should match.
- Confirm the object exists in the bucket under `acts/<adam>/<uuid>__<name>`.
- Delete it from the UI → the object is removed.

## Notes

- Files are served **through the app** (authenticated), never via public bucket
  URLs — keep the bucket private.
- Uploads/OCR are still resource-heavy and, if opened beyond admins, need
  per-user quotas / rate limits (out of scope here).
- To roll back: set `ATTACHMENTS_ENABLED=0` (feature off) or
  `ATTACHMENTS_BACKEND=local_fs` (dev only).
