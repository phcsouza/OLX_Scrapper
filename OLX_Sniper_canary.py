"""
olx_sniper_canary.py

Versão de teste com concorrência assíncrona real (asyncio).
O painel Rich, o motor da OLX, o verificador de cupons e o bot do Telegram rodam cooperativamente.
Integração de Filtro Estatístico de Média Móvel e classificação de IA (NLP).
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
TELEGRAM_TOKEN_CANARY = os.getenv("TELEGRAM_TOKEN_CANARY")
USUARIO_PERMITIDO_STR = os.getenv("USUARIO_PERMITIDO")

if not TELEGRAM_TOKEN_CANARY or not USUARIO_PERMITIDO_STR:
    print("\n[CRÍTICO] Erro: Variáveis de ambiente não encontradas no arquivo .env!")
    sys.exit(1)

USUARIO_PERMITIDO = int(USUARIO_PERMITIDO_STR)
DB_PATH = "sniper_dados_canary.db"

HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "accept-language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "referer": "https://www.olx.com.br/",
    "sec-ch-ua": '"Not-A.Me_Brand";v="99", "Chromium";v="124", "Google Chrome";v="124"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "same-origin",
    "sec-fetch-user": "?1",
    "upgrade-insecure-requests": "1"
}

# Tenta carregar o cérebro da Inteligência Artificial (Pipeline NLP)
try:
    vectorizer = joblib.load('vetorizador_olx.pkl')
    modelo_ia = joblib.load('modelo_ia_olx.pkl')
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
    conteudo.append(">>> OLX SNIPER - DASHBOARD ATIVO\n", style="bold cyan")
    conteudo.append("-" * 56 + "\n", style="bright_black")
    
    conteudo.append("[*] Status Telegram: ", style="bold green")
    conteudo.append("Online & Escutando\n", style="green")
    
    conteudo.append("[*] Cérebro IA:      ", style="bold green" if IA_DISPONIVEL else "bold red")
    conteudo.append("Modelo ML Carregado\n" if IA_DISPONIVEL else "Heurística Simples (Sem .pkl)\n", style="green" if IA_DISPONIVEL else "red")
    
    status_texto = status_sistema
    status_texto = (status_texto[:25] + "...") if len(status_texto) > 28 else status_texto
    conteudo.append("[*] Status Motor:    ", style="bold yellow")
    conteudo.append(f"{status_texto}\n", style="white")
    
    conteudo.append("[*] Proximo Ciclo:   ", style="bold blue")
    conteudo.append(f"{proxima_varredura}\n", style="blue")
    
    conteudo.append("-" * 56 + "\n", style="bright_black")
    conteudo.append("ULTIMO EVENTO REGISTRADO:\n", style="bold magenta")
    
    evento_texto = ultima_atividade
    if len(evento_texto) > 54:
        evento_texto = evento_texto[:51] + "..."
    conteudo.append(f"{evento_texto.ljust(54)}\n", style="italic white")
    
    return Panel(conteudo, border_style="cyan", title="[b]OLX Sniper v2.5[/b]", expand=False, width=60)

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
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS cupons_vistos (
                code TEXT PRIMARY KEY, rules TEXT
            )
        """)
        # Garante a migração de colunas analíticas do banco antigo se necessário
        try: cursor.execute("ALTER TABLE anuncios_vistos ADD COLUMN apelido TEXT")
        except sqlite3.OperationalError: pass
        try: cursor.execute("ALTER TABLE anuncios_vistos ADD COLUMN preco_num REAL")
        except sqlite3.OperationalError: pass
        try: cursor.execute("ALTER TABLE anuncios_vistos ADD COLUMN com_defeito INTEGER DEFAULT 0")
        except sqlite3.OperationalError: pass
        
        conn.commit()
        conn.close()
        log_sistema("Banco", "Estrutura verificada.")
    except Exception as e:
        log_sistema("Banco", f"Erro: {type(e).__name__}")

