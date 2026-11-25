import os
import io
import re
import html
import json
import base64
import sqlite3
from pathlib import Path
from datetime import datetime
from typing import Literal
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from openai import OpenAI
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

load_dotenv()

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]
ALLOWED_USER_ID = int(os.environ["ALLOWED_USER_ID"])
DB_PATH = "financas.db"

client = OpenAI(
    api_key=GROQ_API_KEY,
    base_url="https://api.groq.com/openai/v1",
)

ICONES_CATEGORIA = {
    "Alimentação": "🍔",
    "Moradia": "🏠",
    "Transporte": "🚗",
    "Saúde": "💊",
    "Lazer": "🎬",
    "Contas Fixas": "💡",
    "Outros": "📦",
}


def descobrir_modelos() -> tuple[str, str, str]:
    modelos = [m.id for m in client.models.list().data]
    print("Modelos disponíveis na sua conta Groq:", modelos)

    audios = [m for m in modelos if "whisper" in m.lower()]
    modelo_audio = next(
        (m for m in audios if "turbo" in m.lower()),
        audios[0] if audios else "whisper-large-v3-turbo",
    )

    bloqueados = ("whisper", "guard", "tts", "embed", "safeguard", "orpheus", "playai", "compound")
    textos = [m for m in modelos if not any(b in m.lower() for b in bloqueados)]

    preferidos_texto = ("llama-3.3", "llama-3.1-70b", "llama-4", "qwen", "gpt-oss", "gemma")
    modelo_texto = next(
        (m for pref in preferidos_texto for m in textos if pref in m.lower()),
        textos[0] if textos else modelos[0],
    )

    preferidos_visao = ("vision", "scout", "maverick", "pixtral", "vl", "gemma-3")
    modelo_visao = next(
        (m for pref in preferidos_visao for m in textos if pref in m.lower()),
        modelo_texto,
    )

    print(f"Selecionados -> Texto: {modelo_texto} | Visão: {modelo_visao} | Áudio: {modelo_audio}")
    return modelo_texto, modelo_visao, modelo_audio


MODELO_TEXTO, MODELO_VISAO, MODELO_AUDIO = descobrir_modelos()


# Helpers de formatação visual
def formatar_moeda(valor: float) -> str:
    texto = f"{valor:,.2f}"
    return "R$ " + texto.replace(",", "X").replace(".", ",").replace("X", ".")


def formatar_data_br(data_iso: str) -> str:
    try:
        return datetime.strptime(data_iso, "%Y-%m-%d").strftime("%d/%m/%Y")
    except ValueError:
        return data_iso


def barra_progresso(percentual: float, tamanho: int = 10) -> str:
    cheios = max(0, min(tamanho, round((percentual / 100) * tamanho)))
    return "▰" * cheios + "▱" * (tamanho - cheios)


async def enviar_html(
    update: Update,
    mensagem_html: str,
    reply_markup: InlineKeyboardMarkup | None = None,
):
    """Envia mensagem em HTML com fallback automático para texto puro se houver tag inválida."""
    try:
        await update.message.reply_text(
            mensagem_html,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup,
        )
    except BadRequest:
        texto_limpo = re.sub(r"<[^>]+>", "", mensagem_html)
        await update.message.reply_text(
            html.unescape(texto_limpo),
            reply_markup=reply_markup,
        )


# 1. Esquema unificado de intenção
class AnaliseMensagem(BaseModel):
    intencao: Literal["registrar_gasto", "consultar_gastos", "outro"] = Field(
        description=(
            "'registrar_gasto' se o usuário informa uma compra/despesa ou envia nota fiscal; "
            "'consultar_gastos' se pergunta sobre valores gastos, totais, médias ou histórico; "
            "'outro' caso contrário."
        )
    )
    descricao: str | None = Field(
        default=None, description="Resumo curto do estabelecimento ou item principal."
    )
    valor: float | None = Field(
        default=None, description="Valor total numérico da despesa em reais."
    )
    categoria: Literal[
        "Alimentação",
        "Moradia",
        "Transporte",
        "Saúde",
        "Lazer",
        "Contas Fixas",
        "Outros",
    ] | None = None
    pagamento: Literal[
        "Crédito", "Débito", "Pix", "Dinheiro", "Não informado"
    ] | None = None
    data: str | None = Field(
        default=None, description="Data do gasto no formato YYYY-MM-DD."
    )
    sql_consulta: str | None = Field(
        default=None,
        description="Consulta SQLite SELECT válida na tabela 'gastos' para responder à pergunta.",
    )


