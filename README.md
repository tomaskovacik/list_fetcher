# list-fetcher

Exports SharePoint Online lists for backup and can restore them back into SharePoint Online in create-only mode.

## What it writes

For each exported list the app creates:

- `list.json` - raw list metadata
- `fields.json` - field definitions including `SchemaXml`
- `content_types.json` - content type definitions
- `items.ndjson` - one item per line with field payloads
- `attachments.json` - attachment metadata
- `attachments/` - attachment binaries grouped by item id
- `manifest.json` - local export manifest for the list

It also writes a top-level `manifest.json` with the run summary.

## Authentication

The app uses modern Entra app-only auth against SharePoint REST.

Certificate auth with a PEM private key and thumbprint is mandatory. Client secret auth is not supported in this tool.

Arguments can be passed directly or via environment variables:

- `SP_EXPORT_TENANT`
- `SP_EXPORT_CLIENT_ID`
- `SP_EXPORT_CERT_PATH`
- `SP_EXPORT_CERT_THUMBPRINT`

`SP_EXPORT_TENANT` can be either the tenant domain or the tenant UUID, but the examples below prefer the **tenant UUID** because it is unambiguous.

## Usage

The CLI prints status lines during site discovery, export, and restore so you can see which site and list is currently being processed.

Find list ids without downloading anything, e.g. to get an id for `--exclude-list-id`:

```bash
list-fetcher \
  --site-url https://contoso.sharepoint.com/sites/finance \
  --tenant 11111111-2222-3333-4444-555555555555 \
  --client-id 00000000-0000-0000-0000-000000000000 \
  --cert-path ./sharepoint-app-key.pem \
  --cert-thumbprint ABCDEF0123456789ABCDEF0123456789ABCDEF01 \
  --dry-run
```

```
LIST ID                               TITLE             HIDDEN  PATH
ad3deed7-2be4-4879-aa75-ae4934b67402  CARS DB           no      /sites/finance/Lists/CarsDB
8d522de2-c837-4013-a1aa-982eb34db7b5  Form Templates    yes     /sites/finance/Lists/Templates
2 list(s) matched (dry run, nothing downloaded)
```

`--dry-run` respects `--include-hidden` and `--exclude-list-id`/`--exclude-list-ids-file`, so you can also use it to confirm an exclusion list works before running a real export. `--output` is not required with `--dry-run`.

Discover and export all visible lists from a site:

```bash
list-fetcher \
  --site-url https://contoso.sharepoint.com/sites/finance \
  --tenant 11111111-2222-3333-4444-555555555555 \
  --client-id 00000000-0000-0000-0000-000000000000 \
  --cert-path ./sharepoint-app-key.pem \
  --cert-thumbprint ABCDEF0123456789ABCDEF0123456789ABCDEF01 \
  --output ./backup
```

Export lists from a file with one list URL per line:

```bash
list-fetcher \
  --list-urls-file ./lists.txt \
  --tenant 11111111-2222-3333-4444-555555555555 \
  --client-id 00000000-0000-0000-0000-000000000000 \
  --cert-path ./sharepoint-app-key.pem \
  --cert-thumbprint ABCDEF0123456789ABCDEF0123456789ABCDEF01 \
  --output ./backup
```

Exclude specific lists from discovery and export by id (GUID), not title - SharePoint's `Title` can vary per request language (see [Notes](#notes)), so name-based exclusion would be unreliable:

```bash
list-fetcher \
  --site-url https://contoso.sharepoint.com/sites/finance \
  --tenant 11111111-2222-3333-4444-555555555555 \
  --client-id 00000000-0000-0000-0000-000000000000 \
  --cert-path ./sharepoint-app-key.pem \
  --cert-thumbprint ABCDEF0123456789ABCDEF0123456789ABCDEF01 \
  --output ./backup \
  --exclude-list-id ad3deed7-2be4-4879-aa75-ae4934b67402
```

`--exclude-list-id` is repeatable; `--exclude-list-ids-file` points to a text file with one list id per line (`#` comments and blank lines are skipped).

Use certificate auth:

```bash
list-fetcher \
  --site-url https://contoso.sharepoint.com/sites/finance \
  --tenant 11111111-2222-3333-4444-555555555555 \
  --client-id 00000000-0000-0000-0000-000000000000 \
  --cert-path ./sharepoint-app.pem \
  --cert-thumbprint ABCDEF0123456789ABCDEF0123456789ABCDEF01 \
  --output ./backup
```

Restore all exported lists from a backup root back to their original sites:

```bash
list-fetcher \
  --restore-path ./backup \
  --tenant 11111111-2222-3333-4444-555555555555 \
  --client-id 00000000-0000-0000-0000-000000000000 \
  --cert-path ./sharepoint-app-key.pem \
  --cert-thumbprint ABCDEF0123456789ABCDEF0123456789ABCDEF01
```

## Docker Compose

The repository includes a `Dockerfile` and `docker-compose.yml`.

- `docker-compose.yml` pulls the pre-built image from `ghcr.io/tomaskovacik/list_fetcher` (published by CI on every push and release tag) rather than building locally
- `download` is the default service and runs the export flow
- `restore` is in the `tools` profile, so it does not start unless requested
- all runtime configuration is read from `.env`

