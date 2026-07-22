# Dashboard — Design

> Documento produzido via sessão de brainstorming em 2026-07-22.
> Status: **Aprovado**.
> Escopo: segunda das partes independentes da Fase 4 (ver `docs/design-melhorias.md`, seção 6). Triagem de vulnerabilidades já está pronta. `mcmanager doctor`, erros estruturados/casos extremos e CI Linux+Windows ainda não começaram.

---

## 1. Resumo de Entendimento

- **O quê:** substituir a home atual (dropdown de seleção de servidor) por um dashboard com um card por servidor, mostrando status (ON/OFF), CPU/RAM, jogadores online (via RCON `list`), e botões start/stop diretamente no card, atualizado por polling.
- **Por quê:** hoje, pra ver o estado de qualquer servidor é preciso escolher no dropdown e abrir o console individual — não há visão geral. Design original da Fase 4 já previa isso: "Dashboard na home: cards por servidor (status, CPU/RAM, jogadores online via RCON `list`, start/stop no card)".
- **Para quem:** mesmo público do resto do painel — usuário staff, mesma escala (1 máquina, ~1–10 servidores).
- **Escala:** N pequeno (1–10) — decisões de paralelismo/timeout abaixo são dimensionadas para essa escala, não para dezenas/centenas de servidores.

### Suposições confirmadas

1. Intervalo de polling do dashboard: **5 segundos** (metade da frequência do console individual, que já usa 2.5s para 1 servidor só — o dashboard agrega vários, então tem mais overhead por ciclo).
2. Falha de RCON/stats para 1 servidor durante a agregação é **isolada por servidor** — aquele card mostra "indisponível", os demais continuam normais. Mesmo padrão de isolamento já usado no supervisor (`_tick()` da Fase 3, parte 4).
3. Start/Stop no card reaproveitam os endpoints já existentes (`start_server`/`stop_server`), e o card se atualiza via fetch imediato após o clique — sem reload de página.
4. Cada card linka para o console completo daquele servidor (`/console/<id>/`), preservando acesso a logs/comandos/backups que já existem lá.
5. Servidor parado não dispara nenhuma chamada de CPU/RCON para ele — só mostra badge OFF + botão Start.

---

## 2. Abordagem escolhida

Um endpoint agregado novo (`services/dashboard.py` + view JSON) que coleta os dados de todos os servidores numa única requisição, em vez de cada card fazer sua própria chamada aos endpoints existentes (`get_server_stats` por card + uma chamada RCON `list` nova por card). A abordagem agregada evita multiplicar N requisições HTTP simultâneas a cada ciclo de polling, e permite paralelizar a coleta no backend (ver Design abaixo) em vez de bloquear sequencialmente por servidor.

---

## 3. Design

### `services/dashboard.py` (novo módulo)

```python
"""Aggregates per-server status/stats/player-count for the dashboard view.
Each running server's data is collected in its own thread so one slow or
unreachable server doesn't add its latency to every other server's; a
failure for one server never prevents the others from reporting."""
from concurrent.futures import ThreadPoolExecutor

from . import process, rcon

STATS_CPU_INTERVAL = 0.2


def get_dashboard_data():
    servers = list(Server.objects.all())
    running = {s.id: process.is_running(s) for s in servers}
    running_servers = [s for s in servers if running[s.id]]

    with ThreadPoolExecutor(max_workers=max(len(running_servers), 1)) as pool:
        details = dict(zip(
            (s.id for s in running_servers),
            pool.map(_collect_running_server_data, running_servers),
        ))

    return [
        {
            'server': s,
            'running': running[s.id],
            **(details.get(s.id) or {}),
        }
        for s in servers
    ]


def _collect_running_server_data(server):
    result = {'stats_available': False, 'players_available': False}
    try:
        result.update(_collect_stats(server))
        result['stats_available'] = True
    except Exception:
        pass
    try:
        result.update(_collect_players(server))
        result['players_available'] = True
    except Exception:
        pass
    return result


def _collect_stats(server):
    stats = process.get_stats(server, cpu_interval=STATS_CPU_INTERVAL)
    return {
        'cpu_usage': stats['cpu_usage'],
        'memory_usage': stats['memory_usage'],
    }


def _collect_players(server):
    response = process.send_command(server, 'list')
    return {'players_raw': response}
```