# ==============================================================================
# FUNÇÕES ESTATÍSTICAS E INTELIGÊNCIA ARTIFICIAL
# ==============================================================================
def possui_defeito_heuristica(titulo):
    """Filtro de fallback caso a IA não esteja carregada."""
    gatilhos = ["defeito", "no estado", "avaria", "quebrado", "trincado", "peças", "conserto", "parou", "retirar"]
    titulo_minusculo = titulo.lower()
    return any(gatilho in titulo_minusculo for gatilho in gatilhos)

def calcular_media_movel(apelido, janelas_anuncios=30):
    """Média móvel aritmética dos últimos X anúncios sem defeito cadastrados."""
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
    """Acessa a página individual e extrai a descrição do nó __NEXT_DATA__."""
    try:
        time.sleep(random.uniform(0.5, 1.2))
        response = curl_requests.get(url_anuncio, headers=HEADERS, timeout=10, impersonate="chrome124")
        if response.status_code != 200: return ""
        soup = BeautifulSoup(response.text, 'html.parser')
        script = soup.find('script', id='__NEXT_DATA__')
        if script:
            dados = json.loads(script.string)
            return dados.get('props', {}).get('pageProps', {}).get('ad', {}).get('description', '')
    except Exception:
        return ""
    return ""

def classificar_ia_defeito(descricao):
    """Roda a predição vetorial do modelo matemático treinado."""
    if not IA_DISPONIVEL or not descricao:
        return 0
    try:
        vetor = vectorizer.transform([descricao.lower()])
        return int(modelo_ia.predict(vetor)[0])
    except Exception:
        return 0

# ==============================================================================
# COMANDOS DO TELEGRAM
# ==============================================================================
async def comando_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != USUARIO_PERMITIDO: return
    mensagem = (
        "🎯 <b>OLX Sniper Ativo!</b>\n\n"
        "Comandos disponíveis:\n"
        "➕ <code>/adicionar apelido url_olx</code>\n"
        "📋 <code>/listar</code>\n"
        "❌ <code>/remover apelido</code>"
    )
    await update.message.reply_text(mensagem, parse_mode="HTML")

async def comando_adicionar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != USUARIO_PERMITIDO: return
    if len(context.args) < 2:
        await update.message.reply_text("❌ Use: <code>/adicionar apelido url</code>", parse_mode="HTML")
        return

    apelido, url_olx = context.args[0], context.args[1]
    if "olx.com.br" not in url_olx:
        await update.message.reply_text("❌ URL inválida da OLX.")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO urls_monitoradas (url, apelido) VALUES (?, ?)", (url_olx, apelido))
        conn.commit()
        log_sistema("Telegram", f"Filtro criado: {apelido}")
        
        anuncios_atuais = await asyncio.get_event_loop().run_in_executor(None, buscar_anuncios, url_olx)
        for ad in anuncios_atuais:
            p_num = limpar_string_preco(ad['preco'])
            def_h = 1 if possui_defeito_heuristica(ad['titulo']) else 0
            cursor.execute("""
                INSERT OR IGNORE INTO anuncios_vistos (id, apelido, preco, preco_num, com_defeito) 
                VALUES (?, ?, ?, ?, ?)
            """, (ad['id'], apelido, ad['preco'], p_num, def_h))
        conn.commit()

        await update.message.reply_text(f"✅ Filtro <b>{apelido}</b> ativo! {len(anuncios_atuais)} itens ignorados.", parse_mode="HTML")
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
        mensagem = "📋 <b>Filtros Ativos:</b>\n\n" + "\n".join([f"📌 <b>{row[0]}</b>" for row in filtros])
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
            await update.message.reply_text(f"❌ Filtro <b>{apelido_alvo}</b> removido.")
        else:
            await update.message.reply_text("⚠️ Filtro não encontrado.")
        conn.commit()
    finally: conn.close()

