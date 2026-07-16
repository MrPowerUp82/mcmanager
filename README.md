# mcmanager (BETA)

Um painel web simples baseado em Django para gerenciar servidores Minecraft diretamente na sua VPS Linux.

## Instalação

### 1. Instalar o Java 8 (Necessário para o Minecraft)

```console
sudo apt-get update
sudo apt-get install openjdk-8-jre-headless
```

### 2. Instalar o mcmanager do PyPI

```console
pip install mcmanager
```

### 3. Inicializar e rodar o painel

```console
# Inicializa o banco de dados e cria os diretórios na pasta ~/.mcmanager
mcmanager init

# Cria um usuário administrador
mcmanager createsuperuser

# Inicia o painel
mcmanager run
```

Pronto! Acesse `http://sua-vps-ip:8000/admin/` para logar com o superusuário criado e começar a gerenciar seus servidores.

---

## Estrutura de Diretórios e Dados

Por padrão, todos os dados do painel ficam armazenados de forma isolada na home do usuário:
- `~/.mcmanager/db.sqlite3` (Banco de dados do painel)
- `~/.mcmanager/jar/` (Coloque seus arquivos `.jar` de servidor aqui, ex: `spigot.jar`, `vanilla.jar`)
- `~/.mcmanager/servers/` (Diretório onde cada servidor criado será executado)
- `~/.mcmanager/configs/` (Onde fica o `server.properties` padrão)
- `~/.mcmanager/staticfiles/` (Arquivos estáticos compilados do painel)

### Customizações por Variáveis de Ambiente

Você pode ajustar o comportamento do painel definindo variáveis de ambiente antes de executar o comando `mcmanager`:

* **`MCMANAGER_DATA_DIR`**: Altera a pasta padrão onde os servidores e banco de dados são salvos (padrão: `~/.mcmanager`).
* **`MCMANAGER_JAVA_BIN`**: Especifica o caminho binário do Java (padrão: `/usr/lib/jvm/java-8-openjdk-amd64/bin/java`). Use `whereis java` para encontrar o caminho correto na sua VPS.
* **`MCMANAGER_ALLOWED_HOSTS`**: Lista de hosts permitidos separados por vírgula (padrão: `*`).
* **`MCMANAGER_DEBUG`**: Define se o painel roda em modo debug (`True` ou `False`).

Exemplo definindo um caminho diferente de Java e iniciando:
```console
export MCMANAGER_JAVA_BIN="/usr/bin/java"
mcmanager run
```

---

## Desenvolvimento Local

Se você deseja contribuir ou modificar o código localmente:

1. Clone o repositório.
2. Crie e ative um ambiente virtual.
3. Instale as dependências e o pacote local no modo editável:
   ```console
   pip install -r requirements.txt
   pip install -e .
   ```
4. Ao rodar `python manage.py runserver`, o painel rodará em modo debug local e usará a própria pasta do projeto para salvar os arquivos de desenvolvimento.

### Comandos de Desenvolvimento Úteis

* **Formatar código:**
  ```console
  python manage.py format_code
  ```
* **Gerar nova Secret Key de desenvolvimento:**
  ```console
  python manage.py generate_secret_key
  ```

## Capturas de Tela

![alt text](screenshot.png)
![alt text](screenshot2.png)