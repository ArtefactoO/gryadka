# Deployment

Build the combined API/static frontend image:

```sh
docker build -f deploy/Dockerfile -t gryadka:local .
```

Run the API, Celery worker and Celery Beat with the same image, YAML configuration and environment credentials. The included Compose file demonstrates these commands against local Redis. A shared deployment supplies durable Redis configuration, its own network boundaries, TLS and identity provider.

Runtime uses UID 1000. Keep the root filesystem read-only and provide writable `/tmp` for worker/scheduler state. Mount non-secret configuration read-only. Inject credentials through environment variables from your secret manager.

The GitHub workflow runs frontend/backend checks and image builds only when **Actions → CI → Run workflow** is started manually. Gitleaks remains automatic on pull requests and pushes to `main`. The workflow does not publish a registry image or deploy an environment. Helm charts and Argo CD Applications are deployment-specific and are not included here.