# ==============================================================================
# MOTOR DE RASPAGEM E ALERTAS (ASSÍNCRONOS/COOPERATIVOS)
# ==============================================================================
def buscar_anuncios(url_alvo):
    try:
        time.sleep(1.0)
        response = curl_requests.get(url_alvo, headers=HEADERS, timeout=15, impersonate="chrome124")
        if response.status_code != 200: return []
        soup = BeautifulSoup(response.text, 'html.parser')
        script = soup.find('script', id='__NEXT_DATA__')
        if not script: return []
        dados_json = json.loads(script.string)
        page_props = dados_json.get('props', {}).get('pageProps', {})
        ads = page_props.get('ads', []) or page_props.get('states', {}).get('results', []) or page_props.get('initialState', {}).get('search', {}).get('ads', [])
        if not ads: return []
        
        lista = []
        for ad in ads:
            if ad.get('listId'):
                lista.append({
                    'id': str(ad.get('listId')),
                    'titulo': ad.get('title') or ad.get('subject') or "Sem título",
                    'preco': ad.get('price', 'N/I'), 'url': ad.get('url')
                })
        return lista
    except Exception: return []

def buscar_cupons_olx():
    url_cupons = "https://www.olx.com.br/cupons"
    try:
        time.sleep(1.5)
        response = curl_requests.get(url_cupons, headers=HEADERS, timeout=15, impersonate="chrome124")
        if response.status_code != 200: return []
        soup = BeautifulSoup(response.text, 'html.parser')
        script = soup.find('script', id='__NEXT_DATA__')
        if not script: return []
        dados_json = json.loads(script.string)
        page_props = dados_json.get('props', {}).get('pageProps', {})
        lista_cupons = page_props.get('coupons', []) or page_props.get('initialState', {}).get('coupons', [])
        
        cupons_estruturados = []
        for c in lista_cupons:
            codigo = c.get('code')
            regra = c.get('title') or c.get('description') or "Desconto especial"
            if codigo: cupons_estruturados.append({'code': codigo, 'rules': regra})
        return cupons_estruturados
    except Exception: return []

def limpar_string_preco(p_str):
    try:
        if not p_str or "Não" in p_str or "N/I" in p_str: return 0.0
        limpo = p_str.replace("R$", "").replace(".", "").replace(" ", "").strip()
        if "," in limpo: limpo = limpo.split(",")[0]
        return float(limpo)
    except Exception: return 0.0

async def loop_verificador_cupons(application):
    while True:
        log_sistema("Cupons", "Verificando cupons na OLX...")
        loop = asyncio.get_event_loop()
        cupons_encontrados = await loop.run_in_executor(None, buscar_cupons_olx)
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        for cp in cupons_encontrados:
            cursor.execute("SELECT code FROM cupons_vistos WHERE code = ?", (cp['code'],))
            existe = cursor.fetchone()
            if not existe:
                log_sistema("Cupons", f"🔥 NOVO CUPOM: {cp['code']}")
                msg = f"🔥 <b>NOVO CUPOM DISPONÍVEL NA OLX</b> 🔥\n\n🎟️ <b>Código:</b> <code>{cp['code']}</code>\n📌 <b>Regras:</b> {cp['rules']}\n🔗 <a href='https://www.olx.com.br/cupons'>Ver na OLX</a>"
                await application.bot.send_message(chat_id=USUARIO_PERMITIDO, text=msg, parse_mode="HTML")
                cursor.execute("INSERT INTO cupons_vistos (code, rules) VALUES (?, ?)", (cp['code'], cp['rules']))
                conn.commit()
        conn.close()
        log_sistema("Cupons", "Aguardando 4h para nova checagem.")
        await asyncio.sleep(14400)

