# `mcmanager doctor` — Design

> Documento produzido via sessão de brainstorming em 2026-07-22.
> Status: **Aprovado**.
> Escopo: terceira das partes independentes da Fase 4 (ver `docs/design-melhorias.md`, seção 6). Triagem de vulnerabilidades e Dashboard já estão prontos. Erros estruturados/casos extremos e CI Linux+Windows ainda não começaram.

---

## 1. Resumo de Entendimento

- **O quê:** um novo subcomando `mcmanager doctor` que roda um conjunto de checagens de ambiente e reporta o resultado de cada uma, com exit code apropriado para uso em scripts.
- **Por quê:** hoje, se o ambiente está mal configurado (Java ausente, diretório de dados sem permissão de escrita, migrações pendentes), o erro só aparece de forma obscura quando o usuário tenta usar o painel (ex.: start de servidor falha com um erro genérico de `subprocess`, ou uma view quebra com uma exceção de banco). Design original da Fase 4 já previa isso: "novo `mcmanager doctor` para diagnóstico".
- **Para quem:** mantenedor/usuário do pacote PyPI, tipicamente rodando isso logo após instalar ou ao diagnosticar um problema.
- **Escala:** comando único, síncrono, roda uma vez e termina — sem novos requisitos de escala.

### Checagens desta primeira versão

1. **Java presente e versão** — verifica se o binário em `JAVA_BIN_PATH` existe e é executável, reporta a versão detectada. Não impõe um mínimo (versões diferentes de Minecraft exigem Javas diferentes; o projeto não mantém hoje um mapeamento versão-do-MC → versão-do-Java, então impor um mínimo seria uma suposição não sustentada).
2. **Diretórios de dados graváveis** — confirma que `JAR_DIR`, `SERVERS_DIR`, `CONFIGS_DIR`, `RUN_DIR`, `BACKUPS_DIR` são graváveis pelo usuário atual.
3. **Migrações do banco em dia** — verifica se há migrações Django pendentes não aplicadas.

(Persistência da secret key foi considerada e descartada nesta rodada — fora de escopo por decisão do usuário.)

### Suposições confirmadas

1. A existência dos diretórios de dados já é garantida hoje por `cli.py`/`settings.py` (ambos rodam `mkdir(parents=True, exist_ok=True)` para cada diretório antes de qualquer subcomando executar) — a checagem 2 foca exclusivamente em **gravabilidade** (`os.access(dir, os.W_OK)`), não em existência, já que essa parte nunca falharia de outra forma nesse fluxo.
2. Saída: lista legível no terminal (✓/✗ + mensagem por checagem), com exit code `0` se todas passarem e `1` se qualquer uma falhar — sem distinção de severidade entre checagens (todas são consideradas igualmente bloqueantes para um funcionamento correto do painel).

---

## 2. Abordagem escolhida

Um módulo novo `services/doctor.py`, seguindo o mesmo padrão dos outros serviços do projeto (`process.py`, `dashboard.py`, `supervisor.py`): funções puras e testáveis isoladamente, sem novas dependências externas (`subprocess` e `django.db.migrations.executor.MigrationExecutor`, ambos já disponíveis). `cli.py` ganha um subcomando fino que só chama `run_checks()`, imprime e decide o exit code — sem lógica de diagnóstico duplicada ali.

---

## 3. Design

### `services/doctor.py` (novo módulo)

```python
"""Environment diagnostics for `mcmanager doctor`. Each check is a small,
independently testable function returning a dict; run_checks() aggregates
them so the CLI only needs to print and decide the exit code."""
import os
import re
import subprocess

from django.conf import settings
from django.db.migrations.executor import MigrationExecutor
from django.db import connections

JAVA_VERSION_PATTERN = re.compile(r'version "([^"]+)"')
JAVA_CHECK_TIMEOUT_SECONDS = 5

DATA_DIRECTORIES = {
    'JAR_DIR': lambda: settings.JAR_DIR,
    'SERVERS_DIR': lambda: settings.SERVERS_DIR,
    'CONFIGS_DIR': lambda: settings.CONFIGS_DIR,
    'RUN_DIR': lambda: settings.RUN_DIR,
    'BACKUPS_DIR': lambda: settings.BACKUPS_DIR,
}


def check_java():
    java_path = settings.JAVA_BIN_PATH
    try:
        result = subprocess.run(
            [java_path, '-version'],
            capture_output=True,
            text=True,
            timeout=JAVA_CHECK_TIMEOUT_SECONDS,
        )
    except FileNotFoundError:
        return {'name': 'Java', 'passed': False, 'message': f'Java não encontrado em: {java_path}'}
    except OSError as exc:
        return {'name': 'Java', 'passed': False, 'message': f'Não foi possível executar Java em {java_path}: {exc}'}
    except subprocess.TimeoutExpired:
        return {'name': 'Java', 'passed': False, 'message': f'Java em {java_path} não respondeu em {JAVA_CHECK_TIMEOUT_SECONDS}s'}

    if result.returncode != 0:
        return {'name': 'Java', 'passed': False, 'message': f'Java em {java_path} retornou código {result.returncode}'}

    combined_output = result.stdout + result.stderr
    match = JAVA_VERSION_PATTERN.search(combined_output)
    if match:
        return {'name': 'Java', 'passed': True, 'message': f'Java {match.group(1)} encontrado em {java_path}'}
    return {'name': 'Java', 'passed': True, 'message': f'Java encontrado em {java_path} (versão não identificada)'}


def check_data_directories():
    unwritable = []
    for label, get_path in DATA_DIRECTORIES.items():
        path = get_path()
        if not os.access(path, os.W_OK):
            unwritable.append(f'{label} ({path})')

    if unwritable:
        return {
            'name': 'Diretórios de dados',
            'passed': False,
            'message': f'Sem permissão de escrita em: {", ".join(unwritable)}',
        }
    return {'name': 'Diretórios de dados', 'passed': True, 'message': 'Todos os diretórios de dados são graváveis'}


def check_migrations():
    connection = connections['default']
    executor = MigrationExecutor(connection)
    plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
    if plan:
        pending = ', '.join(f'{migration.app_label}.{migration.name}' for migration, _ in plan)
        return {'name': 'Migrações', 'passed': False, 'message': f'Migrações pendentes: {pending}'}
    return {'name': 'Migrações', 'passed': True, 'message': 'Banco de dados em dia'}


def run_checks():
    return [check_java(), check_data_directories(), check_migrations()]
```

