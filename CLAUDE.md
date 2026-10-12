# bot-interpelli



## Alx Studio

Progetto gestito dagli agenti di Alx Studio: regole complete in `~/AlxStudio/CLAUDE.md` e
`~/AlxStudio/.claude/rules/`. Scheda: `~/AlxStudio/knowledge/bot-interpelli.md`.

- Branch: `main` = produzione (solo via PR di release approvata dal CEO), `develop` = integrazione,
  lavoro su `feat/<ticket>-<nome>` o `fix/<ticket>-<nome>` in un worktree (`wt bot-interpelli <ticket> <slug>`).
- Conventional Commits. Ogni PR verso `develop` chiude una issue (`Closes #n`).
- La PR non si unisce con la CI rossa (job obbligatori: test e build). Lint, typecheck e analyze
  sono informativi finché le issue di debito non sono chiuse.
- Nessun segreto nel repo: `.env` è locale, le variabili sono in `.env.example`.
- Stack: python. Spec in `docs/spec/`, ADR in `docs/adr/`.

## Agent skills

### Issue tracker

Le issue vivono in GitHub Issues di alexsiroli/bot-interpelli (CLI `gh`). See `docs/agents/issue-tracker.md`.

### Triage labels

Etichette predefinite: needs-triage, needs-info, ready-for-agent, ready-for-human, wontfix. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: un `CONTEXT.md` e `docs/adr/` alla radice. See `docs/agents/domain.md`.
