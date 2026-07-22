# Triagem de Vulnerabilidades (Fase 4, parte 1) — Design

> Documento produzido via sessão de brainstorming em 2026-07-22.
> Status: **Aprovado**.
> Escopo: primeira das partes independentes da Fase 4 (ver `docs/design-melhorias.md`, seção 6). Dashboard, `mcmanager doctor`, erros estruturados/casos extremos e CI Linux+Windows ainda não começaram.

---

## 1. Resumo de Entendimento

- **O quê:** sincronizar `requirements.txt` com as versões já instaladas e validadas no ambiente de dev (Django 5.1.15, sqlparse 0.5.5), e rodar `npm audit fix` para atualizar as dependências transitivas do `tailwindcss` no `package-lock.json`. Zero mudança de código de aplicação esperada.
- **Por quê:** GitHub Dependabot flagou 30 vulnerabilidades em `origin/main` após o push da Fase 3 (2026-07-21): 6 críticas, 16 altas, 8 moderadas.
- **Para quem:** mantenedor solo, preparando o pacote para distribuição pública no PyPI — vulnerabilidades conhecidas em dependências públicas são um risco direto de reputação e segurança.
- **Escala:** mudança de arquivos de dependência + lockfile, sem alteração de lógica.

### Causa raiz identificada

1. `requirements.txt` fixa `Django==4.0.5` (lançado em 2022, sem suporte de segurança) e `sqlparse==0.5.0`. O ambiente de dev, porém, já roda `Django 5.1.15` e `sqlparse 0.5.5` de fato (confirmado via `pip show`) — versões que já resolvem essas CVEs. O arquivo está desatualizado em relação à realidade, não o ambiente.
2. `requirements-dev.txt` contém apenas `-r requirements.txt` + `pytest`/`pytest-django` — por isso cada CVE do Django aparece duplicado nos alertas (uma vez por arquivo que referencia a mesma versão presa).
3. `pyproject.toml` já declara `Django>=4.2.0,<5.2.0` — 5.1.15 já cabe nesse intervalo, nenhuma mudança necessária ali.
4. Os 4 alertas npm (glob, minimatch, postcss, picomatch) são dependências transitivas da única devDependency declarada em `package.json` (`tailwindcss ^3.4.4`), usada só para build de CSS (`npm run build:css`) — nunca vão para o pacote PyPI publicado. `npm audit` confirma 9 vulnerabilidades totais nessa árvore (4 moderadas, 5 altas), todas com `fix available via npm audit fix` (sem necessidade de `--force` ou downgrade/upgrade de major do tailwindcss).

### Suposições confirmadas

1. Risco de regressão real é baixo: o Django 5.1.15 já é a versão sob a qual toda a suíte de testes (139 testes) rodou durante as Fases 1–3 desta sessão, sem incidente atribuído à versão do Django.
2. Não há necessidade de revisar manualmente breaking changes entre Django 4.0 e 5.1 (ex.: mudanças em `USE_L10N`, formsets, etc.) — o código já roda e passa nos testes sob 5.1.15 na prática, não é uma migração especulativa.
3. `npm audit fix` (sem `--force`) é suficiente para resolver as 9 vulnerabilidades da árvore de dependências do `tailwindcss` sem quebrar a API pública que `package.json` consome (`tailwindcss build ...`).

---

## 2. Abordagem escolhida

Edição direta e mínima de 2 arquivos de dependência (`requirements.txt`, `package-lock.json` via `npm audit fix`) — sem introduzir ferramentas novas (ex.: `pip-audit`, `renovate`, `dependabot.yml` de auto-merge), que ficam fora de escopo (YAGNI para um projeto de 1 mantenedor).

---

## 3. Design

### Mudanças

**`requirements.txt`:**
```diff
 asgiref==3.8.1
-Django==4.0.5
+Django==5.1.15
 psutil==6.0.0
-sqlparse==0.5.0
+sqlparse==0.5.5
 typing_extensions==4.12.2
 autopep8==2.3.1
 djlint==1.34.1
```

**`requirements-dev.txt`:** nenhuma mudança de conteúdo — herda a correção via `-r requirements.txt`.

**`pyproject.toml`:** nenhuma mudança — `Django>=4.2.0,<5.2.0` já cobre `5.1.15`.

**`package-lock.json`:** rodar `npm audit fix` (sem `--force`) e commitar o lockfile atualizado. Não editar `package.json` (a devDependency direta `tailwindcss ^3.4.4` não muda).

### Verificação

1. `python -m pip install -r requirements.txt` (ou confirmar que o ambiente atual já satisfaz o novo pin, já que a versão já instalada é exatamente a versão alvo).
2. `python -m pytest -v` — suíte completa (139 testes) deve permanecer verde.
3. `npm audit fix`, depois `npm audit` — deve reportar 0 vulnerabilidades (ou, se alguma sobrar sem fix automático, documentar por que ela é aceitável neste momento em vez de escondê-la).
4. `npm run build:css` — confirmar que o build de CSS continua funcionando após a atualização do lockfile.
5. Revisar visualmente o diff do `package-lock.json` para confirmar que nenhuma dependência de produção (`dependencies`, não `devDependencies`) foi afetada — não há nenhuma no `package.json` atual, mas vale confirmar.

### Não-objetivos

- Atualizar `tailwindcss` em si (só as transitivas flagadas pelo Dependabot).
- Introduzir ferramentas de triagem contínua (`pip-audit` no CI, `dependabot.yml` com auto-merge) — pode ser considerado como parte da parte "CI Linux+Windows" da Fase 4, não aqui.
- Revisão de código para aproveitar novas features do Django 5.1 (ex.: `GeneratedField`, mudanças em `forms`) — fora de escopo, é só remediar vulnerabilidades.

---

## 4. Decision Log

| # | Decisão | Alternativas consideradas | Justificativa |
|---|---------|---------------------------|---------------|
| 1 | Alvo de versão: Django 5.1.15 (a mesma já instalada/testada) | Última versão 5.1.x disponível no momento do plano | Já validada por 139 testes ao longo de toda a Fase 1-3; menor risco de surpresa de última hora |
| 2 | Corrigir Python e npm na mesma rodada | Só Python agora, npm depois | Todos os 4 alertas npm têm fix automático (`npm audit fix`, sem `--force`) — barato, fecha os 30 alertas de uma vez |
| 3 | Editar só `requirements.txt` (não `requirements-dev.txt` diretamente) | Duplicar o pin em `requirements-dev.txt` | `requirements-dev.txt` já herda via `-r requirements.txt`; duplicar criaria uma segunda fonte de verdade |
| 4 | `npm audit fix` sem `--force` | `npm audit fix --force` (permite bumps de major) | Todas as 9 vulnerabilidades da árvore já têm fix disponível sem forçar breaking changes |
