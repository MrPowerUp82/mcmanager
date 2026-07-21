# Backups — Design

> Documento produzido via sessão de brainstorming em 2026-07-21.
> Status: **Aprovado**.
> Escopo: terceira das 4 partes independentes da Fase 3 (ver `docs/design-melhorias.md`, seção 5). Console em tempo real (parte 1) e download de jars (parte 2) já estão prontos. Auto-restart/agendamento (parte 4) é um ciclo separado, não coberto aqui.

---

## 1. Resumo de Entendimento

- **O quê:** botão "Fazer backup" na página do servidor + lista dos backups existentes daquele servidor, com opções de restaurar e apagar cada um. Backup zipa o diretório inteiro do servidor; se estiver rodando, usa `save-off`+`save-all` via RCON antes de zipar e `save-on` depois (evita mundo corrompido). Restauração só com servidor parado, com confirmação explícita — substitui o conteúdo do diretório do servidor pelo conteúdo do zip.
- **Por quê:** hoje não existe nenhuma forma de backup no painel — perda de um servidor (disco, erro do usuário, corrupção) é irrecuperável.
- **Para quem:** mesmo público do resto do painel — qualquer usuário staff.
- **Escala:** mesma dos ciclos anteriores — 1 máquina, uso de admin.

### Não-objetivos
- Backup incremental/diferencial — sempre zip completo do diretório.
- Backup automático/agendado — isso é a parte 4 da Fase 3 (auto-restart/agendamento), ciclo separado.
- Limpeza de arquivos `.zip` órfãos se um `Server` for apagado (o `Backup` no banco some via `CASCADE`, o arquivo em disco fica — fora de escopo).

### Suposições confirmadas
1. Retenção de 5 backups é por servidor (cada servidor tem seus próprios últimos 5).
2. Backup roda em thread de background com modelo de status (`Backup`), mesmo padrão já validado nos downloads de jars — evita timeout de request em mundos grandes.
3. Restauração é síncrona (rápida: só descompacta + troca diretório, sem RCON já que o servidor tem que estar parado).
4. `save-off`/`save-all`/`save-on` reaproveitam `process.send_command()` já existente — sem mudança no cliente RCON.

---

## 2. Abordagem escolhida

Thread de background + polling de status pro backup (Opção A do brainstorm), reaproveitando exatamente o padrão já usado e testado em `services/jars.py`. Alternativa descartada: síncrono/bloqueante — arriscaria timeout de request em mundos grandes e deixaria a UI travada sem feedback durante o zip inteiro.

---

## 3. Design

### Estrutura de arquivos

```
mcmanager/console/
├── models.py                    # + Backup
├── services/
│   └── backups.py                # orquestrador
├── views_backups.py              # views (separado, mesmo padrão de views_jars.py)
├── urls.py                       # + rotas de backup
├── templates/console/
│   └── index.html                 # + seção de backups
└── tests/
    └── test_backups.py
```

### Modelo de dados

```python
class Backup(models.Model):
    STATUS_CHOICES = [('pending', 'Pending'), ('running', 'Running'),
                       ('done', 'Done'), ('error', 'Error')]
    server = models.ForeignKey(Server, on_delete=models.CASCADE, related_name='backups')
    filename = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
```

Diferente do `JarDownload` (sem `ForeignKey`), `Backup` **tem** `ForeignKey` para `Server` — um backup é intrinsecamente de um servidor específico; listagem e retenção "últimos 5 deste servidor" precisam dessa relação.

`settings.py` ganha `BACKUPS_DIR = USER_DATA_DIR / 'backups'`, mesmo padrão de `JAR_DIR`/`RUN_DIR`.

### Orquestrador (`services/backups.py`)

