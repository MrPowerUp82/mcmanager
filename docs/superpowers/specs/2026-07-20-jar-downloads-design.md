# Download de Jars — Design

> Documento produzido via sessão de brainstorming em 2026-07-20.
> Status: **Aprovado**.
> Escopo: segunda das 4 partes independentes da Fase 3 (ver `docs/design-melhorias.md`, seção 5). Console em tempo real (parte 1) já está pronto. Backups e auto-restart/agendamento (partes 3-4) são ciclos separados, não cobertos aqui.

---

## 1. Resumo de Entendimento

- **O quê:** uma página dedicada (`/console/jars/`) onde o usuário escolhe provedor (Mojang ou PaperMC) + versão do Minecraft, dispara um download em background, e acompanha o status via polling. Jars baixados caem em `JAR_DIR` e passam a aparecer automaticamente no formulário de criar/editar servidor, exatamente como um jar colocado manualmente hoje.
- **Por quê:** hoje o usuário precisa colocar o `.jar` manualmente na pasta — isso automatiza o caso mais comum (Vanilla/Paper), sem remover a opção de upload manual.
- **Para quem:** mesmo público do resto do painel — qualquer usuário staff.
- **Escala:** mesma dos ciclos anteriores — 1 máquina, uso de admin, sem preocupação com múltiplos downloads concorrentes de usuários diferentes.

### Não-objetivos
- Registro permanente de "biblioteca de jars" no banco (só a tarefa de download em si é registrada).
- Escolha de build específica do Paper (sempre pega a mais recente da versão escolhida).
- Suporte a outros provedores (Forge, Spigot, etc.) — a interface comum entre provedores deixa a porta aberta, mas não é escopo agora.
- Barra de progresso byte-a-byte (só estado: pending/downloading/done/error).

### Suposições confirmadas
1. `urllib.request` da stdlib para as chamadas HTTP — mantém a disciplina de zero dependências novas já seguida nas Fases 1 e 2 (nem o cliente RCON, mais complexo que isso, adicionou uma lib).
2. Download roda numa `threading.Thread` daemon simples — mesmo padrão que a Fase 3 parte 4 (auto-restart) também vai usar.
3. Todos os endpoints exigem `@staff_member_required`.
4. SHA mismatch → rejeita e apaga o arquivo, marca erro. Mojang usa SHA1, PaperMC usa SHA256 — validação agnóstica ao algoritmo via `hashlib.new(algorithm)`.
5. Mojang: só releases estáveis (`type=='release'` no manifest), mais recentes primeiro.

---

## 2. Abordagem escolhida

Interface comum leve entre provedores (`VersionInfo`/`DownloadInfo` como dataclasses, cada módulo de provedor implementando `list_versions()`/`get_download_info()`) em vez de branches `if provider == 'mojang'` espalhados pela view/orquestrador. Complexidade mínima — duas funções por provedor — mas evita duplicar a lógica de "baixar + validar hash" e deixa a porta aberta pra um terceiro provedor no futuro sem mexer no orquestrador.

---

## 3. Design

### Estrutura de arquivos

```
mcmanager/console/
├── models.py                    # + JarDownload
├── services/
│   ├── jars.py                   # orquestrador: list_versions(), start_download()
│   └── jar_providers/
│       ├── base.py                # VersionInfo, DownloadInfo (dataclasses)
│       ├── mojang.py               # list_versions(), get_download_info()
│       └── paper.py                # list_versions(), get_download_info()
├── views_jars.py                 # views da página de jars (separado de views.py)
├── urls.py                       # + rotas de jars
├── templates/console/
│   └── jars.html                  # nova página
└── tests/
    ├── test_jar_providers.py      # Mojang/Paper com HTTP mockado
    └── test_jars.py               # orquestrador com URLs file:// (sem mock)
```

### Modelo de dados

```python
class JarDownload(models.Model):
    STATUS_CHOICES = [('pending', 'Pending'), ('downloading', 'Downloading'),
                       ('done', 'Done'), ('error', 'Error')]
    provider = models.CharField(max_length=20)       # 'mojang' | 'paper'
    version = models.CharField(max_length=50)
    filename = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
```

Sem `ForeignKey` pra `Server` — tarefa de download desacoplada de qualquer servidor específico.

### Provedores

**`base.py`:**
```python
@dataclass
class VersionInfo:
    version: str
    label: str

@dataclass
class DownloadInfo:
    url: str
    filename: str
    expected_hash: str
    hash_algorithm: str  # 'sha1' ou 'sha256'
```

**`mojang.py`** — usa o version manifest v2 (`https://piston-meta.mojang.com/mc/game/version_manifest_v2.json`). `list_versions()` filtra `type=='release'`. `get_download_info(version)` resolve a URL específica da versão e pega `downloads.server.{url,sha1}`. Hash: sha1.

**`paper.py`** — usa a API v2 do PaperMC (`https://api.papermc.io/v2/`). `list_versions()` lista `GET /v2/projects/paper` → campo `versions`. `get_download_info(version)` busca `GET /v2/projects/paper/versions/{version}/builds`, pega a build de maior número, usa `downloads.application.{name,sha256}` dela. Hash: sha256.

