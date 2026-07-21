# Auto-Restart and Scheduling — Design

> Documento produzido via sessão de brainstorming em 2026-07-21.
> Status: **Aprovado**.
> Escopo: quarta e última das 4 partes independentes da Fase 3 (ver `docs/design-melhorias.md`, seção 5). Console em tempo real, download de jars e backups já estão prontos.

---

## 1. Resumo de Entendimento

- **O quê:** uma thread supervisora única (daemon), iniciada explicitamente em `mcmanager run`, rodando a cada 30s: religa servidores que caíram sem parada intencional (respeitando um limite de tentativas), e dispara backup diário num horário configurável por servidor.
- **Por quê:** hoje, se um servidor Minecraft cai (crash, OOM, etc.), ele fica parado até alguém notar e religar manualmente. E não existe nenhuma forma de agendar backups automáticos.
- **Para quem:** mesmo público do resto do painel — qualquer usuário staff, configurando por servidor.
- **Escala:** mesma dos ciclos anteriores — 1 máquina, ~1–10 servidores.

### Não-objetivos
- Restart agendado separado (só backup agendado — YAGNI, já que backup não exige derrubar o servidor).
- Supervisão sem o painel rodando — limitação aceita e documentada no design geral da Fase 3 (`docs/design-melhorias.md`), não resolvida aqui.
- Notificações (email/webhook) quando um servidor é desligado por esgotar tentativas de religada — fora de escopo, o `auto_restart_enabled=False` resultante já é visível no admin.

### Suposições confirmadas
1. `desired_running` é um campo de **intenção** (setado pelas próprias views de start/stop), não um cache de status — diferente do campo `status` removido na Fase 2, que era justamente um cache que ficava desatualizado.
2. Limite de 3 tentativas consecutivas de religada malsucedidas antes de desligar `auto_restart_enabled` automaticamente.
3. Thread iniciada só em `mcmanager run` (`cli.py`), nunca em `ConsoleConfig.ready()` — evita rodar durante `migrate`/`shell`/`createsuperuser`/testes, o mesmo tipo de efeito colateral removido de `ready()` na Fase 2.
4. Horário de backup agendado é comparado em UTC (`datetime.now(timezone.utc)`), consistente com `TIME_ZONE = 'UTC'` já configurado no projeto.
5. Backup agendado reaproveita `services.backups.start_backup()` sem nenhuma lógica nova de backup — só o gatilho de horário.

---

## 2. Abordagem escolhida

Um único módulo `services/supervisor.py`, uma única thread, um único loop verificando auto-restart e backup agendado no mesmo tick de 30s — já era a decisão do design geral da Fase 3 ("thread supervisora única"), sem necessidade de múltiplas threads ou filas de tarefas.

---

## 3. Design

### Modelo de dados

```python
class Server(models.Model):
    # ... campos existentes ...
    auto_restart_enabled = models.BooleanField(default=False)
    desired_running = models.BooleanField(default=False)
    consecutive_restart_failures = models.IntegerField(default=0)
    scheduled_backup_time = models.TimeField(null=True, blank=True)
    last_scheduled_backup_date = models.DateField(null=True, blank=True)
```

Todos com defaults seguros (`False`/`0`/`None`) — sem backfill necessário na migration.

### A thread supervisora (`services/supervisor.py`)

