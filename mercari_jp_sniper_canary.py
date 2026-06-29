"""
mercari_jp_sniper_canary.py

Versão avançada com concorrência assíncrona real (asyncio) para Mercari Japão (JP).
O painel Rich, o motor do Mercari e o bot do Telegram rodam cooperativamente.
Integração de Filtro Estatístico de Média Móvel, tradução JA->PT, conversão JPY->BRL e IA (NLP).
"""

import os
import sys
import time
import json
import sqlite3
import asyncio
import random
import joblib
from bs4 import BeautifulSoup
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from curl_cffi import requests as curl_requests
from dotenv import load_dotenv

# Biblioteca de tradução e requisições normais
from deep_translator import GoogleTranslator
import requests as normal_requests

# Imports do Rich para a interface visual
from rich.live import Live
from rich.panel import Panel
from rich.console import Console
from rich.text import Text

# Carrega as configurações do arquivo .env local
load_dotenv()

# ==============================================================================
# CONFIGURAÇÕES GERAIS
# ==============================================================================
TELEGRAM_TOKEN_MERCARI = os.getenv("TELEGRAM_TOKEN_MERCARI")
USUARIO_PERMITIDO_STR = os.getenv("USUARIO_PERMITIDO")

if not TELEGRAM_TOKEN_MERCARI or not USUARIO_PERMITIDO_STR:
    print("\n[CRÍTICO] Erro: Variáveis de ambiente não encontradas no arquivo .env!")
    sys.exit(1)

USUARIO_PERMITIDO = int(USUARIO_PERMITIDO_STR)
DB_PATH = "mercari_jp_dados_canary.db"

# Variável Global para armazenar a cotação do Iene para Real
COTACAO_JPY_BRL = 0.035  # Valor padrão de fallback

HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "accept-language": "ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7",
    "referer": "https://jp.mercari.com/",
    "sec-ch-ua": '"Not-A.Me_Brand";v="99", "Chromium";v="124", "Google Chrome";v="124"',
    "sec-ch-ua-platform": '"Windows"',
    "upgrade-insecure-requests": "1",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}

# Tenta carregar o cérebro da Inteligência Artificial (Pipeline NLP) para o Japão
try:
    vectorizer = joblib.load('vetorizador_mercari.pkl')
    modelo_ia = joblib.load('modelo_ia_mercari.pkl')
    IA_DISPONIVEL = True
except Exception:
    IA_DISPONIVEL = False

# Gerenciadores de estado visual do Rich
console = Console()
status_sistema = "Inicializando..."
ultima_atividade = "Nenhuma atividade registrada."
proxima_varredura = "Calculando..."

def gerar_painel():
    conteudo = Text()
    conteudo.append(">>> MERCARI JP SNIPER - DASHBOARD ATIVO\n", style="bold red")
    conteudo.append("-" * 56 + "\n", style="bright_black")
    
    conteudo.append("[*] Status Telegram: ", style="bold green")
    conteudo.append("Online & Escutando\n", style="green")
    
    conteudo.append("[*] Cérebro IA:      ", style="bold green" if IA_DISPONIVEL else "bold red")
    conteudo.append("Modelo ML Carregado\n" if IA_DISPONIVEL else "Heurística Japonesa (Sem .pkl)\n", style="green" if IA_DISPONIVEL else "red")
    
    status_texto = status_sistema
    status_texto = (status_texto[:25] + "...") if len(status_texto) > 28 else status_texto
    conteudo.append("[*] Status Motor:    ", style="bold yellow")
    conteudo.append(f"{status_texto}\n", style="white")
    
    conteudo.append("[*] Proximo Ciclo:   ", style="bold blue")
    conteudo.append(f"{proxima_varredura}\n", style="blue")
    
    conteudo.append("-" * 56 + "\n", style="bright_black")
    conteudo.append("ULTIMO EVENTO REGISTRADO:\n", style="bold cyan")
    
    evento_texto = ultima_atividade
    if len(evento_texto) > 54:
        evento_texto = evento_texto[:51] + "..."
    conteudo.append(f"{evento_texto.ljust(54)}\n", style="italic white")
    
    return Panel(conteudo, border_style="red", title="[b]Mercari JP Sniper v2.5[/b]", expand=False, width=60)

def log_sistema(componente, mensagem):
    global ultima_atividade
    horario = time.strftime('%H:%M:%S')
    ultima_atividade = f"[{horario}] {componente}: {mensagem}"

