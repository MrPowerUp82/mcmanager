import os
import sys
import argparse
import shutil
from pathlib import Path

def get_data_dir():
    """Determina o diretório de dados com base na variável de ambiente ou home"""
    if os.environ.get("MCMANAGER_DATA_DIR"):
        return Path(os.environ["MCMANAGER_DATA_DIR"]).resolve()
    
    debug_mode = os.environ.get("MCMANAGER_DEBUG", "False").lower() in ("true", "1", "yes")
    if debug_mode:
        # Se estiver em modo debug local, usa a pasta do próprio projeto
        return Path(__file__).resolve().parent.parent
    else:
        return Path.home() / ".mcmanager"

def main():
    parser = argparse.ArgumentParser(
        description="mcmanager - Painel de Controle de Servidores Minecraft"
    )
    subparsers = parser.add_subparsers(dest="command", required=True, help="Subcomando a executar")

    # Comando init
    init_parser = subparsers.add_parser("init", help="Inicializa o diretório de dados, banco e arquivos padrões")

    # Comando run
    run_parser = subparsers.add_parser("run", help="Inicia o servidor web do painel")
    run_parser.add_argument("--host", default="0.0.0.0", help="Endereço IP para escutar (padrão: 0.0.0.0)")
    run_parser.add_argument("--port", type=int, default=8000, help="Porta para escutar (padrão: 8000)")

    # Comando createsuperuser
    createsuperuser_parser = subparsers.add_parser(
        "createsuperuser", help="Cria um usuário administrador para o painel"
    )

    # Comando shell
    shell_parser = subparsers.add_parser("shell", help="Inicia o console interativo do Django")

    args = parser.parse_args()

    # 1. Obter e criar estrutura de dados do usuário
    data_dir = get_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "servers").mkdir(exist_ok=True)
    (data_dir / "jar").mkdir(exist_ok=True)
    (data_dir / "configs").mkdir(exist_ok=True)

    # 2. Copiar server.properties padrão se não existir
    properties_dest = data_dir / "configs" / "server.properties"
    if not properties_dest.exists():
        # Tenta carregar usando importlib.resources (PEP 517)
        copied = False
        try:
            from importlib import resources
            # Python 3.9+
            source_path = resources.files("mcmanager").joinpath("configs/server.properties")
            with source_path.open("rb") as fsrc:
                with open(properties_dest, "wb") as fdst:
                    shutil.copyfileobj(fsrc, fdst)
            copied = True
        except Exception:
            pass

        if not copied:
            # Fallback para caminho relativo
            source_path = Path(__file__).resolve().parent / "configs" / "server.properties"
            if source_path.exists():
                shutil.copy(source_path, properties_dest)
                copied = True

        if copied:
            print(f"[*] Arquivo server.properties padrão copiado para: {properties_dest}")
        else:
            print("[!] Aviso: Não foi possível copiar o arquivo server.properties padrão.")

    # 3. Configurar variáveis de ambiente do Django
    os.environ.setdefault("MCMANAGER_DATA_DIR", str(data_dir))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "mcmanager.settings")

    # 4. Inicializar Django
    import django
    django.setup()

    from django.core.management import call_command

    # 5. Executar os subcomandos
    if args.command == "init":
        print("[*] Executando migrações do banco de dados...")
        call_command("migrate", interactive=False)
        print("[*] Coletando arquivos estáticos...")
        call_command("collectstatic", "--noinput", "--clear")
        print("\n[+] Inicialização concluída com sucesso!")
        print(f"[+] Seus dados estão em: {data_dir}")
        print("[+] Agora você pode rodar: mcmanager createsuperuser")

    elif args.command == "run":
        bind_address = f"{args.host}:{args.port}"
        print(f"[*] Iniciando servidor mcmanager em http://{bind_address}/")
        print("[*] Pressione CTRL+C para encerrar.")
        # Em produção, rodamos sem autoreload
        call_command("runserver", "--noreload", "--nostatic", bind_address)

    elif args.command == "createsuperuser":
        try:
            call_command("createsuperuser")
        except KeyboardInterrupt:
            print("\n[!] Operação cancelada.")

    elif args.command == "shell":
        call_command("shell")

if __name__ == "__main__":
    main()