```python
"""Background supervisor: restarts servers that crashed unexpectedly and
triggers scheduled daily backups. Runs as a single daemon thread started
explicitly by `mcmanager run` (never during migrate/shell/tests)."""
import threading
from datetime import datetime, timezone

from ..models import Server
from . import backups, process

TICK_SECONDS = 30
MAX_RESTART_ATTEMPTS = 3

_stop_event = threading.Event()


def start():
    thread = threading.Thread(target=_run_forever, daemon=True)
    thread.start()
    return thread


def _run_forever():
    while not _stop_event.is_set():
        _tick()
        _stop_event.wait(TICK_SECONDS)


def _tick():
    for server in Server.objects.all():
        _check_auto_restart(server)
        _check_scheduled_backup(server)


def _check_auto_restart(server):
    if not server.auto_restart_enabled or not server.desired_running:
        return
    if process.is_running(server):
        if server.consecutive_restart_failures:
            server.consecutive_restart_failures = 0
            server.save(update_fields=['consecutive_restart_failures'])
        return

    if server.consecutive_restart_failures >= MAX_RESTART_ATTEMPTS:
        server.auto_restart_enabled = False
        server.save(update_fields=['auto_restart_enabled'])
        return

    try:
        process.start(server)
    except process.AlreadyRunningError:
        if server.consecutive_restart_failures:
            server.consecutive_restart_failures = 0
            server.save(update_fields=['consecutive_restart_failures'])
        return
    except Exception:
        pass
    server.consecutive_restart_failures += 1
    server.save(update_fields=['consecutive_restart_failures'])


def _check_scheduled_backup(server):
    if server.scheduled_backup_time is None:
        return
    now = datetime.now(timezone.utc)
    if server.last_scheduled_backup_date == now.date():
        return
    if now.time() < server.scheduled_backup_time:
        return
    server.last_scheduled_backup_date = now.date()
    server.save(update_fields=['last_scheduled_backup_date'])
    backups.start_backup(server)
```

Pontos importantes:
- `Server.objects.all()` é reconsultado do zero a cada tick — reflete edições feitas pelo admin enquanto a thread roda.
- `process.AlreadyRunningError` (uma request concorrente já religou o servidor) é tratado como sucesso, não como falha — zera o contador em vez de incrementar.
- `_check_scheduled_backup` dispara no primeiro tick em que já passou do horário E ainda não rodou hoje — não precisa do horário exato, só de já ter passado dele.

### Integração

**`views.py`** — `start_server`/`stop_server`/`force_stop_server` mantêm `desired_running` em sincronia com a ação do usuário (setado `True` no start bem-sucedido, `False` no stop/force-stop bem-sucedido); `start_server` também zera `consecutive_restart_failures` (um restart manual é um recomeço limpo).

**`cli.py`** — no comando `run`, `supervisor.start()` é chamado explicitamente antes de `call_command("runserver", ...)`.

**`admin.py`** — `auto_restart_enabled`/`scheduled_backup_time` editáveis; `desired_running`/`consecutive_restart_failures`/`last_scheduled_backup_date` entram no `exclude` (estado interno, mesmo tratamento que `jar`/`server_properties` já recebem).

### Testes

`supervisor.py` testado chamando `_tick()` diretamente (não `_run_forever()`, que é um loop infinito), reaproveitando os fakes já existentes (`fake_java_binary`). Cobre: religa quando caído com `desired_running=True`; não religa se `desired_running=False`; não religa se `auto_restart_enabled=False`; zera contador quando volta a rodar; desiste após 3 tentativas e desliga `auto_restart_enabled`; dispara backup agendado uma vez por dia sem repetir; não dispara fora do horário. Testes de `views.py` confirmam `desired_running` é atualizado corretamente pelas 3 views.

---

## 4. Decision Log

| # | Decisão | Alternativas consideradas | Justificativa |
|---|---------|---------------------------|---------------|
| 1 | Campos `auto_restart_enabled` + `desired_running` separados | Só `auto_restart_enabled`, religando sempre que parado | Evita religar um servidor que o usuário parou de propósito |
| 2 | Só backup agendado, sem restart agendado | Horários separados pra backup e restart | YAGNI — backup não exige derrubar o servidor |
| 3 | Limite de 3 tentativas com backoff (desliga auto_restart) | Tentar pra sempre sem limite | Evita martelar restart infinito num servidor genuinamente quebrado |
| 4 | Thread iniciada só em `mcmanager run` | Em `ConsoleConfig.ready()` | Evita rodar durante migrate/shell/testes — mesmo problema que a Fase 2 já corrigiu uma vez |
| 5 | `AlreadyRunningError` tratado como sucesso na religada | Contar como falha igual qualquer outra exceção | Uma request concorrente já resolveu o problema; não é uma falha da supervisão |
| 6 | `last_scheduled_backup_date` para não repetir no mesmo dia | Comparar só a hora, sem controle de data | Evita disparar o mesmo backup diário várias vezes por causa do tick de 30s |
| 7 | Comparação de horário em UTC | Fuso horário local do servidor | Consistente com `TIME_ZONE = 'UTC'` já configurado no projeto inteiro |