Start by copying the sample file:

```bash
cp .env.example .env
```

Default export run:

```bash
docker compose up download
```

Pin a specific release instead of `latest` by setting `LIST_FETCHER_IMAGE_TAG` in `.env` (e.g. `0.3.0`), then `docker compose pull` to fetch it.

Restore run:

```bash
docker compose --profile tools run --rm restore
```

Find list ids without downloading anything: set `LIST_FETCHER_DRY_RUN=true` in `.env`, then run the `download` service as usual - it reuses your existing `LIST_FETCHER_SITE_URLS`/`LIST_FETCHER_LIST_URLS_FILE`/exclusion settings and doesn't require `LIST_FETCHER_OUTPUT_DIR`:

```bash
docker compose run --rm download
```

Generate a certificate/key pair for SharePoint app-only auth:

```bash
docker compose --profile tools run --rm certgen
```

This writes files into `./certs` in the current directory:

- `<name>-key.pem` - private key for this app
- `<name>-cert.pem` - public certificate you can upload to Entra
- `<name>-cert.cer` - DER form of the same public certificate
- `<name>-cert-info.txt` - thumbprint and next-step notes

Host paths (configurable, see below):

- `./data` is mounted to `/data` for exports and restore input
- `./config` is mounted to `/config` for list URL files, list id exclusion files, and certificate files
- `./certs` is mounted to `/certs` for the `certgen` service

Common `.env` values:

- `LIST_FETCHER_SITE_URLS` - comma or newline separated site URLs for export
- `LIST_FETCHER_LIST_URLS_FILE` - optional file such as `/config/lists.txt`
- `LIST_FETCHER_OUTPUT_DIR` - export destination inside the container, default `/data/backup`
- `LIST_FETCHER_RESTORE_PATH` - restore source path inside the container
- `LIST_FETCHER_TARGET_SITE_URL` - optional restore target override
- `LIST_FETCHER_CERT_NAME` - base file name for generated certificate material
- `LIST_FETCHER_CERT_COMMON_NAME` - certificate subject CN
- `LIST_FETCHER_CERT_DAYS` - certificate validity period
- `LIST_FETCHER_CERT_FORCE` - overwrite existing files when `true`
- `LIST_FETCHER_EXCLUDE_LIST_IDS` - comma or newline separated list ids (GUIDs) to exclude from discovery/export
- `LIST_FETCHER_EXCLUDE_LIST_IDS_FILE` - optional file such as `/config/exclude-list-ids.txt`
- `LIST_FETCHER_DRY_RUN` - set to `true` to discover and print matching lists instead of downloading them; `LIST_FETCHER_OUTPUT_DIR` is not required in this mode

Host directory and user overrides (Compose only):

- `LIST_FETCHER_DATA_DIR` - host directory bind-mounted to `/data`, default `./data`
- `LIST_FETCHER_CONFIG_DIR` - host directory bind-mounted to `/config`, default `./config`
- `LIST_FETCHER_CERTS_DIR` - host directory bind-mounted to `/certs`, default `./certs`
- `PUID` / `PGID` - user and group id the container runs as, default `1000`/`1000`. Set these to match the owner of the host directories above (e.g. your own `id -u`/`id -g`) if they differ from the image default; no image rebuild is needed.

Restore one exported list into a different target site:

```bash
list-fetcher \
  --restore-path "./backup/contoso.sharepoint.com__sites__finance/Invoices [list-guid]" \
  --target-site-url https://contoso.sharepoint.com/sites/restore \
  --tenant 11111111-2222-3333-4444-555555555555 \
  --client-id 00000000-0000-0000-0000-000000000000 \
  --cert-path ./sharepoint-app-key.pem \
  --cert-thumbprint ABCDEF0123456789ABCDEF0123456789ABCDEF01
```

## Restore behavior

- Restore is **create-only**. It fails if a list with the same title already exists in the target site.
- The app recreates the list shell, restores custom fields from `SchemaXml`, recreates items, and uploads classic list attachments.
- Item ids, created/modified timestamps, authors/editors, and GUIDs are not preserved.
- Custom content types are exported for reference but are not recreated yet during restore.

## Notes

- Hidden lists are skipped by default. Use `--include-hidden` to include them.
- SharePoint's list `Title` can vary per request depending on the caller's language context (multilingual title resources), even with no actual rename. Export directory names are chosen once per list id and then reused on later runs, and list exclusion (`--exclude-list-id`) is matched by id for the same reason - don't rely on title matching anything to stay stable across runs.
- The file input accepts common browser list URLs such as `/Lists/.../AllItems.aspx` and document library URLs such as `/Shared%20Documents/Forms/AllItems.aspx`.
- Item attachments are downloaded for classic lists. Document library files are represented as list items; they are not downloaded through the attachment path.
- For Compose, create `./data`, `./config`, and `./certs` on the host if they do not already exist, and make sure they are writable by the `PUID`/`PGID` you configure (default `1000:1000`).