async def loop_motor_sniper(application):
    """Motor analítico de dois estágios rodando em cooperação de concorrência."""
    global status_sistema, proxima_varredura
    loop = asyncio.get_event_loop()
    
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
                def_h = 1 if possui_defeito_heuristica(ad['titulo']) else 0
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
            status_sistema = "Varrendo a OLX..."
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
                        
                        # Primeiro estágio de triagem: Heurística rápida no título
                        defeito_detectado = 1 if possui_defeito_heuristica(ad['titulo']) else 0
                        
                        if not resultado:
                            is_urgente = False
                            
                            # Segundo estágio: Se o preço for suspeito (<= 75% da média) e não for defeito óbvio
                            if media_atual > 0.0 and defeito_detectado == 0 and preco_atual_num > 0.0:
                                if preco_atual_num <= (media_atual * 0.75):
                                    status_sistema = f"IA: Analisando {apelido}..."
                                    desc_completa = await loop.run_in_executor(None, extrair_descricao_completa, ad['url'])
                                    
                                    if desc_completa and IA_DISPONIVEL:
                                        # Predição estatística via Machine Learning
                                        pred_ia = await loop.run_in_executor(None, classificar_ia_defeito, desc_completa)
                                        if pred_ia == 1:
                                            defeito_detectado = 1
                                            log_sistema("IA", f"Defeito pego na descrição: {ad['titulo']}")
                                        else:
                                            is_urgente = True
                                    elif desc_completa:
                                        # Fallback heurístico na descrição caso a IA esteja offline
                                        if possui_defeito_heuristica(desc_completa):
                                            defeito_detectado = 1
                                            log_sistema("IA", f"Defeito pego no fallback: {ad['titulo']}")
                                        else:
                                            is_urgente = True

                            # Formatação dinâmica dos alertas
                            if is_urgente:
                                log_sistema("Motor", f"🔥 ULTRA OPORTUNIDADE ({apelido}): {ad['titulo']}")
                                msg = f"🔥 <b>ULTRA OPORTUNIDADE [{apelido}] (75% DA MÉDIA)</b> 🔥\n🏃‍♂️ <i>CORRE, ABAIXO DO MERCADO!</i>\n\n📌 {ad['titulo']}\n💰 Preço: {ad['preco']} (Média: R$ {media_atual:.2f})\n🔗 <a href='{ad['url']}'>Abrir correndo</a>"
                            else:
                                marcador = " [DEFEITO]" if defeito_detectado else ""
                                log_sistema("Motor", f"🚨 NOVO{marcador} ({apelido}): {ad['titulo']}")
                                msg = f"🚨 <b>NOVO ANÚNCIO [{apelido}]{marcador}</b> 🚨\n\n📌 {ad['titulo']}\n💰 Preço: {ad['preco']}\n🔗 <a href='{ad['url']}'>Abrir</a>"
                            
                            await application.bot.send_message(chat_id=USUARIO_PERMITIDO, text=msg, parse_mode="HTML")
                            cursor.execute("""
                                INSERT INTO anuncios_vistos (id, apelido, preco, preco_num, com_defeito) 
                                VALUES (?, ?, ?, ?, ?)
                            """, (ad['id'], apelido, ad['preco'], preco_atual_num, defeito_detectado))
                            conn.commit()
                            
                        else:
                            # Fluxo de atualização se o preço baixar
                            preco_antigo_str = resultado[0]
                            if 0.0 < preco_atual_num < limpar_string_preco(preco_antigo_str):
                                log_sistema("Motor", f"📉 BAIXOU ({apelido}): {ad['titulo']}")
                                msg = f"📉 <b>BAIXOU PREÇO [{apelido}]</b> 📉\n\n📌 {ad['titulo']}\n💰 Antes: <s>{preco_antigo_str}</s> -> Agora: {ad['preco']}\n🔗 <a href='{ad['url']}'>Abrir</a>"
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
    inicializar_banco()

    # Inicializa o Bot do Telegram de forma nativa e assíncrona
    application = Application.builder().token(TELEGRAM_TOKEN_CANARY).build()
    application.add_handler(CommandHandler("start", comando_start))
    application.add_handler(CommandHandler("adicionar", comando_adicionar))
    application.add_handler(CommandHandler("listar", comando_listar))
    application.add_handler(CommandHandler("remover", comando_remover))

    await application.initialize()
    await application.start()
    await application.updater.start_polling(drop_pending_updates=True)
    log_sistema("Telegram", "Escutador ativo!")

    # Agenda a execução dos motores em background como Tasks concorrentes
    asyncio.create_task(loop_motor_sniper(application))
    asyncio.create_task(loop_verificador_cupons(application))

    # Executa a renderização do Rich de forma cooperativa no mesmo fluxo
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