Ambos com timeout de 10s em toda chamada de rede.

### Orquestrador (`services/jars.py`)

```python
PROVIDERS = {'mojang': mojang, 'paper': paper}

def list_versions(provider_name):
    return PROVIDERS[provider_name].list_versions()

def start_download(provider_name, version):
    download = JarDownload.objects.create(provider=provider_name, version=version, status='pending')
    threading.Thread(target=_run_download, args=(download.id,), daemon=True).start()
    return download

def _run_download(download_id):
    download = JarDownload.objects.get(id=download_id)
    download.status = 'downloading'
    download.save(update_fields=['status'])
    try:
        info = PROVIDERS[download.provider].get_download_info(download.version)
        dest_path = settings.JAR_DIR / info.filename
        tmp_path = dest_path.with_suffix(dest_path.suffix + '.part')
        hasher = hashlib.new(info.hash_algorithm)

        with urllib.request.urlopen(info.url, timeout=30) as resp, open(tmp_path, 'wb') as f:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                f.write(chunk)
                hasher.update(chunk)

        if hasher.hexdigest() != info.expected_hash:
            tmp_path.unlink(missing_ok=True)
            download.status = 'error'
            download.error_message = 'Hash mismatch -- downloaded file did not match expected checksum'
            download.save(update_fields=['status', 'error_message'])
            return

        tmp_path.rename(dest_path)
        download.filename = info.filename
        download.status = 'done'
        download.save(update_fields=['filename', 'status'])
    except Exception as exc:
        download.status = 'error'
        download.error_message = str(exc)
        download.save(update_fields=['status', 'error_message'])
```

Baixa para um arquivo `.part` e só faz `rename()` pro nome final `.jar` depois do hash bater — `get_jar_files()` só enxerga `.jar`, então um download pela metade nunca aparece como opção válida no formulário de servidor.

### Views e URLs (`views_jars.py`)

Todos `@staff_member_required`:
- `GET /console/jars/` (`jars_page`) — renderiza a página.
- `GET /console/jars/versions/<provider>` (`list_jar_versions`) — chama `jars.list_versions`, retorna `{status, versions: [{version, label}]}`.
- `POST /console/jars/download` (`start_jar_download`) — recebe `provider`+`version`, chama `jars.start_download`, retorna `{status, download_id}`.
- `GET /console/jars/download/<id>` (`jar_download_status`) — retorna `{status, download_status, error_message, filename}`.

### Frontend (`templates/console/jars.html`)

Página nova e independente, sem depender de nenhum servidor específico. Dois `<select>` (provedor → versão, o segundo recarregado via `fetch` quando o primeiro muda), botão "Baixar", área de status com polling a cada 2s em `jar_download_status` até `done`/`error`, usando os mesmos `create_toast`/CSRF já estabelecidos nos outros templates.

### Testes

- `test_jar_providers.py`: mocka `urllib.request.urlopen` pros dois provedores com JSON de exemplo real, verificando parsing pra `VersionInfo`/`DownloadInfo` (filtro de releases da Mojang, escolha de build mais recente do Paper).
- `test_jars.py`: testa o orquestrador sem mockar nada — usa URLs `file://` apontando pra um arquivo de teste local com hash conhecido, simulando um provedor fake. Cobre: download com sucesso, hash errado (`.part` removido, status `error`, nenhum `.jar` inválido sobra), provedor/versão inexistente.

---

## 4. Decision Log

| # | Decisão | Alternativas consideradas | Justificativa |
|---|---------|---------------------------|---------------|
| 1 | Página dedicada de jars | Dentro do ServerForm | Desacopla "gerenciar biblioteca de jars" de "configurar servidor" |
| 2 | Polling simples de status | Barra de progresso byte-a-byte | Jars de Minecraft baixam em segundos numa VPS; complexidade não se justifica |
| 3 | JarDownload só rastreia a tarefa | Registro permanente de biblioteca de jars | Zero mudança no ServerForm/fluxo existente; `get_jar_files()` continua sendo a fonte de verdade |
| 4 | Hash mismatch rejeita e apaga | Aceitar com aviso | Postura de segurança consistente com as Fases 1-2 |
| 5 | Mojang: só releases, mais recentes primeiro | Todas as versões incluindo snapshots | Cobre o caso de uso real de administração de servidor |
| 6 | Interface comum entre provedores (`VersionInfo`/`DownloadInfo`) | Branch direto por provedor na view | Evita duplicar lógica de download/hash; abre porta pra novo provedor sem mexer no orquestrador |
| 7 | `urllib.request` (stdlib) | Adicionar `requests` | Mantém disciplina de zero dependências novas já seguida desde a Fase 1 |
| 8 | `threading.Thread` daemon | Celery/fila de tarefas | Sem dependências novas; mesmo padrão que a Fase 3 parte 4 também vai usar |
| 9 | Paper sempre pega a build mais recente | Usuário escolhe a build | Cobre o caso de uso real; evita um segundo dropdown pra um cenário raro |
| 10 | Testes do orquestrador via URLs `file://`, sem mock | Mockar `urllib.request` também no orquestrador | Testa o código de download real de ponta a ponta, mesmo espírito dos fakes já usados na Fase 2 |