# 2. Banco de dados SQLite
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS gastos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                data TEXT NOT NULL,
                descricao TEXT NOT NULL,
                valor REAL NOT NULL,
                categoria TEXT NOT NULL,
                pagamento TEXT NOT NULL,
                criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)


def salvar_gasto(dados: AnaliseMensagem) -> int:
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            """
            INSERT INTO gastos (data, descricao, valor, categoria, pagamento)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                dados.data,
                dados.descricao,
                dados.valor,
                dados.categoria or "Outros",
                dados.pagamento or "Não informado",
            ),
        )
        return cursor.lastrowid or 0


def listar_ultimos_gastos(limite: int = 10) -> list[tuple]:
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            """
            SELECT id, data, descricao, valor, categoria
            FROM gastos
            ORDER BY data DESC, id DESC
            LIMIT ?
            """,
            (limite,),
        )
        return cursor.fetchall()


def excluir_gasto_por_id(gasto_id: int) -> tuple | None:
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            "SELECT id, data, descricao, valor, categoria FROM gastos WHERE id = ?",
            (gasto_id,),
        )
        registro = cursor.fetchone()
        if registro:
            conn.execute("DELETE FROM gastos WHERE id = ?", (gasto_id,))
        return registro


def limpar_todos_gastos() -> int:
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute("DELETE FROM gastos")
        total_removido = cursor.rowcount
        try:
            conn.execute("DELETE FROM sqlite_sequence WHERE name='gastos'")
        except sqlite3.OperationalError:
            pass
        return total_removido


def resumo_mes_atual() -> list[tuple[str, float]]:
    mes_atual = datetime.now().strftime("%Y-%m")
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            """
            SELECT categoria, SUM(valor) as total
            FROM gastos
            WHERE data LIKE ?
            GROUP BY categoria
            ORDER BY total DESC
            """,
            (f"{mes_atual}%",),
        )
        return cursor.fetchall()


def executar_consulta_segura(sql: str) -> list[dict]:
    sql_limpo = sql.strip().rstrip(";")

    if not sql_limpo.upper().startswith("SELECT"):
        raise ValueError("Apenas consultas SELECT são permitidas.")

    if re.search(
        r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|REPLACE|ATTACH|DETACH|PRAGMA|VACUUM)\b",
        sql_limpo,
        re.IGNORECASE,
    ):
        raise ValueError("Comando SQL não autorizado na consulta.")

    uri_somente_leitura = f"{Path(DB_PATH).resolve().as_uri()}?mode=ro"
    with sqlite3.connect(uri_somente_leitura, uri=True) as conn:
        cursor = conn.execute(sql_limpo)
        colunas = [desc[0] for desc in cursor.description] if cursor.description else []
        return [dict(zip(colunas, linha)) for linha in cursor.fetchall()]


def _limpar_json(resposta_bruta: str) -> str:
    sem_pensamento = re.sub(r"<think>.*?</think>", "", resposta_bruta, flags=re.DOTALL)
    match = re.search(r"\{.*\}", sem_pensamento, re.DOTALL)
    return match.group(0) if match else sem_pensamento


# 3. Inteligência via Groq
def analisar_mensagem_com_ia(
    texto_usuario: str | None = None,
    imagem_bytes: bytes | None = None,
) -> AnaliseMensagem:
    hoje = datetime.now().strftime("%Y-%m-%d")

    instrucoes = f"""Data de referência hoje: {hoje}.
Você é um assistente financeiro doméstico conectado a um banco SQLite.
Analise a entrada do usuário e responda APENAS com um objeto JSON válido contendo:
- "intencao": "registrar_gasto", "consultar_gastos" ou "outro"
- "descricao": string ou null
- "valor": float ou null (ex: 45.90)
- "categoria": "Alimentação", "Moradia", "Transporte", "Saúde", "Lazer", "Contas Fixas", "Outros" ou null
- "pagamento": "Crédito", "Débito", "Pix", "Dinheiro", "Não informado" ou null
- "data": "YYYY-MM-DD" ou null
- "sql_consulta": string SQL ou null

REGRAS PARA 'registrar_gasto':
- Preencha descricao, valor, categoria, pagamento e data (calcule datas relativas como 'ontem' usando hoje={hoje}).
- Deixe sql_consulta como null.

