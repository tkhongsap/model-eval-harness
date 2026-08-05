# Cloud Build — Common Utilities

Shared Cloud Build workflows used across projects.

## 📁 Files

| File            | Purpose                      |
| --------------- | ---------------------------- |
| `tf_unlock.yml` | Force-unlock Terraform state |

---

## 🔓 Terraform State Unlock (`tf_unlock.yml`)

Use this workflow when a Terraform deployment fails mid-apply and leaves the state file locked. It performs:

1. **Validate** — Checks all required substitution variables
2. **Terraform Init** — Initializes backend with the locked state bucket
3. **Force-Unlock** — Releases the state lock using the lock ID
4. **Verify** — Confirms state is accessible again

### Usage

```bash
gcloud builds submit --no-source \
  --config=cloud_build/common/tf_unlock.yml \
  --region=<REGION> \
  --substitutions=\
    _LOCK_ID=<LOCK_ID>,\
    _TERRAFORM_PROJECT_PATH=terraform/projects/<project>,\
    _STATE_BUCKET=<PROJECT_ID>-terraform-state,\
    _STATE_PREFIX=terraform-state/<env>-<project>
```

### Required Parameters

| Variable                  | Description                    | Example                                   |
| ------------------------- | ------------------------------ | ----------------------------------------- |
| `_LOCK_ID`                | Lock ID from the error message | `1771558201700864`                        |
| `_TERRAFORM_PROJECT_PATH` | Path to the Terraform project  | `terraform/projects/sentiment_telesale`   |
| `_STATE_BUCKET`           | GCS bucket for Terraform state | `my-project-terraform-state`              |
| `_STATE_PREFIX`           | State prefix in the bucket     | `terraform-state/nprd-sentiment-telesale` |

### Optional Parameters

| Variable             | Default  | Description              |
| -------------------- | -------- | ------------------------ |
| `_TERRAFORM_VERSION` | `1.13.3` | Terraform version to use |

> [!CAUTION]
> Only run this if you are **certain** no other Terraform operation is currently in progress. Force-unlocking while another apply is running can corrupt state.
