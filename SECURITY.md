# Security and deployment boundaries

## Report a vulnerability

Use the repository's **Security → Report a vulnerability** action if private vulnerability reporting is enabled. Otherwise contact a maintainer privately through the contact information on their GitHub profile. Avoid publishing credentials, real image data, or exploit details in a public issue. There is no guaranteed response SLA for this early research project.

## Supported version

The initial `0.1.x` source line is the supported development line. Deploy an explicitly reviewed commit and update the dependency locks deliberately. Public release checks do not certify a system secure for regulated data.

## Operator responsibilities

- Bind worker ports to loopback. Put the authenticated gateway behind HTTPS for remote use; do not expose worker APIs directly.
- Generate deployment credentials with the configuration tool; keep `deploy/local.env` and runtime files out of Git.
- Use an SSH tunnel for initial remote tests. A shared gateway login is not per-user authorization or a clinical audit system.
- Import checkpoints only from trusted sources. PyTorch checkpoint deserialization can execute code; treat downloaded or supplied model files as executable artifacts.
- Plan disk quotas, backups, and cleanup for large uploads, revisions, and model files. Limit access to the server filesystem.
- Do not send private images, masks, credentials, or logs to coding agents or external APIs without appropriate authorization.

Dependencies, browser libraries, CUDA, and the model framework form part of the deployment's security boundary. Model downloads use the pinned manifest and integrity checks; integrity alone does not establish trust in an arbitrary checkpoint.
