# Erros Estruturados & Casos Extremos — Design

> Documento produzido via sessão de brainstorming em 2026-07-22.
> Status: **Aprovado**.
> Escopo: quarta das partes independentes da Fase 4 (ver `docs/design-melhorias.md`, seção 6). Triagem de vulnerabilidades, Dashboard e `mcmanager doctor` já estão prontos. CI Linux+Windows ainda não começou.

---

## 1. Resumo de Entendimento

- **O quê:** códigos HTTP corretos em todas as respostas JSON de erro do painel (hoje sempre 200, mesmo em erro), checagem de porta em uso antes de iniciar um servidor, e detecção de arquivo `.jar` removido do disco com status "quebrado" visível no dashboard.
- **Por quê:** o design original da Fase 4 previa "respostas JSON padronizadas com códigos HTTP corretos" e uma lista de casos extremos. Uma investigação prévia (via subagente Explore) mostrou que a maioria desses casos extremos já está coberta por trabalho de fases anteriores (PID reciclado, crash do Minecraft via supervisor, reconstrução de estado no restart do painel) — restam 3 lacunas reais, que são o escopo desta rodada.
- **Para quem:** mesmo público do resto do painel — usuário staff.
- **Escala:** mudança cirúrgica em ~30 pontos de resposta JSON já existentes + 2 checagens novas em `process.start()`.

### Descoberta que reduziu o escopo (via investigação prévia)

Destes 7 itens do design original, 3 já estavam **totalmente implementados** antes desta rodada:
1. **PID reciclado** — `process.is_running()` já valida que o cmdline do processo vivo contém o nome do jar rastreado, não só que o PID existe (`services/process.py`).
2. **Crash do Minecraft** — já tratado pelo supervisor da Fase 3, parte 4 (`services/supervisor.py`), com limite de tentativas.
3. **Reinício do painel** — já funciona sem código especial, porque todo estado é lido do disco (`run/*.json`) a cada chamada, nunca mantido em memória entre requests.

A checagem de versão errada do Java no momento do start foi **descartada** desta rodada por decisão do usuário — já existe uma checagem de versão separada via `mcmanager doctor`, e duplicar a lógica no fluxo de start seria redundante.

### Suposições confirmadas

1. **Não** vamos reestruturar o formato das respostas de sucesso (`{status, message, data}` aninhado) — mantém o formato achatado atual, que já funciona e é consumido por JS em vários templates. Só as respostas de **erro** ganham `status=` correto no `JsonResponse`; `message` já está presente em praticamente todas.
2. Checagem de porta usa `socket.bind()` da stdlib (multiplataforma, sem dependência nova) — tenta abrir e fechar um socket na porta configurada do servidor antes de lançar o Java.
3. Checagem de jar usa o caminho já conhecido pelo provisionamento: `SERVERS_DIR/server_{id}/{server.jar}` (confirmado em `services/provisioning.py`, que copia o jar para exatamente esse caminho na criação do servidor).
4. O status "quebrado" (jar ausente) é surfaced tanto como erro no `start_server` (409) quanto visualmente no dashboard — um servidor quebrado não deveria só falhar silenciosamente ao tentar iniciar, deve aparecer diferenciado antes mesmo do usuário tentar.

---

## 2. Abordagem escolhida

Um pequeno helper de resposta de erro (`json_error(message, status=400)`) centralizado, usado nos 3 arquivos de views existentes (`views.py`, `views_jars.py`, `views_backups.py`), mapeando cada tipo de exceção já existente (ou nova) para o código HTTP correto — sem introduzir uma reestruturação de payload nem uma abstração maior (ex.: um decorator de tratamento de exceções genérico), YAGNI para o tamanho atual do projeto.

---

## 3. Design

### Mapeamento de códigos HTTP

| View | Condição | Código |
|------|----------|--------|
| `start_server` | `AlreadyRunningError` | 409 |
| `start_server` | `JavaNotFoundError` | 503 |
| `start_server` | `PortInUseError` (novo) | 409 |
| `start_server` | `JarMissingError` (novo) | 409 |
| `stop_server` | `ProcessNotRunningError` | 409 |
| `stop_server` | `StopTimeoutError` | 504 |
| `stop_server` | `RconError` | 502 |
| `view_logs` | log file não encontrado | 404 |
| `send_command` | comando faltando | 400 |
| `send_command` | `ProcessNotRunningError` | 409 |
| `send_command` | `RconError` | 502 |
| `get_server_stats` | `ProcessNotRunningError` | 409 |
| `list_jar_versions` | provider desconhecido | 400 |
| `list_jar_versions` | falha do provedor externo | 502 |
| `start_jar_download` | provider/versão inválidos | 400 |
| `jar_download_status` | download não encontrado | 404 |
| `backup_status_view` | backup não encontrado | 404 |
| `restore_backup_view` | filename faltando | 400 |
| `restore_backup_view` | falha no restore | 409 |
| `delete_backup_view` | filename faltando | 400 |
| `delete_backup_view` | falha na exclusão | 409 |