REGRAS PARA 'consultar_gastos':
- Gere uma query SQLite SELECT na tabela 'gastos' (colunas: id, data, descricao, valor, categoria, pagamento).
- Valores exatos de categoria: 'Alimentação', 'Moradia', 'Transporte', 'Saúde', 'Lazer', 'Contas Fixas', 'Outros'.
- Valores exatos de pagamento: 'Crédito', 'Débito', 'Pix', 'Dinheiro', 'Não informado'.
- Para períodos relativos (ex: 'últimos 15 dias', 'semana passada', 'mês passado'), use funções de data do SQLite tomando '{hoje}' como base (ex: date('{hoje}', '-15 days')).
- Para buscar termos na descrição (ex: 'Uber', 'mercado', 'farmácia'), use LOWER(descricao) LIKE '%termo%'.
- Use COALESCE(SUM(valor), 0) ao somar valores e limite listagens detalhadas a LIMIT 30."""

    if imagem_bytes:
        img_b64 = base64.b64encode(imagem_bytes).decode("utf-8")
        legenda = (
            f"Legenda do usuário: {texto_usuario}"
            if texto_usuario
            else "Extraia os dados deste comprovante ou nota fiscal para registrar o gasto."
        )
        response = client.chat.completions.create(
            model=MODELO_VISAO,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": f"{instrucoes}\n\n{legenda}"},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"},
                        },
                    ],
                }
            ],
            temperature=0.1,
        )
    else:
        response = client.chat.completions.create(
            model=MODELO_TEXTO,
            messages=[
                {"role": "system", "content": instrucoes},
                {"role": "user", "content": texto_usuario or ""},
            ],
            temperature=0.1,
        )

    conteudo_json = _limpar_json(response.choices[0].message.content or "")
    return AnaliseMensagem.model_validate_json(conteudo_json)


def formular_resposta_consulta(pergunta: str, resultados: list[dict]) -> str:
    prompt = f"""Pergunta do usuário: '{pergunta}'
Dados retornados do banco financeiro (JSON): {json.dumps(resultados, ensure_ascii=False)}

