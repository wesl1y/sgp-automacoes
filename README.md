# Escola 10 / Pé de Meia — Automação de Dados (SEDUC)

Aplicação web em **Streamlit** que substitui a coleção de scripts Python soltos (Colab/Pandas) usados nas rotinas do **Escola 10** e do **Pé de Meia** por uma interface única de upload/processamento/download de planilhas.

## Stack

- **Streamlit** — interface web (upload/download de planilhas e relatórios)
- **Polars** — processamento das bases em memória
- **DuckDB** — consultas SQL analíticas direto sobre os DataFrames do Polars
- **Pydantic** — validação dos schemas de entrada/saída

## Pré-requisitos

- Python 3.11+ (o ambiente virtual do projeto foi criado com Python 3.14)
- `pip`

## Como rodar localmente

1. **Crie e ative um ambiente virtual** (o projeto já ignora `.venv/` no Git, então cada máquina cria o seu):

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate      # Linux/macOS
   # .venv\Scripts\activate       # Windows
   ```

   Se você já tem uma pasta `.venv` neste projeto, só ative:

   ```bash
   source .venv/bin/activate
   ```

2. **Instale as dependências:**

   ```bash
   pip install -r requirements.txt
   ```

3. **Rode a aplicação:**

   ```bash
   streamlit run app.py
   ```

4. O Streamlit abre automaticamente no navegador em `http://localhost:8501`. Se não abrir sozinho, acesse esse endereço manualmente.

Para parar o servidor, use `Ctrl+C` no terminal.

## Estrutura do projeto

```
app.py                  # Ponto de entrada — só monta a navegação e delega para src/
src/
├── core/                # Regras de negócio (Polars + DuckDB)
│   ├── deduplicacao.py      # Módulo A — deduplicação e matrícula mais recente
│   ├── etapa_de_ensino.py   # Módulo B — motor de etapa de ensino
│   ├── movimentacao.py      # Módulo C — discrepâncias e movimentação
│   ├── frequencia.py        # Módulo D — cálculo de frequência
│   └── exportacao.py        # Módulo E — exportação padrão MEC
├── schemas/              # Modelos Pydantic (validação de entrada/saída)
├── ui/                    # Componentes visuais reutilizáveis (upload, botões de download)
└── utils/                 # Funções de higienização (CPF, texto) usadas em todo o sistema
```

## Módulos implementados

A navegação (menu lateral) tem os seguintes módulos:

| Módulo | O que faz |
|---|---|
| 🏠 Início | Teste rápido do ambiente — upload de planilha para conferir leitura, Polars, DuckDB e higienização |
| 🔁 A. Deduplicação e Matrícula Mais Recente | Mantém 1 registro por CPF (o mais recente) |
| 🎓 B. Motor de Etapa de Ensino | Calcula `ESTUDANTE_ETAPA_DE_ENSINO` a partir de Oferta + Série |
| 🔀 C. Discrepâncias e Movimentação | Compara base Antiga x Nova e classifica transferências/novos cadastros |
| 📅 D. Cálculo de Frequência | Cruza base SGP com base SIGEDUC e corrige a frequência |
| 📤 E. Exportação Padrão MEC | Força o layout oficial de colunas para reenvio ao SGP |

Cada módulo valida as colunas recebidas e mostra um erro claro se alguma estiver faltando.

## Sobre os dados

⚠️ As planilhas processadas contêm dados pessoais de alunos (CPF, nome, data de nascimento). Por isso, `*.xlsx`, `*.xls`, `*.csv` e a pasta `/dados/` estão no `.gitignore` — **nunca devem ir parar no repositório Git**. Use apenas planilhas locais para testes e uploads via interface.

## Formatos de entrada aceitos

`.csv`, `.xlsx` e `.xls`. O leitor de planilhas (`src/ui/uploader.py`) trata automaticamente:

- Codificação UTF-8 com fallback para Latin-1 (exports antigos do SIGEDUC/SGP)
- Separador `,` ou `;` (detectado automaticamente no CSV)
- Todas as colunas são lidas como texto, para não perder zeros à esquerda em CPF/códigos
