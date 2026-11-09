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
│   ├── movimentacao.py      # Módulo C — reconciliação SGP atual x SIGEDUC (Cadastro + Movimentação)
│   ├── frequencia.py        # Módulo D — cálculo de frequência
│   ├── exportacao.py        # Módulo E — exportação padrão MEC
│   ├── situacao_aluno.py    # Módulo F — situação de matrícula e aprovações (com base de destino)
│   ├── situacao_sigeduc.py  # Módulo G — situação de matrícula direto do SIGEDUC (sem base de destino)
│   ├── comparacao_situacao.py  # Módulo H — divergências de situação SIGEDUC × SGP
│   ├── comparacao_serie.py     # Módulo I — divergências de série/ano SIGEDUC × SGP
│   ├── ajuste_tercos_eja.py    # Módulo J — ajuste do 1º e do 3º terço EJA
│   ├── exclusao_movimentacao.py  # Módulo K — SGP sem CPFs da movimentação
│   ├── cadastro_proxima_matricula.py  # Módulo L — cadastro da matrícula seguinte
│   └── reconciliacao_eja.py  # Módulo M — histórico EJA e próxima ação no SGP
├── schemas/              # Modelos Pydantic (validação de entrada/saída)
├── ui/                    # Componentes visuais reutilizáveis (upload, botões de download)
└── utils/                 # Funções de higienização (CPF, texto) usadas em todo o sistema
```

## Módulos implementados

A navegação (menu lateral) tem os seguintes módulos:

| Módulo | O que faz |
|---|---|
| 🏠 Início | Teste rápido do ambiente — upload de planilha para conferir leitura, Polars, DuckDB e higienização |
| 🔁 A. Deduplicação e Matrícula Mais Recente | Mantém 1 registro por CPF pela data de referência mais recente: encerramento da linha ou, se vazio, atualização do cadastro. Em empate, prefere encerramento. Sem ambas, prefere data de alocação, depois matrícula mais recente e alocação mais recente em empate. As colunas de data são selecionáveis. |
| 🎓 B. Motor de Etapa de Ensino | Calcula `ESTUDANTE_ETAPA_DE_ENSINO` a partir de Oferta + Série |
| 🔀 C. Discrepâncias e Movimentação | Cruza a base SGP atual com uma ou mais planilhas do SIGEDUC e gera dois relatórios: **Cadastro** (CPFs do SIGEDUC ainda não cadastrados no SGP, já formatados nas 24 colunas oficiais) e **Movimentação** (CPFs já cadastrados, classificados em `trans. escola`, `trans. mod` ou `cancelado` conforme o que o SIGEDUC mostra hoje) |
| 📅 D. Cálculo de Frequência | Cruza base SGP com base SIGEDUC e corrige a frequência |
| 📤 E. Exportação Padrão MEC | Força o layout oficial de colunas para reenvio ao SGP |
| ✅ F. Situação de Matrícula e Aprovações | Cruza SIGEDUC e destino por CPF e data de matrícula. Preenche a situação e as datas da matrícula correspondente, mantém a contagem de módulos aprovados por CPF e anexa data e situação da matrícula posterior mais recente, quando houver. |
| 📥 G. Situação de Matrícula (Somente SIGEDUC) | Gera a situação mais recente + contagem de módulos aprovados direto do SIGEDUC (linha completa por CPF), sem cruzar com nenhuma base de destino |
| 🔎 H. Comparar Situação SIGEDUC × SGP | Seleciona CPF, data e situação em cada planilha, além da série/ano no SIGEDUC; compara apenas matrículas com CPF **e data de matrícula iguais** nas duas bases e exibe situações divergentes. O XLSX preserva todas as colunas do SGP e acrescenta as informações da comparação ao final. `APROVADO` no 3º/4º ano corresponde ao código 11. |
| 📚 I. Comparar Série/Ano SIGEDUC × SGP | Cruza os CPFs das duas bases e compara as colunas de série/ano selecionadas. Datas de matrícula opcionais resolvem CPFs repetidos. Mostra apenas séries diferentes e exporta XLSX com todas as colunas do SGP, CPF normalizado e as duas séries ao final. |
| 🧩 J. Ajuste de Terços EJA | Recebe histórico SIGEDUC, alunos do 1º terço com dados corretos e cadastro. Permite escolher as colunas de CPF, datas, situação e terço. Ordena as aprovações em módulo pelo encerramento e gera dois arquivos: dados corretos com matrícula da 1ª e encerramento da 2ª aprovação; cadastro com matrícula e encerramento da 3ª aprovação. Preserva as colunas originais de cada arquivo. |
| 🧹 K. Retirar Movimentações do SGP | Recebe planilhas do SGP e de movimentação, com seleção da coluna de CPF em cada uma. Remove do SGP todas as linhas cujos CPFs constam na movimentação e exporta a planilha resultante com as colunas originais. |
| 📥 L. Cadastro da Próxima Matrícula | Recebe o resultado do módulo F e o histórico SIGEDUC. Para cada CPF e data da base, busca a primeira matrícula posterior cuja situação do aluno seja `MATRICULADO` e gera o cadastro no mesmo layout do módulo C. As colunas de identificação, data, situação, oferta e série são selecionáveis. |
| 🔄 M. Reconciliação Completa EJA | Compara cada matrícula do SGP com CPF, data, escola e terço do SIGEDUC. Usa todo o histórico para contar aprovações, mas corrige apenas da data de início em diante. Gera uma próxima matrícula por CPF e rodada, movimentações e diagnóstico das datas faltantes ou excedentes. |

### Uso do módulo M

1. Envie o SGP atual, o histórico SGP de 2025 e 2026 e o SIGEDUC bruto com as matrículas do mesmo período. Opcionalmente, envie uma planilha de CPFs para limitar a análise, como o resultado do módulo F ou a planilha de situações do 2º terço. Selecione a data e o terço da matrícula a corrigir na planilha de foco, quando disponíveis. Se a data do SGP ainda pertence a um terço anterior, o módulo localiza no SIGEDUC a primeira matrícula do terço indicado e começa a correção nela. Por exemplo, uma matrícula lançada em 10/01/2026 no 2º terço, quando essa data ainda é do 1º, é substituída pela matrícula de 28/05/2026 no 2º. A data antiga continua contando aprovações, mas não gera cadastro nesta sequência.
2. Confira ou selecione as colunas de CPF, data, terço, situação, ID, escola, oferta e série. Execute a análise e examine o diagnóstico antes de importar.
3. Importe primeiro a movimentação, que pode conter código 1 para derrubar uma matrícula incorreta e resultados das matrículas já existentes. Em seguida, importe o cadastro da primeira data pendente do terço que está sendo corrigido. O cadastro inclui a última coluna `SITUACAO_ALUNO` para conferência; remova essa coluna antes de importar no SGP, como no módulo C.
4. Quando a matrícula atual só precisa de resultado e há uma matrícula posterior pendente, o módulo gera a movimentação da atual e o cadastro da próxima data na mesma rodada. Importe a movimentação antes do cadastro. Baixe novamente o SGP atual e o histórico para obter o novo ID; execute o módulo para gerar o resultado da matrícula cadastrada. A sequência continua até todas as matrículas desde a data de início terem data, escola, terço e resultado correspondentes no SGP.

As aprovações de 2025 continuam contando em 2026: as duas primeiras ficam no 1º terço, a terceira no 2º e a quarta no 3º, como concluinte. `APROVADO` e `APROVADO EM MÓDULO` contam como aprovação; reprovação, frequência, abandono e outras situações não avançam o terço. Transferências sem código de resultado confirmado aparecem para conferência; cancelamentos usam o texto `cancelado` na coluna de situação; a matrícula ainda pode ser cadastrada. Resultados sem encerramento também exigem conferência.

Quando a matrícula atual do SGP já está no 3º terço e coincide em CPF, data e escola com a última matrícula aprovada do SIGEDUC, o módulo gera a movimentação de conclusão (código 11), mesmo que o histórico SIGEDUC enviado não contenha todas as aprovações anteriores. Exige data de encerramento e não gera cadastro posterior. Matrículas com datas posteriores no SIGEDUC continuam na sequência normal de reconciliação.

Para a planilha de foco `APROVADOS-3TERCO.xlsx`, a opção **Corrigir as matrículas pendentes desta planilha de foco em ordem** fica marcada inicialmente. Ela usa `SITUACAO_MATRICULA_RECENTE` e `DATA_CONCLUSAO_ENSINO_MEDIO`, cujas colunas podem ser selecionadas. Se existe matrícula posterior no SIGEDUC e a matrícula atual está no terço errado, o módulo derruba a atual com código 1 e cadastra a mesma data no terço calculado pelas aprovações. Após nova exportação do SGP, informa o resultado dessa matrícula e prepara o cadastro da próxima data, quando houver. Se a matrícula atual já tem data e terço corretos, gera seu resultado e o cadastro da próxima data juntos. A transferência para outra escola seguida imediatamente por aprovação derruba a matrícula atual e cadastra a matrícula aprovada na escola de destino. Se não existe matrícula posterior, a aprovação da matrícula atual pode gerar código 11 sem cadastro. Se existem apenas matrículas posteriores sem aprovação, uma aprovação anterior confirmada pela data de conclusão do foco também pode encerrar o 3º terço, como no CPF 04977746406. Para outras planilhas de foco, a opção começa desmarcada.

Cada módulo valida as colunas recebidas e mostra um erro claro se alguma estiver faltando.

## Sobre os dados

⚠️ As planilhas processadas contêm dados pessoais de alunos (CPF, nome, data de nascimento). Por isso, `*.xlsx`, `*.xls`, `*.csv` e a pasta `/dados/` estão no `.gitignore` — **nunca devem ir parar no repositório Git**. Use apenas planilhas locais para testes e uploads via interface.

## Formatos de entrada aceitos

`.csv`, `.xlsx` e `.xls`. O leitor de planilhas (`src/ui/uploader.py`) trata automaticamente:

- Codificação UTF-8 com fallback para Latin-1 (exports antigos do SIGEDUC/SGP)
- Separador `,` ou `;` (detectado automaticamente no CSV)
- Todas as colunas são lidas como texto, para não perder zeros à esquerda em CPF/códigos
