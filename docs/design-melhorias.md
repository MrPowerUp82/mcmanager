# Design de Melhorias — mcmanager

> Documento produzido via sessão de brainstorming em 2026-07-17.
> Status: **Aprovado** (todas as seções validadas pelo mantenedor).

---

## 1. Resumo de Entendimento

- **O quê:** evoluir o mcmanager (painel Django + CLI para gerenciar servidores Minecraft) de um beta Linux-only para um produto público maduro no PyPI.
- **Por quê:** o projeto é distribuído publicamente, mas tem falhas de segurança (views sem autenticação, CSRF desabilitado, injeção de comando), gerenciamento frágil de processos (PTY, `/tmp`, `pgrep`/`pkill`) e nenhum teste.
- **Para quem:** qualquer pessoa que instale via `pip install mcmanager` em VPS Linux **ou** máquina Windows.
- **Escala:** 1 máquina, ~1–10 servidores. SQLite suficiente.
- **Restrições:** mantenedor solo; soluções simples de manter (YAGNI); se o painel cair, os servidores Minecraft continuam rodando.

### Não-objetivos
- Arquitetura multi-nó / agentes distribuídos.
- SaaS multi-tenant.
- Postgres, Celery ou filas de tarefas.
- Gerenciar instalações de Java pelo painel (usuário instala; painel só detecta/valida).

### Suposições confirmadas
1. Java instalado pelo usuário; painel apenas detecta e valida.
2. Carga de admin (dezenas de req/min), sem metas agressivas de performance.
3. Servidores Minecraft independem do processo do painel.
4. Ok quebrar compatibilidade com instalações beta (migração simples aceitável).
5. Console em tempo real pode usar polling incremental (sem WebSocket/Channels).

---

## 2. Abordagem escolhida

**Opção A — Evolução incremental em 4 fases** sobre a base Django/SQLite/CLI existente. Alternativas descartadas: (B) painel + daemon supervisor separado — robustez extra não justifica dobrar a complexidade de instalação para 1–10 servidores; (C) reescrita FastAPI + SPA — meses sem valor, joga fora o admin funcional do Django.

---

## 3. Fase 1 — Segurança e fundação

### Estrutura alvo do app `console`

```
mcmanager/console/
├── models.py          # Só modelos e validação (signals removidos)
├── views.py           # Views finas: validam request, chamam services
├── services/
│   ├── provisioning.py   # Criar/excluir diretórios do servidor (ex-signals)
│   ├── process.py        # ServerProcessManager (Fase 2)
│   ├── properties.py     # Ler/escrever server.properties
│   ├── jars.py           # Download de jars (Fase 3)
│   └── backups.py        # Backups (Fase 3)
└── tests/             # Testes por módulo de serviço
```

### Mudanças

1. **Autenticação:** `@staff_member_required` em todas as views de controle (`start`, `stop`, `force_stop`, `view_logs`, `stats`, `home`).
2. **CSRF:** remover `@csrf_exempt` de `send_command`; frontend envia o token CSRF do template.
3. **Métodos HTTP:** ações mutantes (`start`, `stop`, `force_stop`, `send_command`) aceitam apenas `POST` (`@require_POST`).
4. **Injeção de comando:** eliminar `os.popen(f"pgrep -f {jar}")` e `pkill`. Identificação de processo por PID + verificação de cmdline via `psutil`, sem shell.
5. **Signals → serviços:** `post_save_server` e `pre_delete_server` viram funções explícitas em `provisioning.py`, chamadas pela view/admin. Elimina o `instance.save()` recursivo dentro do signal.
6. **Bug de choices congelados:** `get_jar_files()` hoje é avaliado na definição da classe (choices fixados na importação). Passa a ser choices dinâmicos no form/admin, não no model.
7. **Testes:** pytest + pytest-django cobrindo services e permissões (smoke tests 401/403).

---

## 4. Fase 2 — Process manager multiplataforma

Classe `ServerProcessManager` (`services/process.py`), única dona do ciclo de vida dos processos.

### Iniciar
- `subprocess.Popen([java_bin, "-Xms{m}M", "-Xmx{m}M", "-jar", jar, "nogui"], cwd=server_dir, stdout=log, stderr=STDOUT)`.
- Lista de argumentos (sem shell → sem injeção); `cwd=` elimina o `os.chdir` global.
- Linux: `start_new_session=True`; Windows: `CREATE_NEW_PROCESS_GROUP`. Minecraft sobrevive à queda do painel.

### Estado
- `~/.mcmanager/run/server_<id>.json` com `{pid, started_at, jar}` — substitui `/tmp`.
- `is_running()`: PID + confirmação de cmdline via `psutil` (evita falso positivo de PID reciclado).

### Comandos e parada — via RCON
- `stdin` do Popen não é confiável entre workers → comandos e stop gracioso usam **RCON** (protocolo nativo do Minecraft).
- Painel habilita RCON automaticamente no `server.properties`: porta local, senha gerada por servidor, bind `127.0.0.1`.
- Bônus: RCON retorna a resposta do comando (o PTY atual não retornava).
- `force_stop` = `psutil.Process(pid).terminate()` → `kill()` após timeout.

---

