# Security guidance

AtlasOps is a university and research project, not a commercial vulnerability-response
program.

- Never disclose credentials or live secrets in public issues, pull requests, logs, or
  demonstrations.
- Revoke and replace an accidentally exposed token immediately.
- Store future CI/CD secrets in GitHub Secrets or an approved environment secret store.
- Never commit kubeconfigs, GCP service-account material, model-provider keys, or other
  cluster/cloud credentials.
- Review generated logs, trajectories, postmortems, and benchmark outputs for sensitive
  values before publication.
- A public Hugging Face or other UI/deployment must never directly contain privileged
  cluster credentials.
- Report security-sensitive findings privately to project maintainers or team members
  first, without publishing usable credentials or exploit material.
