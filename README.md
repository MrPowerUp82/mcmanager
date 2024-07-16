# mcmanager (BETA)

## Instalação

```console
sudo apt-get install openjdk-8-jre-headless
```

```console
python3 -m pip install -r requirements.txt
python3 manage.py generate_secret_key
python3 manage.py migrate 
python3 manage.py createsuperuser     
```

## Comandos Customizados

### Gerar chave secreta
  ```console
python3 manage.py generate_secret_key
  ```
### Formatar arquivos: .py, .html, .js, .css
```console
python3 manage.py format_code
  ```

## Exemplo

![alt text](screenshot.png)
![alt text](screenshot2.png)