Pontos importantes:
- `Server.objects.all()` é reconsultado a cada chamada — reflete o estado atual, mesmo padrão do supervisor.
- Servidores parados nunca entram no `ThreadPoolExecutor` — zero overhead de CPU/RCON para eles.
- Cada servidor rodando tem sua coleta isolada em `try/except Exception: pass` — uma falha de RCON ou uma corrida rara do `psutil` (processo morreu entre o `is_running()` e a leitura de stats) marca só aquele campo como indisponível (`stats_available`/`players_available` = `False`), sem afetar os outros servidores nem derrubar a requisição inteira.
- `process.get_stats()` ganha um parâmetro novo `cpu_interval` (default mantém o comportamento atual `1.0` para o endpoint existente `get_server_stats`/console individual; o dashboard passa `0.2` explicitamente). Isso é uma mudança de assinatura aditiva e compatível — nenhum chamador existente precisa mudar.
- Parsing do `players_raw` (string RCON tipo `"There are 2 of a max of 20 players online: Alex, Steve"`) fica no template/JS, não no service — o service só repassa o texto bruto do RCON, sem acoplar o service a um formato específico de exibição.

### View e URL

**`views.py`** — a view `home` atual é substituída:

```python
@staff_member_required
def home(request: HttpRequest):
    return render(request, 'index.html', {'servers': dashboard.get_dashboard_data()})


@staff_member_required
def dashboard_data(request: HttpRequest):
    return JsonResponse({'status': 'success', 'servers': [
        {
            'id': entry['server'].id,
            'name': entry['server'].name,
            'running': entry['running'],
            'stats_available': entry.get('stats_available', False),
            'cpu_usage': entry.get('cpu_usage'),
            'memory_usage': entry.get('memory_usage'),
            'players_available': entry.get('players_available', False),
            'players_raw': entry.get('players_raw'),
        }
        for entry in dashboard.get_dashboard_data()
    ]})
```

**`urls.py`** — nova rota `path('dashboard/data/', views.dashboard_data, name='dashboard_data')`, mantendo `home` na raiz.

O antigo dropdown/formulário POST em `home` é removido — a navegação para o console de um servidor passa a ser só o link "Abrir console" em cada card.

### Frontend (`index.html`)

- Um card por servidor: nome, badge ON/OFF, CPU/RAM (só se `stats_available`), jogadores (`X/Y` parseado de `players_raw`, ou "indisponível" se `players_available` for falso), botões Start/Stop (reaproveitando os fetches já usados no console individual, com `X-CSRFToken`), link "Abrir console" para `/console/<id>/`.
- `setInterval(fetchDashboardData, 5000)` chamando `dashboard/data/` e atualizando os cards no DOM (sem reload).
- Clique em Start/Stop dispara o POST existente e, no `.then()`, chama `fetchDashboardData()` imediatamente (sem esperar os 5s do próximo ciclo).
- Servidor OFF: card mostra só badge OFF + botão Start (sem tentar renderizar CPU/RAM/jogadores).

### Testes

`services/dashboard.py` testado chamando `get_dashboard_data()` diretamente com fakes de `process.is_running`/`process.get_stats`/`process.send_command` (via `unittest.mock.patch`, mesmo padrão usado nos testes do supervisor): servidor rodando com stats e RCON ok; servidor rodando com RCON falhando (levanta exceção) enquanto outro servidor na mesma chamada continua ok; servidor rodando com `get_stats` falhando; servidor parado (nunca entra no pool, campos de stats ausentes). View `dashboard_data` testada com o client do Django (permissão staff, contrato JSON). View `home` testada renderizando com a lista de servidores mockada.

---

## 4. Decision Log

| # | Decisão | Alternativas consideradas | Justificativa |
|---|---------|---------------------------|---------------|
| 1 | Endpoint agregado (1 requisição, N coletas em paralelo no backend) | Cada card chama os endpoints existentes individualmente | Evita multiplicar requisições HTTP simultâneas a cada ciclo; paralelismo no backend limita o tempo total ao servidor mais lento, não à soma |
| 2 | `ThreadPoolExecutor` para paralelizar a coleta por servidor | Coleta sequencial (soma os tempos de CPU+RCON de cada servidor) | Para ~10 servidores, sequencial multiplicaria a latência total; paralelo mantém o tempo de resposta limitado |
| 3 | Intervalo de polling do dashboard: 5s | 2.5s (igual ao console); 10s | Meio-termo aprovado pelo usuário — visão geral não precisa da mesma responsividade que o console de 1 servidor |
| 4 | Isolamento por servidor em caso de falha (try/except por coleta) | Falhar a requisição inteira se qualquer servidor falhar | Aprovado pelo usuário — um servidor com RCON travado não deve tirar visibilidade dos outros, mesmo princípio do supervisor da Fase 3 |
| 5 | `cpu_interval` parametrizável em `process.get_stats()` (default 1.0, dashboard usa 0.2) | Criar uma função de stats totalmente separada para o dashboard | Mudança aditiva e compatível; evita duplicar a lógica de leitura de CPU/RAM já existente |
| 6 | Parsing do texto RCON `list` fica no frontend, não no service | Parsear no backend e retornar `{count, max, names}` estruturado | Fora de escopo do dashboard aprofundar acoplamento ao formato exato da resposta do Vanilla/Paper; texto bruto é suficiente e mais simples agora (YAGNI) |