## 5. Fase 3 — Novas funcionalidades

### Console em tempo real
- Polling incremental: `logs/<id>?offset=N` retorna só linhas novas do `latest.log`; frontend faz poll a cada 2s.
- Sem Channels/WebSocket/Redis — suficiente para uso de admin.

### Download de jars (`services/jars.py`)
- Provedores: **Mojang** (version manifest, Vanilla) e **PaperMC** (API v2). APIs públicas, sem chave.
- Download em thread de background para `~/.mcmanager/jar/`, verificação de SHA, status no banco.
- Upload manual de `.jar` continua suportado.

### Backups (`services/backups.py`)
- Zip do diretório do servidor em `~/.mcmanager/backups/server_<id>/<timestamp>.zip`.
- Com servidor rodando: `save-off` + `save-all` via RCON antes de zipar, `save-on` depois (evita mundo corrompido).
- Restauração só com servidor parado + confirmação explícita. Retenção padrão: últimos 5.

### Auto-restart e agendamento
- Thread supervisora única (daemon) no processo do painel: a cada 30s religa servidores com `auto_restart=True` que morreram sem stop intencional.
- Agendamento (restart/backup diário): campos de horário no modelo `Server`, avaliados pela mesma thread. Sem Celery/cron.
- Limitação aceita: sem painel rodando, sem supervisão. Documentar systemd/serviço Windows como instalação recomendada.

---

## 6. Fase 4 — UX, erros e testes

### UX
- **Dashboard** na home: cards por servidor (status, CPU/RAM, jogadores online via RCON `list`, start/stop no card).
- Página do servidor: console incremental + input de comando; abas para `server.properties` (editor com validação básica), backups e configurações.
- Tailwind mantido; sem SPA.
- `mcmanager init` valida ambiente (Java presente? versão?); novo `mcmanager doctor` para diagnóstico.

### Erros e casos extremos
- Respostas JSON padronizadas `{status, message, data?}` com códigos HTTP corretos.
- Cobertos: Java ausente/errado; porta em uso (detectada antes do start); jar removido do disco (status "quebrado"); PID reciclado; crash do Minecraft (supervisor); reinício do painel (estado reconstruído de `run/*.json`).
- Logging estruturado do painel para suportar usuários do PyPI.

### Testes e CI
- Unitários: services (provisioning, properties, jars com APIs mockadas, backups).
- Integração: views com client do Django (permissões, métodos, contratos JSON).
- Process manager: processo fake (script echo) no lugar do Minecraft real.
- GitHub Actions: matriz **Linux + Windows**, lint com ruff.

---

## 7. Decision Log

| # | Decisão | Alternativas consideradas | Justificativa |
|---|---------|---------------------------|---------------|
| 1 | Evolução incremental (Opção A) | Daemon separado (B); reescrita FastAPI (C) | Menor risco, valor contínuo, adequado a mantenedor solo e escala de 1–10 servidores |
| 2 | Suporte Linux + Windows | Só Linux; Linux + Docker | Decisão do mantenedor; amplia o público do pacote PyPI |
| 3 | `subprocess.Popen` com lista de args + `cwd` | Manter `pty.fork` (Linux-only) | Multiplataforma, elimina injeção de shell e o `os.chdir` global |
| 4 | RCON para comandos e stop gracioso | stdin do Popen; PTY; screen/tmux | stdin não é confiável entre workers; RCON é nativo do Minecraft, multiplataforma e retorna respostas |
| 5 | Estado em `~/.mcmanager/run/*.json` | `/tmp` (atual) | `/tmp` é Linux-only, volátil e compartilhado entre usuários |
| 6 | Polling incremental para logs | WebSocket (Django Channels + Redis); SSE | Zero infra extra; suficiente para uso de admin; YAGNI |
| 7 | Provedores de jar: Mojang + PaperMC | Spigot (sem API de download direto); CurseForge | APIs públicas estáveis e sem chave; cobrem os casos dominantes |
| 8 | Backup com `save-off`/`save-all` via RCON | Só permitir backup com servidor parado | Padrão da comunidade; permite backup a quente sem corromper o mundo |
| 9 | Supervisor em daemon thread no painel | Celery beat; cron; daemon separado | Sem dependências novas; limitação (painel precisa estar de pé) aceita e documentada |
| 10 | Lógica de provisionamento em services, não em signals | Manter signals | Testabilidade, sem `save()` recursivo, fluxo explícito |
| 11 | pytest + matriz CI Linux/Windows | unittest puro; CI só Linux | Windows agora é plataforma suportada — precisa de verificação contínua |
| 12 | Choices de jar dinâmicos no form | Choices no model (atual, congelados na importação) | Corrige bug latente: jars adicionados após o boot não apareciam |

---

## 8. Ordem de implementação sugerida

1. **Fase 1** (segurança) — pré-requisito de tudo; produto público não pode esperar.
2. **Fase 2** (process manager) — desbloqueia Windows e RCON, base das features.
3. **Fase 3** (features) — console, jars, backups, supervisor (nesta ordem interna).
4. **Fase 4** (UX/polimento) — contínuo, mas consolidado ao final.

Cada fase deve terminar com testes verdes na matriz Linux + Windows antes da próxima.
