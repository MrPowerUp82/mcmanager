# Captura de Saída de Erro do Java — Design

> Documento produzido via sessão de brainstorming em 2026-07-23.
> Status: **Aprovado**.
> Motivação: crash real ocorrido durante uso do painel — um jar Minecraft exigia Java 25, o `MCMANAGER_JAVA_BIN` apontava para Java 8, e o servidor "ligava por 1 segundo e desligava" sem nenhuma explicação visível no painel. Diagnóstico só foi possível rodando o comando manualmente fora do mcmanager, já que `process.start()` descarta toda a saída (stdout/stderr) do processo Java.

---

## 1. Resumo de Entendimento

- **O quê:** `process.start()` deixa de descartar a saída do processo Java (`subprocess.DEVNULL`) e passa a capturá-la num arquivo por servidor, sobrescrito a cada novo start. Um novo botão na página do console permite visualizar essa captura.
- **Por quê:** hoje, se o Java crasha logo após o start (versão incompatível, jar corrompido, memória mal configurada, etc.), não sobra nenhum diagnóstico em lugar nenhum — nem no painel, nem em arquivo. Isso foi confirmado num incidente real durante esta sessão.
- **Para quem:** mesmo público do resto do painel — staff investigando por que um servidor não sobe.
- **Escala:** um arquivo pequeno por servidor, sobrescrito a cada tentativa de start — sem acúmulo, sem rotação necessária.

### Suposições confirmadas

1. Abordagem reativa (capturar e mostrar a saída real), não proativa (mapeamento versão-do-Minecraft → versão-do-Java) — aprovado pelo usuário; um mapeamento desse tipo é frágil e precisaria ser atualizado a cada release do Minecraft, e já tinha sido descartado antes nesta sessão pelo mesmo motivo (ver `docs/superpowers/specs/2026-07-22-structured-errors-design.md`, item 3 do Decision Log).
2. Arquivo sobrescrito a cada `start()`, não acumulado — só a última tentativa importa; acumular cresceria sem limite com o supervisor tentando religar automaticamente várias vezes.
3. Arquivo de captura é **separado** do `logs/latest.log` que o próprio Minecraft escreve — captura desde o instante zero do processo, funcionando mesmo quando o crash acontece antes do Minecraft conseguir inicializar o próprio logger (exatamente o caso do incidente real: nem a pasta `logs/` chegou a ser criada).
4. Botão "Ver saída de erro" fica na página do console, **sempre visível** (não só quando o servidor está quebrado) — dashboard não é afetado nesta rodada.

---

## 2. Abordagem escolhida

Redirecionar `stdout`/`stderr` do `subprocess.Popen` diretamente para um arquivo (em vez de `DEVNULL`), e expor esse arquivo através de um endpoint de leitura simples — sem introduzir parsing de erros conhecidos (ex: detectar especificamente "UnsupportedClassVersionError" e traduzir pra uma mensagem amigável) nesta rodada. Mostrar a saída bruta já resolve o problema central (hoje não existe *nenhuma* visibilidade); interpretação automática de mensagens de erro específicas fica como possível melhoria futura, não necessária agora (YAGNI).

---

## 3. Design

### `services/process.py`

```python
def start(server):
    if is_running(server):
        raise AlreadyRunningError(f'Server {server.id} is already running')

    _check_port_available(server.port)

    if is_jar_missing(server):
        raise JarMissingError(f'Jar file missing for server {server.id}: {_jar_path(server)}')

    server_dir = settings.SERVERS_DIR / f'server_{server.id}'
    logs_dir = server_dir / 'logs'
    logs_dir.mkdir(parents=True, exist_ok=True)
    launch_output_path = logs_dir / 'last_start_output.log'

    cmd = [
        settings.JAVA_BIN_PATH,
        f'-Xms{server.memory_limit}M',
        f'-Xmx{server.memory_limit}M',
        '-jar', server.jar,
        'nogui',
    ]
    with launch_output_path.open('wb') as launch_output_file:
        kwargs = {
            'cwd': str(server_dir),
            'stdout': launch_output_file,
            'stderr': subprocess.STDOUT,
        }
        if os.name == 'posix':
            kwargs['start_new_session'] = True
        else:
            kwargs['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP

        try:
            proc = subprocess.Popen(cmd, **kwargs)
        except FileNotFoundError as exc:
            raise JavaNotFoundError(f'Java binary not found: {settings.JAVA_BIN_PATH}') from exc

    _write_state(server, proc.pid)
```