Pontos importantes:
- Cada `check_*` função é independente e testável isoladamente (mockando `subprocess.run` para o Java, ajustando permissões de diretório via `tmp_path` para diretórios, usando o banco real de teste — que sempre está com migrações em dia sob `pytest-django` — para migrações, com um segundo teste simulando uma migração pendente via um app de teste dedicado ou mockando `MigrationExecutor.migration_plan`).
- `run_checks()` nunca levanta exceção — cada `check_*` já trata seus próprios erros esperados e sempre retorna um dict.

### CLI (`cli.py`)

Novo subparser:

```python
doctor_parser = subparsers.add_parser("doctor", help="Diagnostica o ambiente (Java, diretórios, banco de dados)")
```

Novo branch de execução (mesma posição dos outros comandos, após `django.setup()`):

```python
elif args.command == "doctor":
    from mcmanager.console.services import doctor
    results = doctor.run_checks()
    all_passed = True
    for result in results:
        symbol = "✓" if result['passed'] else "✗"
        print(f"[{symbol}] {result['name']}: {result['message']}")
        if not result['passed']:
            all_passed = False
    sys.exit(0 if all_passed else 1)
```

### Testes

`services/doctor.py` testado com `check_java` mockando `subprocess.run` (3 casos: sucesso com versão detectada, `FileNotFoundError`, saída sem padrão de versão reconhecível); `check_data_directories` testado com `tmp_path` + `settings` sobrescritas via fixture `settings` do `pytest-django`, removendo permissão de escrita de um diretório num caso e mantendo tudo ok no outro; `check_migrations` testado com o banco de teste normal (deve passar, já que `pytest-django` sempre aplica todas as migrações antes dos testes) e com `MigrationExecutor.migration_plan` mockado para simular uma pendência. CLI testado invocando `doctor.run_checks` mockado e verificando que o exit code reflete corretamente o resultado (todos passam → 0; algum falha → 1).

---

## 4. Decision Log

| # | Decisão | Alternativas consideradas | Justificativa |
|---|---------|---------------------------|---------------|
| 1 | 3 checagens nesta versão: Java, diretórios graváveis, migrações pendentes | Incluir também persistência da secret key | Aprovado pelo usuário — as 3 primeiras cobrem os erros mais comuns e obscuros hoje; secret key fica pra uma rodada futura se necessário |
| 2 | Checagem de diretórios foca em gravabilidade, não existência | Checar também existência | Existência já é garantida por `cli.py`/`settings.py` antes de qualquer subcomando rodar — checar de novo seria redundante |
| 3 | Java: reporta versão encontrada, sem impor mínimo | Exigir uma versão mínima (ex.: Java 17+) | Projeto não mantém hoje um mapeamento versão-do-MC → versão-do-Java; impor um mínimo seria uma suposição não sustentada pelos dados que o projeto tem |
| 4 | Saída: lista legível + exit code (0/1), sem níveis de severidade | JSON estruturado; severidades diferentes por checagem | Aprovado pelo usuário — simples de ler no terminal e usável em scripts via exit code; todas as 3 checagens são igualmente bloqueantes pro funcionamento correto do painel |
| 5 | Módulo novo em `services/doctor.py`, CLI só imprime e decide exit code | Lógica de diagnóstico direto em `cli.py` | Mesma separação já usada pelos outros serviços do projeto (`process.py`, `dashboard.py`) — mantém `cli.py` fino e o diagnóstico testável isoladamente |
