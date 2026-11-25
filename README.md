# 💰 Assistente Financeiro Pessoal no Telegram
LINK: https://t.me/SeuAmigo_SaldoBot

Bot de controle financeiro doméstico para Telegram que registra despesas a partir de **texto, áudio ou foto de notas fiscais** e responde perguntas em linguagem natural sobre seus gastos, utilizando os modelos gratuitos da **Groq** e banco de dados local **SQLite**.

![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white)
![Telegram](https://img.shields.io/badge/Telegram-Bot%20API-26A5E4?logo=telegram&logoColor=white)
![Groq](https://img.shields.io/badge/AI-Groq%20API-F55036)
![SQLite](https://img.shields.io/badge/Database-SQLite-003B57?logo=sqlite&logoColor=white)

---

## ✨ Funcionalidades

- **📝 Registro Multimodal de Gastos:**
  - **Texto:** Envie mensagens naturais como *"Almoço 42,50 no pix"* ou *"Abasteci 150 reais ontem no crédito"*.
  - **Áudio:** Grave mensagens de voz; o bot transcreve automaticamente via **Whisper** (`whisper-large-v3-turbo`) e extrai os dados.
  - **Fotos de Cupons Fiscais:** Envie a foto da nota ou comprovante (com ou sem legenda) para leitura automática do valor total, data e estabelecimento.
- **🔎 Consultas em Linguagem Natural (Text-to-SQL):**
  - Faça perguntas livres como *"Quanto gastei com alimentação este mês?"* ou *"Quais foram meus gastos nos últimos 15 dias?"*.
  - A IA converte a pergunta em uma consulta SQL executada em conexão **estritamente somente leitura (`mode=ro`)**, garantindo integridade total dos dados.
- **📊 Resumo Visual Mensal (`/resumo`):**
  - Exibe a distribuição de gastos do mês por categoria com valores formatados, percentuais e barras de progresso diretamente no chat.
- **🔄 Detecção Automática de Modelos:**
  - Ao iniciar, o bot consulta os modelos ativos na sua conta Groq e seleciona automaticamente as melhores opções disponíveis para texto, visão e áudio.
- **🔒 Privacidade e Segurança:**
  - Responde exclusivamente ao ID do usuário autorizado (`ALLOWED_USER_ID`) e mantém todo o histórico financeiro em um arquivo local (`financas.db`).

---

## 🛠️ Tecnologias Utilizadas

- **[Python 3.10+](https://www.python.org/)**
- **[python-telegram-bot](https://python-telegram-bot.org/)** — Comunicação assíncrona com a API do Telegram
- **[OpenAI Python SDK](https://github.com/openai/openai-python)** — Cliente configurado para os endpoints da **Groq API**
- **[Pydantic](https://docs.pydantic.dev/)** — Validação estruturada dos dados extraídos pela IA
- **[SQLite3](https://www.sqlite.org/)** — Persistência local leve e sem necessidade de servidor externo
- **[python-dotenv](https://github.com/theskumar/python-dotenv)** — Gerenciamento seguro de credenciais

---

## 📁 Estrutura do Projeto

```text
├── bot.py              # Lógica principal, handlers do Telegram, IA e consultas SQLite
├── requirements.txt    # Dependências do projeto
├── .env.example        # Modelo de configuração das variáveis de ambiente
├── .gitignore          # Impede o envio de senhas e do banco pessoal ao GitHub
└── README.md           # Documentação do projeto
