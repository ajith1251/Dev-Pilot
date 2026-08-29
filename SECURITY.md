# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability within DevPilot, please send an email to the maintainers. All security vulnerabilities will be promptly addressed.

**Please do NOT report security vulnerabilities through public GitHub issues.**

## Security Measures

### LLM Security

- **Prompt Injection Protection**: All user-provided content is marked with trust boundaries (`UNTRUSTED_REPOSITORY_CONTENT`, `UNTRUSTED_TEST_OUTPUT`)
- **No Direct File Access**: Agents have reasoning authority only; no agent directly writes files or executes processes
- **Deterministic Gates**: All mutations pass through deterministic security gates (PatchValidator, ExecutionPolicy, RepairPolicy)

### API Security

- **CORS Configuration**: Configurable allowed origins via `DEVPILOT_CORS_ORIGINS`
- **Request Size Limits**: Maximum request body size enforced via `DEVPILOT_MAX_REQUEST_BODY_BYTES`
- **Credential Redaction**: API keys and passwords are never exposed in logs, APIs, or CLI output

### Database Security

- **Environment-Based Configuration**: All database credentials stored in environment variables, never in code
- **Secret Redaction**: Passwords are automatically redacted in all output
- **Connection Pooling**: Secure connection management with pre-ping health checks

### Code Execution Security

- **Execution Policy**: Whitelist-based executable allowlist
- **Environment Sanitization**: Controlled environment variables for child processes
- **Timeout Enforcement**: Strict timeouts on all code execution
- **Path Validation**: Traversal protection and allowed root enforcement

### Patch Security

- **Hash Verification**: Content hashes verify file integrity before and after patches
- **Size Limits**: Configurable maximum file and patch sizes
- **Protected Files**: Critical files cannot be modified without explicit configuration
- **Atomic Writes**: Patch application uses atomic file operations with rollback support

### Provider Security

- **Key Isolation**: API keys stored in environment variables, never committed to repository
- **Secure Defaults**: Conservative timeout and retry settings
- **Circuit Breakers**: Prevent cascade failures and abuse
- **Health Monitoring**: Automatic detection of provider misconfigurations

## Configuration Security

### Environment Variables

All sensitive configuration is done through environment variables:

```bash
# Database
DATABASE_URL=postgresql+asyncpg://user:password@host:5432/db

# LLM Providers
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
GEMINI_API_KEY=...
NVIDIA_API_KEY=nvapi-...

# GitHub
GITHUB_TOKEN=ghp_...
```

### `.env` File

The `.env` file is gitignored and should never be committed:

```gitignore
# .gitignore
.env
backend/.env
```

### CI/CD Secrets

Sensitive values should be stored as GitHub repository secrets:

- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`
- `GEMINI_API_KEY`
- `DATABASE_URL`

## Best Practices

### For Users

1. **Never commit API keys** to version control
2. **Use environment variables** for all sensitive configuration
3. **Rotate API keys** regularly
4. **Monitor provider usage** for unexpected activity
5. **Use the principle of least privilege** for database credentials

### For Contributors

1. **Never hardcode secrets** in source code
2. **Use the redaction utilities** when logging sensitive data
3. **Validate all inputs** before processing
4. **Follow secure coding practices** for Python and TypeScript
5. **Report security issues** privately before public disclosure

## Security Updates

Security updates will be released as patch versions (e.g., 0.1.1, 0.1.2) and will be documented in the CHANGELOG.md.

## Acknowledgments

We thank all security researchers who responsibly disclose vulnerabilities to us.