def inicializar_banco():
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS anuncios_vistos (
                id TEXT PRIMARY KEY, 
                apelido TEXT,
                preco TEXT, 
                preco_num REAL,
                com_defeito INTEGER DEFAULT 0,
                data_captura TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS urls_monitoradas (
                id INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT UNIQUE, apelido TEXT UNIQUE
            )
        """)
        conn.commit()
        conn.close()
        log_sistema("Banco", "Estrutura verificada.")
    except Exception as e:
        log_sistema("Banco", f"Erro: {type(e).__name__}")

# ==============================================================================
# LÓGICA DE CÂMBIO, ESTATÍSTICA E INTELIGÊNCIA ARTIFICIAL
# ==============================================================================
def atualizar_cotacao_moeda():
    global COTACAO_JPY_BRL
    url_api = "https://economia.awesomeapi.com.br/last/JPY-BRL"
    try:
        response = normal_requests.get(url_api, timeout=10)
        if response.status_code == 200:
            dados = response.json()
            COTACAO_JPY_BRL = float(dados["JPYBRL"]["bid"])
            log_sistema("Câmbio", f"Cotação Atualizada: 1 ¥ = R$ {COTACAO_JPY_BRL:.4f}")
    except Exception:
        log_sistema("Câmbio", "Falha ao obter câmbio. Usando Fallback.")

def converter_jpy_para_brl(preco_jpy_num):
    try:
        valor_brl = preco_jpy_num * COTACAO_JPY_BRL
        return f"R$ {valor_brl:,.2f}".replace(",", "v").replace(".", ",").replace("v", ".")
    except Exception:
        return "R$ --,--"

def possui_defeito_heuristica(texto):
    """Filtro de fallback com gatilhos em japonês para identificar Junk/Defeitos."""
    if not texto: return False
    # ジャンク (Junk), 故障 (Falha/Quebrado), 動作未確認 (Operação não confirmada), 傷 (Risco/Avaria), 部品取り (Retirada de peças)
    gatilhos = ["ジャンク", "故障", "動作未確認", " 傷", "部品取り", "不具合", "破損", "状態が悪い", "修理"]
    texto_minusculo = texto.lower()
    return any(gatilho in texto_minusculo for gatilho in gatilhos)

def calcular_media_movel(apelido, janelas_anuncios=30):
    """Média móvel aritmética dos últimos X anúncios em Ienes sem defeito."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT preco_num FROM anuncios_vistos 
        WHERE apelido = ? AND com_defeito = 0 AND preco_num > 0
        ORDER BY data_captura DESC LIMIT ?
    """, (apelido, janelas_anuncios))
    precos = [row[0] for row in cursor.fetchall()]
    conn.close()
    
    if not precos or len(precos) < 5: 
        return 0.0
    return sum(precos) / len(precos)

def extrair_descricao_completa(url_anuncio):
    """Acessa a página individual do item no Mercari JP e extrai a descrição interna."""
    try:
        time.sleep(random.uniform(0.6, 1.3))
        response = curl_requests.get(url_anuncio, headers=HEADERS, timeout=10, impersonate="chrome124")
        if response.status_code != 200: return ""
        soup = BeautifulSoup(response.text, 'html.parser')
        script = soup.find('script', id='__NEXT_DATA__')
        if script:
            dados = json.loads(script.string)
            # Varre o Apollo State em busca da descrição estruturada do item
            queries = dados.get('props', {}).get('pageProps', {}).get('apolloState', {})
            for chave, valor in queries.items():
                if chave.startswith("Item:") and isinstance(valor, dict):
                    if valor.get('description'):
                        return valor.get('description')
    except Exception:
        return ""
    return ""

def classificar_ia_defeito(descricao):
    if not IA_DISPONIVEL or not descricao:
        return 0
    try:
        vetor = vectorizer.transform([descricao.lower()])
        return int(modelo_ia.predict(vetor)[0])
    except Exception:
        return 0

async def traduzir_texto(texto_ja):
    if not texto_ja: return "Sem título"
    try:
        loop = asyncio.get_event_loop()
        texto_pt = await loop.run_in_executor(
            None, 
            lambda: GoogleTranslator(source='ja', target='pt').translate(texto_ja)
        )
        return texto_pt
    except Exception:
        return f"[Trad. Falhou] {texto_ja}"

# ==============================================================================
# COMANDOS DO TELEGRAM
# ==============================================================================
async def comando_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != USUARIO_PERMITIDO: return
    mensagem = (
        "🎯 <b>Mercari JP Sniper Ativo!</b>\n\n"
        "Comandos disponíveis:\n"
        "➕ <code>/adicionar apelido url_jp</code>\n"
        "📋 <code>/listar</code>\n"
        "❌ <code>/remover apelido</code>"
    )
    await update.message.reply_text(mensagem, parse_mode="HTML")

async def comando_adicionar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != USUARIO_PERMITIDO: return
    if len(context.args) < 2:
        await update.message.reply_text("❌ Use: <code>/adicionar apelido url</code>", parse_mode="HTML")
        return

    apelido, url_jp = context.args[0], context.args[1]
    if "mercari.com/jp" not in url_jp and "jp.mercari.com" not in url_jp:
        await update.message.reply_text("❌ URL inválida do Mercari JP.")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO urls_monitoradas (url, apelido) VALUES (?, ?)", (url_jp, apelido))
        conn.commit()
        log_sistema("Telegram", f"Filtro JP criado: {apelido}")
        
        anuncios_atuais = await asyncio.get_event_loop().run_in_executor(None, buscar_anuncios, url_jp)
        for ad in anuncios_atuais:
            p_num = limpar_string_preco(ad['preco'])
            def_h = 1 if possui_defeito_heuristica(ad['titulo_original']) else 0
            cursor.execute("""
                INSERT OR IGNORE INTO anuncios_vistos (id, apelido, preco, preco_num, com_defeito) 
                VALUES (?, ?, ?, ?, ?)
            """, (ad['id'], apelido, ad['preco'], p_num, def_h))
        conn.commit()

        await update.message.reply_text(f"✅ Filtro <b>{apelido}</b> ativo no Japão! {len(anuncios_atuais)} itens ignorados.", parse_mode="HTML")
    except sqlite3.IntegrityError:
        await update.message.reply_text("❌ Apelido ou URL já cadastrada.")
    except Exception as e:
        log_sistema("Telegram", f"Erro ADD: {type(e).__name__}")
    finally: conn.close()

async def comando_listar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != USUARIO_PERMITIDO: return
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT apelido FROM urls_monitoradas")
        filtros = cursor.fetchall()
        if not filtros:
            await update.message.reply_text("📋 Nenhum filtro cadastrado.")
            return
        mensagem = "📋 <b>Filtros Mercari JP Ativos:</b>\n\n" + "\n".join([f"📌 <b>{row[0]}</b>" for row in filtros])
        await update.message.reply_text(mensagem, parse_mode="HTML")
    finally: conn.close()

async def comando_remover(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != USUARIO_PERMITIDO: return
    if not context.args: return
    apelido_alvo = context.args[0]
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM urls_monitoradas WHERE apelido = ?", (apelido_alvo,))
        if cursor.rowcount > 0:
            log_sistema("Telegram", f"Removido: {apelido_alvo}")
            await update.message.reply_text(f"❌ Filtro JP <b>{apelido_alvo}</b> removido.")
        else:
            await update.message.reply_text("⚠️ Filtro não encontrado.")
        conn.commit()
    finally: conn.close()

# ==============================================================================
# MOTOR DE RASPAGEM
# ==============================================================================
def buscar_anuncios(url_alvo):
    try:
        time.sleep(1.5)
        response = curl_requests.get(url_alvo, headers=HEADERS, timeout=15, impersonate="chrome124")
        if response.status_code != 200: return []
        soup = BeautifulSoup(response.text, 'html.parser')
        script = soup.find('script', id='__NEXT_DATA__')
        if not script: return []
        dados_json = json.loads(script.string)
        queries = dados_json.get('props', {}).get('pageProps', {}).get('apolloState', {})
        if not queries:
            queries = dados_json.get('props', {}).get('pageProps', {}).get('dehydratedState', {}).get('queries', [])
            
        lista_estruturada = []
        for chave, valor in queries.items():
            if chave.startswith("Item:") and isinstance(valor, dict):
                item_id = valor.get('id') or chave.split(":")[-1]
                titulo_ja = valor.get('name')
                preco = valor.get('price')
                
                if item_id and titulo_ja and preco:
                    lista_estruturada.append({
                        'id': str(item_id),
                        'titulo_original': titulo_ja,
                        'preco': f"¥{preco}",
                        'url': f"https://jp.mercari.com/item/{item_id}"
                    })
        return lista_estruturada
    except Exception: return []

def limpar_string_preco(p_str):
    try:
        if not p_str: return 0.0
        return float(p_str.replace("¥", "").replace(",", "").strip())
    except Exception: return 0.0

async def loop_motor_sniper(application):
    """Motor analítico de dois estágios adaptado para o ecossistema japonês."""
    global status_sistema, proxima_varredura
    loop = asyncio.get_event_loop()
    
    ultima_atualizacao_cambio = 0

    status_sistema = "Sincronizando banco..."
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT url, apelido FROM urls_monitoradas")
        filtros_iniciais = cursor.fetchall()
        for url, apelido in filtros_iniciais:
            ads = await loop.run_in_executor(None, buscar_anuncios, url)
            for ad in ads:
                p_num = limpar_string_preco(ad['preco'])
                def_h = 1 if possui_defeito_heuristica(ad['titulo_original']) else 0
                cursor.execute("""
                    INSERT OR IGNORE INTO anuncios_vistos (id, apelido, preco, preco_num, com_defeito) 
                    VALUES (?, ?, ?, ?, ?)
                """, (ad['id'], apelido, ad['preco'], p_num, def_h))
        conn.commit()
        conn.close()
        log_sistema("Motor", "Sincronização concluída.")
    except Exception as e: 
        log_sistema("Motor", f"Erro inicial: {type(e).__name__}")

    while True:
        try:
            if time.time() - ultima_atualizacao_cambio > 3600:
                atualizar_cotacao_moeda()
                ultima_atualizacao_cambio = time.time()

            status_sistema = "Varrendo o Mercari JP..."
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT url, apelido FROM urls_monitoradas")
            filtros_ativos = cursor.fetchall()
        
            if filtros_ativos:
                for url, apelido in filtros_ativos:
                    anuncios = await loop.run_in_executor(None, buscar_anuncios, url)
                    media_atual = calcular_media_movel(apelido)
                    
                    for ad in anuncios:
                        cursor.execute("SELECT preco FROM anuncios_vistos WHERE id = ?", (ad['id'],))
                        resultado = cursor.fetchone()
                        preco_atual_num = limpar_string_preco(ad['preco'])
                        
                        # Estágio 1: Triagem imediata por heurística de caracteres no título JA
                        defeito_detectado = 1 if possui_defeito_heuristica(ad['titulo_original']) else 0
                        
                        if not resultado:
                            is_urgente = False
                            
                            # Estágio 2: Se for suspeito (<= 75% da média em ienes) e sem defeito óbvio no título
                            if media_atual > 0.0 and defeito_detectado == 0 and preco_atual_num > 0.0:
                                if preco_atual_num <= (media_atual * 0.75):
                                    status_sistema = f"IA: Analisando {apelido} JP..."
                                    desc_completa = await loop.run_in_executor(None, extrair_descricao_completa, ad['url'])
                                    
                                    if desc_completa and IA_DISPONIVEL:
                                        # Executa classificação preditiva via Machine Learning na descrição nativa
                                        pred_ia = await loop.run_in_executor(None, classificar_ia_defeito, desc_completa)
                                        if pred_ia == 1:
                                            defeito_detectado = 1
                                            log_sistema("IA", f"Defeito japonês pego na descrição: {ad['id']}")
                                        else:
                                            is_urgente = True
                                    elif desc_completa:
                                        # Fallback heurístico na descrição nativa JA se a IA estiver offline
                                        if possui_defeito_heuristica(desc_completa):
                                            defeito_detectado = 1
                                            log_sistema("IA", f"Defeito japonês pego no fallback: {ad['id']}")
                                        else:
                                            is_urgente = True

                            # Resolve as traduções e conversões cambiais necessárias
                            titulo_traduzido = await traduzir_texto(ad['titulo_original'])
                            preco_convertido = converter_jpy_para_brl(preco_atual_num)

                            if is_urgente:
                                log_sistema("Motor", f"🔥 ULTRA OPORTUNIDADE JP ({apelido})")
                                media_brl = converter_jpy_para_brl(media_atual)
                                msg = (
                                    f"🔥 <b>ULTRA OPORTUNIDADE JAPÃO [{apelido}] (75% DA MÉDIA)</b> 🔥\n"
                                    f"🏃‍♂️ <i>ABAIXO DO MERCADO NATIVO!</i>\n\n"
                                    f"🇯🇵 <b>Original:</b> {ad['titulo_original']}\n"
                                    f"🇧🇷 <b>Tradução:</b> {titulo_traduzido}\n"
                                    f"💰 <b>Preço JP:</b> {ad['preco']} (Média: ¥{int(media_atual)})\n"
                                    f"💵 <b>Conversão BRL:</b> {preco_convertido} (Média: {media_brl})\n\n"
                                    f"🔗 <a href='{ad['url']}'>Abrir correndo</a>"
                                )
                            else:
                                marcador = " [DEFEITO/JUNK]" if defeito_detectado else ""
                                log_sistema("Motor", f"🚨 NOVO{marcador} ({apelido})")
                                msg = (
                                    f"🚨 <b>NOVO ANÚNCIO JAPÃO [{apelido}]{marcador}</b> 🚨\n\n"
                                    f"🇯🇵 <b>Original:</b> {ad['titulo_original']}\n"
                                    f"🇧🇷 <b>Tradução:</b> {titulo_traduzido}\n"
                                    f"💰 <b>Preço JP:</b> {ad['preco']}\n"
                                    f"💵 <b>Conversão Estimada:</b> {preco_convertido}\n\n"
                                    f"🔗 <a href='{ad['url']}'>Abrir</a>"
                                )
                            
                            await application.bot.send_message(chat_id=USUARIO_PERMITIDO, text=msg, parse_mode="HTML")
                            cursor.execute("""
                                INSERT INTO anuncios_vistos (id, apelido, preco, preco_num, com_defeito) 
                                VALUES (?, ?, ?, ?, ?)
                            """, (ad['id'], apelido, ad['preco'], preco_atual_num, defeito_detectado))
                            conn.commit()
                            
                        else:
                            preco_antigo_str = resultado[0]
                            if 0.0 < preco_atual_num < limpar_string_preco(preco_antigo_str):
                                log_sistema("Motor", f"📉 BAIXOU ({apelido})")
                                
                                titulo_traduzido = await traduzir_texto(ad['titulo_original'])
                                preco_convertido_novo = converter_jpy_para_brl(preco_atual_num)
                                preco_convertido_antigo = converter_jpy_para_brl(limpar_string_preco(preco_antigo_str))
                                
                                msg = (
                                    f"📉 <b>PREÇO BAIXOU NO JAPÃO [{apelido}]</b> 📉\n\n"
                                    f"📌 <b>Item:</b> {titulo_traduzido}\n"
                                    f"💰 <b>Antes:</b> <s>{preco_antigo_str}</s> ({preco_convertido_antigo}) \n"
                                    f"🔥 <b>Agora:</b> <b>{ad['preco']}</b> ({preco_convertido_novo})\n\n"
                                    f"🔗 <a href='{ad['url']}'>Abrir</a>"
                                )
                                await application.bot.send_message(chat_id=USUARIO_PERMITIDO, text=msg, parse_mode="HTML")
                                cursor.execute("UPDATE anuncios_vistos SET preco = ?, preco_num = ? WHERE id = ?", (ad['preco'], preco_atual_num, ad['id']))
                                conn.commit()
            conn.close()
        except Exception as e: 
            log_sistema("Motor", f"Erro ciclo: {type(e).__name__}")
            
        status_sistema = "Dormindo..."
        intervalo = random.randint(180, 300)
        for restante in range(intervalo, 0, -1):
            proxima_varredura = f"{restante // 60}m {restante % 60}s ({intervalo}s)"
            await asyncio.sleep(1)

# ==============================================================================
# LOOP PRINCIPAL DO SISTEMA
# ==============================================================================
async def main():
    atualizar_cotacao_moeda()
    inicializar_banco()

    application = Application.builder().token(TELEGRAM_TOKEN_MERCARI).build()
    application.add_handler(CommandHandler("start", comando_start))
    application.add_handler(CommandHandler("adicionar", comando_adicionar))
    application.add_handler(CommandHandler("listar", comando_listar))
    application.add_handler(CommandHandler("remover", comando_remover))

    await application.initialize()
    await application.start()
    await application.updater.start_polling(drop_pending_updates=True)
    log_sistema("Telegram", "Escutador JP ativo!")

    asyncio.create_task(loop_motor_sniper(application))

    with Live(gerar_painel(), screen=True, auto_refresh=True) as live:
        while True:
            live.update(gerar_painel())
            await asyncio.sleep(1)

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass