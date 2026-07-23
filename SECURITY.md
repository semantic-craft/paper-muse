# Security Policy

## Reporting a vulnerability

Report suspected vulnerabilities through [GitHub's private security advisory form](https://github.com/semantic-craft/paper-muse/security/advisories/new).

Do not open a public issue for a vulnerability. Do not include API keys, tokens, cookies, unpublished papers, private datasets, complete output archives, raw logs, machine names, usernames, or absolute local paths in any public report.

If the private form is unavailable, contact the repository owner through their GitHub profile to arrange a private channel before sharing technical details.

## Protecting credentials and research material

- Keep credentials in environment variables, the operating-system keychain, or an ignored local `secrets.toml`; never commit real values.
- Use synthetic topics and minimal, redacted excerpts in bug reports and tests.
- Before committing, review the staged diff and run the repository's secret scan.
- If a credential is exposed, revoke or rotate it immediately before removing it from the repository.
