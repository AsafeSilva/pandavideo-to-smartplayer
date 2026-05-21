# Guia de Execução — Migração Panda Video → SmartPlayer

Execute os comandos **um por vez, em sequência**. Todos rodam no terminal com o venv ativado.

## Sequência

### 1. Testar credenciais (só uma vez antes de começar)

```powershell
python check_credentials.py
```

Confirma que a API Key do Panda e as credenciais OAuth2 do SmartPlayer estão funcionando.

---

### 2. Discovery — descobrir pastas e vídeos

```powershell
python -m src.migrate discover --prefix "EDUCACIONAL |"
```

Lista as pastas com prefixo "EDUCACIONAL |" no Panda e registra todos os vídeos em `data/manifest.json`. **Não baixa nada ainda.**

---

### 3. Dry-run — ver o plano antes de executar

```powershell
python -m src.migrate run --dry-run
```

Mostra quantos vídeos serão migrados e o tamanho total estimado em GB. **Não baixa nada ainda.**

---

### 4. Executar a migração

```powershell
python -m src.migrate run
```

Baixa os vídeos do Panda e faz upload para o SmartPlayer. Pode demorar algumas horas.

> **Se cair no meio** (queda de internet, fechar o PC): rode o mesmo comando de novo. O script continua de onde parou automaticamente.

---

### 5. Reprocessar falhas (se necessário)

```powershell
python -m src.migrate retry-failed
```

Reprocessa apenas os vídeos que falharam. Execute depois do passo 4 se aparecerem erros.

---

### 6. Gerar planilha de mapeamento

```powershell
python -m src.migrate export
```

Gera `data/migration_log.csv` e `data/migration_log.md` com todos os links:
- Título do vídeo
- ID e URL no Panda Video
- ID e embed URL no SmartPlayer

> O passo 4 já gera isso automaticamente ao terminar, mas pode rodar novamente a qualquer momento.

---

## Resultado final

| Arquivo | Descrição |
|---|---|
| `data/migration_log.csv` | Planilha para Excel/Sheets com todos os embed URLs |
| `data/migration_log.md` | Tabela agrupada por pasta para revisão humana |
| `data/manifest.json` | Estado completo da migração (não apagar) |
| `logs/migration_*.log` | Log detalhado em JSON-lines |