Pontos importantes:
- `logs_dir.mkdir(parents=True, exist_ok=True)` garante que a pasta existe **antes** do Java rodar — hoje ela só existia se o próprio Minecraft chegasse a criá-la.
- Abrir o arquivo em modo `'wb'` sobrescreve qualquer captura anterior automaticamente (sem precisar deletar o arquivo antes).
- `stderr=subprocess.STDOUT` combina as duas saídas no mesmo arquivo, na ordem real em que foram emitidas — suficiente para diagnóstico, sem precisar de dois arquivos separados.
- O arquivo (`with launch_output_path.open('wb') as launch_output_file`) precisa permanecer aberto até o `Popen` retornar (o processo filho herda o handle), mas pode ser fechado no processo pai logo depois — o `with` cobre isso corretamente, já que `subprocess.Popen` duplica o handle para o processo filho antes de retornar.

Nova função de leitura:

```python
def read_launch_output(server):
    path = settings.SERVERS_DIR / f'server_{server.id}' / 'logs' / 'last_start_output.log'
    if not path.exists():
        return None
    return path.read_text(encoding='utf-8', errors='replace')
```

### View (`console/views.py`)

```python
@staff_member_required
def launch_output(request, id):
    server = Server.objects.get(id=id)
    output = process.read_launch_output(server)
    if output is None:
        return json_error('No launch output captured yet', status=404)
    return JsonResponse({'status': 'success', 'output': output})
```

### URL (`console/urls.py`)

```python
path('launch_output/<int:id>', views.launch_output, name='launch_output'),
```

### Frontend (`console/index.html`)

Novo botão ao lado dos já existentes (Start/Stop/Force Stop/View Logs): "Ver saída de erro", que busca `launch_output` e mostra o conteúdo num painel de texto simples (mesmo estilo visual do `#logs` já existente), com tratamento de erro claro quando ainda não há captura (404 → mensagem "Nenhuma saída capturada ainda — inicie o servidor pelo menos uma vez").

### Testes

`process.py`: teste verificando que `start()` cria `server_dir/logs/` quando ainda não existe; teste de integração com um script mínimo (não o `fake_java_binary`/`fake_server.py` completo, que sempre inicia com sucesso) que escreve uma mensagem reconhecível em stderr e sai com código de erro, confirmando que `read_launch_output()` retorna exatamente essa mensagem depois; teste confirmando que uma segunda chamada a `start()` sobrescreve a captura anterior (não acumula). View testada com o client do Django: arquivo existente retorna 200 com o conteúdo esperado; arquivo ausente retorna 404 com a mensagem padronizada.

---

## 4. Decision Log

| # | Decisão | Alternativas consideradas | Justificativa |
|---|---------|---------------------------|---------------|
| 1 | Capturar saída real em vez de checagem proativa de versão | Mapeamento versão-do-Minecraft → versão-do-Java | Aprovado pelo usuário — mais simples, cobre qualquer causa de crash (não só versão do Java), sem mapeamento frágil pra manter atualizado a cada release |
| 2 | Sobrescrever a cada `start()`, não acumular | Acrescentar/rotacionar arquivos de log | Só a última tentativa importa pro diagnóstico; evita crescimento sem limite com o supervisor tentando religar automaticamente |
| 3 | Arquivo separado do `logs/latest.log` do Minecraft | Reaproveitar o mesmo arquivo | Captura desde o instante zero do processo, funciona mesmo se o Minecraft morrer antes de inicializar o próprio logger — exatamente o caso do incidente real que motivou este design |
| 4 | Botão sempre visível na página do console | Só aparecer quando o servidor estiver "quebrado"; também estender o badge do dashboard | Aprovado pelo usuário — menor escopo por agora; útil inspecionar mesmo em starts bem-sucedidos |
| 5 | Sem parsing/interpretação de mensagens de erro conhecidas (ex: detectar `UnsupportedClassVersionError` especificamente) | Traduzir para mensagens amigáveis tipo "Java incompatível, use uma versão mais nova" | YAGNI — mostrar a saída bruta já resolve o problema central (zero visibilidade hoje); interpretação automática fica pra uma iteração futura se necessário |
