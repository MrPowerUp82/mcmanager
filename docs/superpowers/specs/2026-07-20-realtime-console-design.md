# Console em Tempo Real — Design

> Documento produzido via sessão de brainstorming em 2026-07-20.
> Status: **Aprovado**.
> Escopo: primeira das 4 partes independentes da Fase 3 (ver `docs/design-melhorias.md`, seção 5). As outras três (download de jars, backups, auto-restart/agendamento) são ciclos separados, não cobertos aqui.

---

## 1. Resumo de Entendimento

- **O quê:** o endpoint `view_logs` (mesma URL, `/console/view_logs/<id>`) passa a suportar leitura incremental via `?offset=N` (bytes), retornando só o conteúdo novo do `logs/latest.log` em vez do arquivo inteiro a cada poll.
- **Por quê:** hoje o painel reenvia o arquivo inteiro a cada 2.5s de poll — funciona, mas desperdiça banda e cresce sem limite conforme o log do servidor cresce. Melhoria incremental sobre um comportamento já existente e funcional.
- **Para quem:** qualquer usuário do painel mcmanager olhando o console de um servidor específico.
- **Escala:** mesma da Fase 1/2 — 1 máquina, ~1–10 servidores, uso de admin (não precisa suportar centenas de viewers simultâneos).

### Não-objetivos
- Paginação/limite no carregamento inicial (offset=0 continua devolvendo o arquivo inteiro — YAGNI na escala atual).
- WebSocket/Server-Sent Events — polling incremental já decidido no design geral da Fase 3.
- Alinhamento de fronteira de caractere UTF-8 ao cortar por byte — aceito um glitch cosmético raríssimo e autocorrigível.

### Suposições confirmadas
1. Offset é em **bytes**, não linhas — mais simples e robusto a escrita parcial de linha.
2. Se `offset` pedido for maior que o tamanho atual do arquivo (log truncado/recriado por um restart do Minecraft), o servidor reinicia a leitura do zero automaticamente.
3. Mesma URL/nome `view_logs`, contrato de resposta muda (ganha campo `offset`; `offset` ausente na query = `0`).
4. Sem limite no carregamento inicial.
5. Frontend faz auto-scroll simples (sempre rola pro final a cada conteúdo novo).
6. Corte de caractere UTF-8 multi-byte no meio: decodifica com `errors='replace'`, sem lógica de alinhamento.

---

## 2. Abordagem escolhida

Endpoint existente (`view_logs`) evolui em vez de ganhar um novo endpoint paralelo — menos superfície, um único jeito de pegar logs. Único ponto de design genuinamente em aberto era o tratamento do corte de byte em fronteira UTF-8; optou-se por `errors='replace'` sem alinhamento (Opção A) em vez de alinhar a leitura ao byte inicial de uma sequência UTF-8 válida (Opção B) — o impacto de um glitch visual raríssimo e autocorrigível numa tela de debug de log não justifica a complexidade extra.

---

## 3. Design

### Backend — `mcmanager/console/views.py`

```python
@staff_member_required
def view_logs(request, id):
    server = Server.objects.get(id=id)
    log_path = settings.SERVERS_DIR / f'server_{server.id}' / LOG_FILE
    try:
        offset = int(request.GET.get('offset', 0))
    except ValueError:
        offset = 0
    if offset < 0:
        offset = 0

    if not log_path.exists():
        return JsonResponse({'status': 'error', 'message': 'Log file not found'})

    file_size = log_path.stat().st_size
    if offset > file_size:
        offset = 0  # log foi truncado/recriado (restart do Minecraft) -- recomeça do zero

    with log_path.open('rb') as f:
        f.seek(offset)
        new_bytes = f.read()

    return JsonResponse({
        'status': 'success',
        'logs': new_bytes.decode('utf-8', errors='replace'),
        'offset': offset + len(new_bytes),
    })
```

Continua `@staff_member_required`, sem `@require_POST` (GET, leitura, sem mudança de estado). `offset` inválido (não numérico ou negativo) cai para `0`.

### Frontend — `mcmanager/console/templates/console/index.html`

```javascript
let logOffset = 0;

function viewLogs() {
    fetch(`{% url 'view_logs' id=server.id %}?offset=${logOffset}`)
        .then(response => response.json())
        .then(data => {
            if (data.status === 'success') {
                const logsEl = document.getElementById('logs');
                if (data.offset < logOffset) {
                    logsEl.textContent = '';
                }
                logsEl.textContent += data.logs;
                logOffset = data.offset;
                logsEl.scrollTop = logsEl.scrollHeight;
            } else {
                create_toast(data.message, 'red', 'white');
            }
        });
}
```

`logOffset` é uma variável JS de página, reseta naturalmente a cada carregamento (nova página = `offset=0` = arquivo inteiro, comportamento correto). Detecção de truncamento no frontend: se o `offset` retornado for **menor** que o offset guardado, o backend reiniciou a leitura — limpa a `<div>` antes de anexar, evitando conteúdo antigo+novo desalinhado. `setInterval(viewLogs, 2500)` já existe (Fase 1) e não muda.

### Testes

- View: offset ausente devolve arquivo inteiro com `offset` correto; offset no meio do arquivo devolve só o restante; offset maior que o tamanho do arquivo reinicia do zero; arquivo inexistente continua retornando erro; offset inválido (string não numérica, negativo) cai para 0 sem quebrar.
- Sem teste automatizado de frontend (JS) — fora do padrão de testes já estabelecido no projeto (pytest-django, backend apenas).

---

## 4. Decision Log

| # | Decisão | Alternativas consideradas | Justificativa |
|---|---------|---------------------------|---------------|
| 1 | Offset em bytes | Offset em linhas | Mais simples, robusto a linha escrita pela metade entre polls |
| 2 | Reinício automático em truncamento | Retornar erro pedindo reset manual | Evita fricção/erro visível ao usuário após um restart normal do servidor |
| 3 | Reaproveitar URL/nome `view_logs` | Endpoint novo separado | Evita duas formas de pegar log coexistindo sem necessidade |
| 4 | Sem limite na carga inicial | Limitar a N últimos KB | YAGNI na escala de 1-10 servidores; já era o comportamento atual |
| 5 | Auto-scroll sempre | Auto-scroll condicional à posição do usuário | Simplicidade; mesmo comportamento implícito de hoje |
| 6 | `errors='replace'` sem alinhamento UTF-8 | Alinhar ao limite de caractere válido | Complexidade não se justifica para um glitch cosmético raríssimo numa tela de debug |