Formate a resposta para o Telegram usando EXCLUSIVAMENTE HTML simples suportado pelo Telegram: <b>, <i>, <code> e <blockquote>.
PROIBIDO usar Markdown (NUNCA use **, *, ###, --- ou crases `).
Escape caracteres como & usando &amp;.

Regras de visual:
1. Formate todos os valores em reais com vírgula dentro de <code> (ex: <code>R$ 145,50</code>).
2. Converta datas YYYY-MM-DD para DD/MM/YYYY.
3. Use o emoji correspondente à categoria: 🍔 Alimentação, 🏠 Moradia, 🚗 Transporte, 💊 Saúde, 🎬 Lazer, 💡 Contas Fixas, 📦 Outros.
4. Se houver lista de gastos, agrupe por data dentro de blocos <blockquote> no formato:
📅 <b>DD/MM/YYYY</b>
<blockquote>🍔 <b>Descrição</b> • <code>R$ 00,00</code> <i>(Categoria • Pagamento)</i></blockquote>
E finalize fora do blockquote com:
💵 <b>Total do período:</b> <code>R$ 00,00</code>
5. Se for apenas uma resposta de valor único (ex: quanto gastei este mês), responda de forma direta e elegante, por exemplo:
💸 <b>Gasto no período:</b> <code>R$ 927,99</code>
6. Se a lista vier vazia ou soma for 0, responda: 🔍 <i>Nenhum gasto encontrado para esse período ou filtro.</i>"""

    response = client.chat.completions.create(
        model=MODELO_TEXTO,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
    )
    return response.choices[0].message.content or "🔍 <i>Não consegui gerar a resposta.</i>"


def transcrever_audio(audio_bytes: bytes) -> str:
    arquivo_memoria = io.BytesIO(audio_bytes)
    arquivo_memoria.name = "audio.ogg"
    transcricao = client.audio.transcriptions.create(
        model=MODELO_AUDIO,
        file=arquivo_memoria,
        language="pt",
    )
    return transcricao.text


# 4. Handlers do Telegram
def autorizado(update: Update) -> bool:
    return (
        update.effective_user is not None
        and update.effective_user.id == ALLOWED_USER_ID
    )


async def processar_resultado_ia(
    update: Update, analise: AnaliseMensagem, texto_original: str = ""
):
    if analise.intencao == "registrar_gasto":
        if not analise.valor or analise.valor <= 0 or not analise.descricao:
            await enviar_html(
                update,
                "⚠️ <i>Não identifiquei um valor válido para registrar esse gasto.</i>",
            )
            return

        gasto_id = salvar_gasto(analise)
        categoria = analise.categoria or "Outros"
        pagamento = analise.pagamento or "Não informado"
        icone = ICONES_CATEGORIA.get(categoria, "📦")
        desc_segura = html.escape(analise.descricao)
        valor_fmt = formatar_moeda(analise.valor)
        data_fmt = formatar_data_br(analise.data or "")

        card = (
            f"✅ <b>Gasto Registrado</b>\n"
            f"<blockquote>{icone} <b>{desc_segura}</b>\n"
            f"💰 Valor: <code>{valor_fmt}</code>\n"
            f"🏷️ Categoria: {html.escape(categoria)}\n"
            f"💳 Pagamento: {html.escape(pagamento)}\n"
            f"📅 Data: {data_fmt}</blockquote>"
        )
        botao_desfazer = InlineKeyboardMarkup(
            [[InlineKeyboardButton("🗑️ Desfazer lançamento", callback_data=f"del_{gasto_id}")]]
        )
        await enviar_html(update, card, reply_markup=botao_desfazer)

    elif analise.intencao == "consultar_gastos":
        if not analise.sql_consulta:
            await enviar_html(
                update,
                "⚠️ <i>Não consegui montar a consulta para essa pergunta. Tente reformular.</i>",
            )
            return

        resultados = executar_consulta_segura(analise.sql_consulta)
        resposta_final = formular_resposta_consulta(texto_original, resultados)
        await enviar_html(update, resposta_final)

    else:
        dica = (
            "💡 <b>Comandos e exemplos:</b>\n"
            "<blockquote>• <b>Registrar:</b> <i>\"Almoço 38,50 no pix\"</i> ou foto de cupom\n"
            "• <b>Consultar:</b> <i>\"Quanto gastei com alimentação este mês?\"</i>\n"
            "• /resumo — Consolidado visual do mês\n"
            "• /excluir — Apagar um gasto específico\n"
            "• /limpar — Zerar todo o histórico</blockquote>"
        )
        await enviar_html(update, dica)


async def comando_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not autorizado(update):
        return
    boas_vindas = (
        "💼 <b>Assistente Financeiro Ativo</b>\n"
        "<blockquote>📝 Envie gastos por <b>texto</b>, <b>áudio</b> ou <b>foto</b> de nota fiscal.\n"
        "🔎 Pergunte qualquer coisa sobre seus gastos em linguagem natural.\n"
        "📊 /resumo — Distribuição visual do mês\n"
        "🗑️ /excluir — Remover um item específico\n"
        "🧹 /limpar — Apagar todo o histórico</blockquote>"
    )
    await enviar_html(update, boas_vindas)


async def comando_resumo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not autorizado(update):
        return

    linhas = resumo_mes_atual()
    if not linhas:
        await enviar_html(update, "📭 <i>Nenhum gasto registrado neste mês ainda.</i>")
        return

    total_geral = sum(valor for _, valor in linhas)
    mes_ref = datetime.now().strftime("%m/%Y")
    blocos = [f"📊 <b>Resumo Financeiro ({mes_ref})</b>\n"]

    itens_card = []
    for categoria, total in linhas:
        pct = (total / total_geral * 100) if total_geral > 0 else 0
        icone = ICONES_CATEGORIA.get(categoria, "📦")
        barra = barra_progresso(pct)
        itens_card.append(
            f"{icone} <b>{html.escape(categoria)}</b> — {pct:.0f}%\n"
            f"<code>{formatar_moeda(total)}</code>  {barra}"
        )

    blocos.append("<blockquote>" + "\n\n".join(itens_card) + "</blockquote>")
    blocos.append(f"\n💵 <b>Total Geral:</b> <code>{formatar_moeda(total_geral)}</code>")

    await enviar_html(update, "\n".join(blocos))


async def comando_excluir(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not autorizado(update):
        return

    ultimos = listar_ultimos_gastos(10)
    if not ultimos:
        await enviar_html(update, "📭 <i>Não há nenhum gasto registrado para excluir.</i>")
        return

    botoes = []
    for gasto_id, data, desc, valor, categoria in ultimos:
        icone = ICONES_CATEGORIA.get(categoria, "📦")
        resumo_desc = desc[:20] + "…" if len(desc) > 20 else desc
        texto_botao = f"{icone} {formatar_data_br(data)[:5]} • {resumo_desc} ({formatar_moeda(valor)})"
        botoes.append([InlineKeyboardButton(texto_botao, callback_data=f"del_{gasto_id}")])

    botoes.append([InlineKeyboardButton("❌ Cancelar", callback_data="cancelar_acao")])

    await enviar_html(
        update,
        "🗑️ <b>Excluir Gasto Específico</b>\n"
        "<blockquote>Toque no lançamento abaixo que você deseja remover permanentemente:</blockquote>",
        reply_markup=InlineKeyboardMarkup(botoes),
    )


async def comando_limpar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not autorizado(update):
        return

    teclado = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("⚠️ Sim, apagar tudo", callback_data="confirmar_limpar"),
                InlineKeyboardButton("❌ Cancelar", callback_data="cancelar_acao"),
            ]
        ]
    )
    await enviar_html(
        update,
        "🚨 <b>Atenção: Apagar Todo o Histórico</b>\n"
        "<blockquote>Isso removerá <b>todos</b> os gastos salvos no banco de dados permanentemente. Deseja continuar?</blockquote>",
        reply_markup=teclado,
    )


async def tratar_botoes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    if not autorizado(update):
        await query.answer("Acesso não autorizado.", show_alert=True)
        return

    await query.answer()
    acao = query.data or ""

    if acao.startswith("del_"):
        gasto_id = int(acao.split("_", 1)[1])
        removido = excluir_gasto_por_id(gasto_id)
        if removido:
            _, data, desc, valor, categoria = removido
            icone = ICONES_CATEGORIA.get(categoria, "📦")
            await query.edit_message_text(
                f"🗑️ <b>Gasto excluído</b>\n"
                f"<blockquote>{icone} <s>{html.escape(desc)}</s> • <code>{formatar_moeda(valor)}</code>\n"
                f"📅 Data: {formatar_data_br(data)}</blockquote>",
                parse_mode=ParseMode.HTML,
            )
        else:
            await query.edit_message_text(
                "⚠️ <i>Esse gasto já foi excluído anteriormente.</i>",
                parse_mode=ParseMode.HTML,
            )

    elif acao == "confirmar_limpar":
        qtd = limpar_todos_gastos()
        await query.edit_message_text(
            f"🧹 <b>Histórico zerado com sucesso!</b>\n"
            f"<blockquote>Foram removidos <b>{qtd}</b> registro(s) do banco de dados.</blockquote>",
            parse_mode=ParseMode.HTML,
        )

    elif acao == "cancelar_acao":
        await query.edit_message_text(
            "✅ <i>Operação cancelada. Nenhum dado foi alterado.</i>",
            parse_mode=ParseMode.HTML,
        )


async def processar_texto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not autorizado(update) or not update.message or not update.message.text:
        return

    await update.message.chat.send_action(ChatAction.TYPING)
    try:
        texto = update.message.text
        analise = analisar_mensagem_com_ia(texto_usuario=texto)
        await processar_resultado_ia(update, analise, texto_original=texto)
    except Exception as e:
        await update.message.reply_text(f"Erro ao processar mensagem: {e}")


async def processar_foto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not autorizado(update) or not update.message or not update.message.photo:
        return

    await update.message.chat.send_action(ChatAction.TYPING)
    try:
        foto = update.message.photo[-1]
        arquivo = await foto.get_file()
        dados_bytes = bytes(await arquivo.download_as_bytearray())

        analise = analisar_mensagem_com_ia(
            texto_usuario=update.message.caption,
            imagem_bytes=dados_bytes,
        )
        await processar_resultado_ia(update, analise)
    except Exception as e:
        await update.message.reply_text(f"Erro ao processar imagem: {e}")


async def processar_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not autorizado(update) or not update.message:
        return

    midia = update.message.voice or update.message.audio
    if not midia:
        return

    await update.message.chat.send_action(ChatAction.TYPING)
    try:
        arquivo = await midia.get_file()
        dados_bytes = bytes(await arquivo.download_as_bytearray())

        texto_transcrito = transcrever_audio(dados_bytes)
        analise = analisar_mensagem_com_ia(texto_usuario=texto_transcrito)
        await processar_resultado_ia(update, analise, texto_original=texto_transcrito)
    except Exception as e:
        await update.message.reply_text(f"Erro ao processar áudio: {e}")


if __name__ == "__main__":
    init_db()
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", comando_start))
    app.add_handler(CommandHandler("resumo", comando_resumo))
    app.add_handler(CommandHandler("excluir", comando_excluir))
    app.add_handler(CommandHandler("limpar", comando_limpar))
    app.add_handler(CallbackQueryHandler(tratar_botoes))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, processar_texto))
    app.add_handler(MessageHandler(filters.PHOTO, processar_foto))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, processar_audio))

    print("Bot financeiro rodando com opções de exclusão...")
    app.run_polling()