```python
RETENTION_COUNT = 5


class RestoreServerRunningError(Exception):
    """Raised when trying to restore a backup while the server is still running."""


def list_backups(server):
    backup_dir = settings.BACKUPS_DIR / f'server_{server.id}'
    if not backup_dir.exists():
        return []
    return sorted((p.name for p in backup_dir.glob('*.zip')), reverse=True)


def start_backup(server):
    backup = Backup.objects.create(server=server, status='pending')
    threading.Thread(target=_run_backup, args=(backup.id,), daemon=True).start()
    return backup


def _run_backup(backup_id):
    backup = Backup.objects.get(id=backup_id)
    server = backup.server
    backup.status = 'running'
    backup.save(update_fields=['status'])

    server_dir = settings.SERVERS_DIR / f'server_{server.id}'
    backup_dir = settings.BACKUPS_DIR / f'server_{server.id}'
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    filename = f'{timestamp}.zip'
    dest_path = backup_dir / filename
    server_was_running = process.is_running(server)

    try:
        if server_was_running:
            process.send_command(server, 'save-off')
            process.send_command(server, 'save-all')

        with tempfile.TemporaryDirectory(dir=str(backup_dir)) as tmp_dir:
            archive_path = shutil.make_archive(str(Path(tmp_dir) / timestamp), 'zip', root_dir=str(server_dir))
            shutil.move(archive_path, str(dest_path))

        backup.filename = filename
        backup.status = 'done'
        backup.save(update_fields=['filename', 'status'])
        _apply_retention(server)
    except Exception as exc:
        backup.status = 'error'
        backup.error_message = str(exc)
        backup.save(update_fields=['status', 'error_message'])
    finally:
        if server_was_running:
            try:
                process.send_command(server, 'save-on')
            except Exception:
                pass  # best-effort -- doesn't mask the backup's own outcome


def _apply_retention(server):
    for old_filename in list_backups(server)[RETENTION_COUNT:]:
        (settings.BACKUPS_DIR / f'server_{server.id}' / old_filename).unlink(missing_ok=True)
        Backup.objects.filter(server=server, filename=old_filename).delete()


def start_restore(server, filename):
    if process.is_running(server):
        raise RestoreServerRunningError(f'Server {server.id} must be stopped before restoring a backup')
    backup_path = settings.BACKUPS_DIR / f'server_{server.id}' / filename
    if not backup_path.exists():
        raise FileNotFoundError(f'Backup not found: {filename}')

    server_dir = settings.SERVERS_DIR / f'server_{server.id}'
    tmp_dir = Path(tempfile.mkdtemp(dir=str(settings.SERVERS_DIR)))
    try:
        shutil.unpack_archive(str(backup_path), str(tmp_dir), 'zip')
        if server_dir.exists():
            shutil.rmtree(server_dir)
        tmp_dir.rename(server_dir)
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise


def delete_backup(server, filename):
    backup_path = settings.BACKUPS_DIR / f'server_{server.id}' / filename
    backup_path.unlink(missing_ok=True)
    Backup.objects.filter(server=server, filename=filename).delete()
```

Pontos importantes:
- `save-on` roda num `finally`, mesmo se o backup falhar no meio — deixar `save-off` ligado depois de um erro arriscaria perda de dados se o servidor caísse antes de alguém religar manualmente.
- O zip é montado num diretório temporário DENTRO de `backup_dir` (mesmo filesystem, então `shutil.move` é um rename atômico) e só "aparece" com o nome final depois de pronto — mesmo princípio do `.part` dos jars, adaptado porque `shutil.make_archive` não permite escolher o nome do arquivo final livremente.
- Restauração descompacta num diretório temporário primeiro e só apaga o diretório atual do servidor depois que a descompactação já deu certo — evita deixar o servidor num estado pela metade se o zip estiver corrompido.
- Retenção e exclusão manual apagam arquivo E registro `Backup` juntos — sem catálogo permanente, mesma filosofia dos jars.

### Views e URLs (`views_backups.py`)

Todas `@staff_member_required`:
- `GET /console/backups/<server_id>` (`list_backups_view`) — lista os backups do servidor.
- `POST /console/backups/<server_id>/create` (`start_backup_view`) — inicia backup, retorna `backup_id`.
- `GET /console/backups/status/<backup_id>` (`backup_status_view`) — status pra polling.
- `POST /console/backups/<server_id>/restore` (`restore_backup_view`) — restaura (`filename` no body); erro claro se servidor rodando ou arquivo não existe.
- `POST /console/backups/<server_id>/delete` (`delete_backup_view`) — apaga.

### Frontend

Nova seção "Backups" em `console/index.html` (mesma página do servidor, junto de start/stop/logs): botão "Fazer backup" + polling de status (mesmo padrão de `jars.html`), lista de backups com botões "Restaurar" (confirmação via `confirm()` do navegador, desabilitado se o servidor estiver rodando) e "Apagar" (confirmação via `confirm()`).

### Testes

- `test_backups.py`: backup com servidor parado (sem chamadas RCON); backup com servidor rodando (save-off/save-all/save-on chamados na ordem certa, incluindo save-on mesmo se o zip falhar); retenção (6º backup apaga o mais antigo, arquivo e registro); restauração com sucesso (conteúdo do servidor bate com o do zip); restauração rejeitada com servidor rodando; restauração com arquivo inexistente; exclusão remove arquivo e registro.

---

## 4. Decision Log

| # | Decisão | Alternativas consideradas | Justificativa |
|---|---------|---------------------------|---------------|
| 1 | Botão na página do servidor | Página separada de backups | Backup é uma ação por servidor, mais natural junto de start/stop/logs |
| 2 | Retenção de 5 por servidor | Retenção global | Cada servidor tem seu próprio histórico, não faz sentido misturar |
| 3 | Thread de background + polling | Síncrono/bloqueante | Evita timeout de request em mundos grandes; mesmo padrão já validado nos jars |
| 4 | `Backup` com `ForeignKey` pra `Server` | Sem relação, como `JarDownload` | Backup é intrinsecamente de um servidor; listagem/retenção por servidor precisam disso |
| 5 | Restauração síncrona | Restauração em background também | Rápida o suficiente (sem RCON, servidor já parado); simplifica a UI |
| 6 | `save-on` em `finally` | Só no caminho de sucesso | Evita deixar o mundo em modo "save-off" permanentemente se o backup falhar |
| 7 | Zip em diretório temporário + `shutil.move` atômico | Escrever direto no destino final | Backup pela metade nunca aparece como opção válida de restauração |
| 8 | Restauração extrai em tmp antes de apagar o diretório atual | Apagar primeiro, depois extrair | Um zip corrompido não deixa o servidor num estado pela metade |
