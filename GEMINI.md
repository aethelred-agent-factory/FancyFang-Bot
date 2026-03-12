# FancyFang-Bot: Mandates & Conventions

## Workflow Mandates
- **Finalization Protocol:** Every task or session of work MUST conclude with a descriptive, informative commit message and a push to the remote repository.
- **Commit Style:** Use conventional commit prefixes (e.g., `feat:`, `fix:`, `refactor:`, `test:`) and provide concise summaries of the changes.
- **Verification:** Ensure all tests pass and the application is in a stable state before the final push.

## Environment & Security
- **Sensitive Data:** Never commit `.env` files. Ensure they remain in `.gitignore`.
- **API Keys:** Use the keys defined in `.env` for all external service integrations (Phemex, DeepSeek, GitHub).
