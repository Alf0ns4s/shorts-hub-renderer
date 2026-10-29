# Shorts Hub production worker

This repository contains a generic production launcher, not the private Shorts Hub application or its content.

## Public contents

- `.github/workflows/render.yml`: standard Ubuntu job, owner/default-branch guard, five daily runs plus manual run.
- `worker.py`: private checkout, credential validation, private subprocess logs, ordinary production commands, private state save.
- `test_worker.py`: offline checks with fake credentials and a simulated production pass.
- `.gitignore` and this README.

No production scripts, research, analytics, videos, drawings, credentials, repository history, or output artifacts are copied into this public repository. The workflow retrieves the trusted private main branch at runtime. Its console only reports generic status. It does not run on pull requests and does not publish artifacts or cache private data.

## Configuration needed before production

Create `Alf0ns4s/shorts-hub-renderer` with a new, empty history. Keep `Alf0ns4s/shorts-hub` private. Configure these repository Actions secrets using GitHub's encrypted-secret interface; never paste values into an issue, chat, workflow file, or commit:

- `SOURCE_TOKEN`: a credential permitted to read and write **only the private production repository's contents**. It retrieves production code and saves workflow-owned results there. The public repository's automatic `GITHUB_TOKEN` cannot access a different private repository.
- `YT_SECRETS`: base64 ZIP containing the existing YouTube credential JSON files at archive root, in the same format as the original private workflow. No new YouTube login is required if those credentials remain valid.
- `GROQ_API_KEY`: the existing free Groq writer key. Required for daily cloud-only writing; without it this worker can only process scripts already in the inbox.
- `PAGES_TOKEN`: optional existing dashboard deployment credential. Without it, production state is saved privately but the public dashboard is not refreshed by this worker.

GitHub will not reveal the old secrets' stored values. Source credentials must be transferred through a supported credential setup flow or existing local credential backup, never guessed or printed. Existing local YouTube JSON backups were located, but this package contains none of them.

Before enabling the new schedule, disable the old private production/writer workflows so they cannot race the public worker. Transfer other scheduled work separately; this package does not migrate the independent analytics/learning workflows. Keep paid usage budgets at zero; no billing settings are changed by this package.

## Processing

The launcher writes missing scripts when Groq is configured, validates channel login availability, downloads the existing voice model, renders authored drawings, and runs the unchanged `hub.py cloud` pipeline. That pipeline retains its existing video QA and scheduling behavior; this worker does not lower its gate. Production state is committed only to the private repository. Public job success does not, by itself, prove any particular video was uploaded: check the private queue and YouTube scheduling results.

Standard public-repository hosted runner compute is free under GitHub's documented rules. Storage and other products have their own rules; this worker avoids public output artifacts and caches. An account-wide billing restriction may still prevent a runner from starting. Only a real test can establish whether this account can start the public worker.

## Validation / rollout

1. Run `python -m unittest -v test_worker` offline.
2. Review the five public files and the new access boundary before publication or adding credentials.
3. Configure the repository and secrets, disable competing private writers/renderers, then manually run the worker.
4. Confirm runner startup, Deep Sea render QA, saved private state, and the actual YouTube publication time. Do not call the migration complete before those checks.

Known limits: no production test has run yet; private logs are intentionally not exported to the public job. Failed state pushes require investigation before repeating an upload. Review failed private queue/QA records; do not assume a green workflow means audience engagement or guaranteed publication.
