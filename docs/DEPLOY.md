# Publish a private pilot

This deployment uses one HTTPS web service for the frontend, API, worker and Streamable HTTP endpoint. It keeps cookie authentication same-origin. The managed services are examples. Check current costs, usage limits and availability before creating resources. The polling worker and frequent readiness checks can keep Neon awake, and a sleeping web service delays processing.

## 1. Prepare the repository

From the extracted source directory, initialize your repository and review the files. Keep `.env`, secrets, data volumes and real documents out of git. Push to a GitHub repository you control when you are ready. Wait for **Validate** to pass before hosting it.

```sh
git init -b main
git add .
git commit -m "Initial EvidenceFlow release"
git remote add origin <your-repository-url>
git push -u origin main
```

## 2. Prepare Postgres and storage

- Create a dedicated Neon project or database for this pilot. Copy its **direct** connection string into a secret named `DATABASE_URL`. Use TLS with the options from the Neon console. The app runs migrations on startup, so the initial DB role needs permission to create tables in the target schema. [Neon connection documentation](https://neon.com/docs/connect/connect-from-any-app)
- Create a **private** Cloudflare R2 bucket and generate S3 Object Read & Write credentials scoped to that bucket. Copy the R2 endpoint and credentials into server secrets. There is no public bucket URL or bucket CORS requirement. [R2 S3 setup](https://developers.cloudflare.com/r2/get-started/s3/)

## 3. Create the web service

In a Docker-capable host such as Render, connect the repository and create a **Docker Web Service** with the repository root as the build context. The Dockerfile sets the startup command, and the process listens on the host’s `PORT`. Configure the health path `/healthz`. Use a single instance for the first pilot. [Render Docker](https://render.com/docs/docker) · [Render health checks](https://render.com/docs/health-checks)

| Variable | Value |
| --- | --- |
| `DATABASE_URL` | Neon connection secret |
| `APP_ORIGIN` | The public HTTPS origin, e.g. `https://your-service.onrender.com`, with no path |
| `COOKIE_SECURE` | `true` |
| `WORKER_ENABLED` | `true` |
| `STORAGE_BACKEND` | `s3` |
| `S3_ENDPOINT` | `https://<account-id>.r2.cloudflarestorage.com`, or the bucket’s jurisdiction endpoint |
| `S3_BUCKET` | Your private bucket name |
| `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY` | Bucket-scoped S3 credential pair |
| `S3_REGION` | `auto` |
| `HF_ALLOW_EVIDENCE` | `false` for the first deploy |
| `HF_TOKEN` | Leave unset until you intend to use HF |

Allowlist any trusted proxy IPs only if you know how your host forwards client IPs. Keep the external rate and request-size limits aligned with the 5 MiB upload cap, and enable only the canonical public origin. A separate Pages frontend would require a same-origin reverse proxy for API routes and is outside this MVP deployment.

## 4. Provision two pilot users and verify

Open a private operator shell with the service’s server-side environment, or run the operator CLI locally with `DATABASE_URL` set to the hosted DB secret in that shell. Provision a reviewer and analyst using the [local CLI examples](../README.md#run-locally-with-docker-compose). Use `python -m app.cli` in the service shell or `uv run --locked python -m app.cli` on your computer. Use the service shell to seed the hosted bucket, or upload the included sample files from the web UI.

1. Check `https://<your-domain>/healthz` for HTTP 200.
2. Sign in over HTTPS and upload the synthetic PO, invoice and receipt. Wait for `ready` and download an original to check private storage.
3. Run an offline investigation, create a proposal and approve it from a different account.
4. Issue an MCP token from **MCP lab**, then run `uv run --locked python -m app.cli mcp-inspect --url https://<your-domain>/protocol/mcp`. Confirm the tool list and case scope, revoke the token, then confirm it is rejected.
5. Set up database and object backups and test restoring a case before using sensitive documents. Use `python -m app.cli prune-auth` periodically to remove expired sessions, grants and sign-in buckets.

To add Hugging Face later, follow the [optional setup](../README.md#optional-hugging-face-mode) and run the lab on synthetic data first. For rollback, redeploy the last known good image and confirm that its code is compatible with the database migration already applied. Preserve both Postgres and object storage when debugging a release.