`force_stop_server` e `list_backups_view` não têm branch de erro hoje — permanecem sem mudança.

### Novo helper

```python
def json_error(message, status=400):
    return JsonResponse({'status': 'error', 'message': message}, status=status)
```

Usado em substituição direta de cada `JsonResponse({'status': 'error', 'message': ...})` existente, com o `status=` correto da tabela acima. Respostas de sucesso (`{'status': 'success', ...}`) não mudam.

### Porta em uso (`services/process.py`)

```python
import socket

class PortInUseError(Exception):
    """Raised by start() when the server's configured port is already bound."""

def _check_port_available(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(('0.0.0.0', port))
        except OSError as exc:
            raise PortInUseError(f'Port {port} is already in use') from exc
```

Chamada no início de `start()`, antes do `subprocess.Popen`.

### Jar ausente (`services/process.py` + `services/dashboard.py`)

```python
class JarMissingError(Exception):
    """Raised by start() when the server's configured jar file is missing from disk."""

def _jar_path(server):
    return settings.SERVERS_DIR / f'server_{server.id}' / server.jar

def is_jar_missing(server):
    return not _jar_path(server).exists()
```

`start()` chama `is_jar_missing(server)` antes do `subprocess.Popen` e levanta `JarMissingError` se ausente. `services/dashboard.py`'s `get_dashboard_data()` ganha um campo `jar_missing` por servidor (calculado sempre, independente de `running`, já que um servidor quebrado é sempre quebrado esteja rodando ou não — mas na prática `is_running()` já seria `False` se o jar sumiu). O template do dashboard mostra um badge "Quebrado" (cor diferente de ON/OFF) quando `jar_missing` é verdadeiro, no lugar do botão Start.

### Testes

`process.py`: testes isolados para `_check_port_available`/`PortInUseError` (porta já ocupada por um socket de teste) e `is_jar_missing`/`JarMissingError` (arquivo removido do `tmp_path` antes do `start()`). Views: cada cenário de erro testado com o client do Django, verificando `response.status_code` correto (reaproveitando os fixtures de servidor provisionado já existentes em `test_process.py`/`test_views_desired_running.py`). `dashboard.py`: teste isolado para `jar_missing=True` quando o arquivo não existe, sem afetar os campos de stats/players já testados.

---

## 4. Decision Log

| # | Decisão | Alternativas consideradas | Justificativa |
|---|---------|---------------------------|---------------|
| 1 | Manter formato achatado de sucesso, só corrigir `status=` de erro | Reestruturar tudo sob `data` | Aprovado pelo usuário — menor risco, não exige tocar no JS de ~4 templates que já funciona |
| 2 | Excluir PID reciclado, crash do Minecraft, reinício do painel do escopo | Reimplementar/reforçar mesmo já funcionando | Investigação confirmou que os 3 já estão totalmente cobertos por trabalho de fases anteriores |
| 3 | Excluir checagem de versão do Java no start | Duplicar a lógica do `doctor` no fluxo de start | Aprovado pelo usuário — já existe uma checagem separada, redundância desnecessária |
| 4 | `socket.bind()` da stdlib pra checar porta | `psutil.net_connections()` (já uma dependência do projeto) | `socket.bind()` é mais direto pro caso de uso (só precisamos saber se dá pra abrir a porta, não listar todas as conexões do sistema) |
| 5 | Jar ausente vira erro no start (409) E status visual no dashboard | Só erro no start, sem indicação visual prévia | Aprovado pelo usuário — um servidor quebrado deve ser visível antes do usuário tentar iniciar, não só falhar silenciosamente |
| 6 | Helper `json_error()` simples, sem decorator/middleware genérico de tratamento de exceções | Decorator que mapeia exceções pra códigos automaticamente | YAGNI para o tamanho atual do projeto — 30 call sites não justificam uma abstração de framework própria |
