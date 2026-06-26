"""
olx_sniper.py

Motor de monitoramento (Sniper) automatizado para a plataforma OLX Brasil com integração ao Telegram.
Versão purificada sem dependências gráficas de Rich para máxima estabilidade de rede.

Principais funcionalidades
--------------------------
- Varredura assíncrona e paralela de múltiplos filtros customizados da OLX.
- Detecção automatizada de novos anúncios e de redução de preços de itens existentes.
- Logs limpos diretamente no terminal padrão, evitando conflitos de concorrência com o Telegram.
- Tolerância a falhas físicas de rede e intervalos aleatórios para evitar rate limit.
"""

import os
import sys
import time
import json
import sqlite3
import threading
import asyncio
import random
import httpx
from bs4 import BeautifulSoup
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.request import HTTPXRequest
from curl_cffi import requests as curl_requests

# Import para carregamento de variáveis de ambiente
from dotenv import load_dotenv

# Carrega as configurações do arquivo .env local
load_dotenv()

# ==============================================================================
# CONFIGURAÇÕES GERAIS
# ==============================================================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
USUARIO_PERMITIDO_STR = os.getenv("USUARIO_PERMITIDO")

if not TELEGRAM_TOKEN or not USUARIO_PERMITIDO_STR:
    print("\n[CRÍTICO] Erro: Variáveis de ambiente não encontradas no arquivo .env!")
    print("Certifique-se de que o arquivo .env existe e contém TELEGRAM_TOKEN e USUARIO_PERMITIDO.")
    sys.exit(1)

USUARIO_PERMITIDO = int(USUARIO_PERMITIDO_STR)
DB_PATH = "sniper_dados.db"

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


def log_sistema(componente, mensagem):
    """
    Exibe logs limpos e datados no terminal padrão.
    
    Parameters
    ----------
    componente : str
        Módulo que gerou o log (Ex: Motor, Telegram, Scraper).
    mensagem : str
        Texto descritivo do evento.
    """
    horario = time.strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{horario}] [{componente}] {mensagem}")


def inicializar_banco():
    """
    Inicializa o banco de dados SQLite local e cria as tabelas estruturais.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS anuncios_vistos (
                id TEXT PRIMARY KEY,
                preco TEXT,
                data_captura TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS urls_monitoradas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT UNIQUE,
                apelido TEXT UNIQUE
            )
        """)
        try:
            cursor.execute("ALTER TABLE anuncios_vistos ADD COLUMN preco TEXT")
        except sqlite3.OperationalError:
            pass
            
        conn.commit()
        conn.close()
        log_sistema("Banco", "Estrutura SQLite verificada/criada com sucesso.")
    except Exception as e:
        log_sistema("Banco", f"Erro crítico nas tabelas: {e}")


# ==============================================================================
# COMANDOS DO TELEGRAM
# ==============================================================================
async def comando_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != USUARIO_PERMITIDO:
        return
    mensagem = (
        "🎯 <b>OLX Sniper Ativo!</b>\n\n"
        "Comandos disponíveis:\n"
        "➕ <code>/adicionar apelido url_olx</code> - Monitora um novo filtro\n"
        "📋 <code>/listar</code> - Mostra os filtros ativos\n"
        "❌ <code>/remover apelido</code> - Para de monitorar um filtro"
    )
    await update.message.reply_text(mensagem, parse_mode="HTML")


async def comando_adicionar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != USUARIO_PERMITIDO:
        return

    if len(context.args) < 2:
        await update.message.reply_text("❌ Formato incorreto! Use: <code>/adicionar apelido url_da_olx</code>", parse_mode="HTML")
        return

    apelido = context.args[0]
    url_olx = context.args[1]

    if "olx.com.br" not in url_olx:
        await update.message.reply_text("❌ Isso não parece ser uma URL válida da OLX Brasil.")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO urls_monitoradas (url, apelido) VALUES (?, ?)", (url_olx, apelido))
        conn.commit()
        
        log_sistema("Telegram", f"Adicionando novo filtro: {apelido}")
        anuncios_atuais = buscar_anuncios(url_olx)
        for ad in anuncios_atuais:
            cursor.execute("INSERT OR IGNORE INTO anuncios_vistos (id, preco) VALUES (?, ?)", (ad['id'], ad['preco']))
        conn.commit()

        await update.message.reply_text(
            f"✅ Filtro <b>{apelido}</b> adicionado com sucesso!\n"
            f"📌 {len(anuncios_atuais)} anúncios antigos catalogados e ignorados.", 
            parse_mode="HTML"
        )
    except sqlite3.IntegrityError:
        await update.message.reply_text("❌ Este apelido ou esta URL já está sendo monitorada.")
    except Exception as e:
        log_sistema("Telegram", f"Erro ao salvar URL: {e}")
    finally:
        conn.close()


async def comando_listar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != USUARIO_PERMITIDO:
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT apelido FROM urls_monitoradas")
        filtros = cursor.fetchall()
        
        if not filtros:
            await update.message.reply_text("📋 Nenhum filtro cadastrado no momento.")
            return

        mensagem = "📋 <b>Filtros Ativos:</b>\n\n"
        for row in filtros:
            mensagem += f"📌 <b>{row[0]}</b>\n"
        
        await update.message.reply_text(mensagem, parse_mode="HTML")
    except Exception as e:
        log_sistema("Telegram", f"Erro ao buscar filtros: {e}")
    finally:
        conn.close()


async def comando_remover(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != USUARIO_PERMITIDO:
        return

    if not context.args:
        await update.message.reply_text("❌ Informe o apelido do filtro. Exemplo: <code>/remover Wii</code>", parse_mode="HTML")
        return

    apelido_alvo = context.args[0]

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM urls_monitoradas WHERE apelido = ?", (apelido_alvo,))
        linhas_afetadas = cursor.rowcount
        conn.commit()

        if linhas_afetadas > 0:
            log_sistema("Telegram", f"Filtro removido: {apelido_alvo}")
            await update.message.reply_text(f"❌ Filtro <b>{apelido_alvo}</b> removido com sucesso!", parse_mode="HTML")
        else:
            await update.message.reply_text(f"⚠️ Nenhum filtro encontrado com o apelido <b>{apelido_alvo}</b>.", parse_mode="HTML")
    except Exception as e:
        log_sistema("Telegram", f"Erro ao deletar filtro: {e}")
    finally:
        conn.close()


# ==============================================================================
# MOTOR DE RASPAGEM E ALERTAS
# ==============================================================================
def enviar_alerta_direto(titulo, preco, url, baixa_preco=False, preco_antigo=None):
    url_api = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    
    if not titulo:
        titulo = "Anúncio sem título cadastrado"

    if baixa_preco:
        topo_alerta = f"📉 <b>PREÇO BAIXOU!</b> 📉\n💰 Antes: <s>{preco_antigo}</s> -> <b>Agora: {preco}</b>"
    else:
        topo_alerta = "🚨 <b>NOVO ANÚNCIO ENCONTRADO</b> 🚨"

    mensagem = (
        f"{topo_alerta}\n\n"
        f"📌 <b>{titulo}</b>\n"
        f"💰 <b>Preço:</b> {preco}\n"
        f"🔗 <a href='{url}'>Abrir na OLX</a>"
    )
    payload = {"chat_id": str(USUARIO_PERMITIDO), "text": mensagem, "parse_mode": "HTML"}
    try:
        import requests as normal_requests
        normal_requests.post(url_api, json=payload, timeout=10)
    except Exception as e:
        log_sistema("Notificador", f"Falha ao enviar mensagem ao chat: {e}")


def limpar_string_preco(preco_str):
    try:
        if not preco_str or "Não informado" in preco_str or "N/I" in preco_str:
            return 0.0
        limpo = preco_str.replace("R$", "").replace(".", "").replace(" ", "").strip()
        if "," in limpo:
            limpo = limpo.split(",")[0]
        return float(limpo)
    except Exception:
        return 0.0


def buscar_anuncios(url_alvo):
    try:
        time.sleep(2.0)
        response = curl_requests.get(url_alvo, headers=HEADERS, timeout=15, impersonate="chrome124")
        
        if response.status_code == 502:
            return []
        elif response.status_code != 200:
            log_sistema("Scraper", f"OLX retornou HTTP {response.status_code}")
            return []
            
        soup = BeautifulSoup(response.text, 'html.parser')
        script = soup.find('script', id='__NEXT_DATA__')
        if not script: 
            return []
        
        dados_json = json.loads(script.string)
        page_props = dados_json.get('props', {}).get('pageProps', {})
        
        ads = page_props.get('ads', [])
        if not ads:
            ads = page_props.get('states', {}).get('results', [])
        if not ads:
            ads = page_props.get('initialState', {}).get('search', {}).get('ads', [])

        if not ads:
            return []

        lista_estruturada = []
        for ad in ads:
            if ad.get('listId'):
                titulo_extraido = ad.get('title') or ad.get('subject') or "Anúncio sem título"
                lista_estruturada.append({
                    'id': str(ad.get('listId')),
                    'titulo': titulo_extraido,
                    'preco': ad.get('price', 'N/I'),
                    'url': ad.get('url')
                })
        return lista_estruturada
    except Exception as e: 
        log_sistema("Scraper", f"Timeout ou falha na requisição: {e}")
        return []


def loop_motor_sniper():
    """Loop contínuo executado em Thread secundária para monitorar a OLX."""
    log_sistema("Motor", "Iniciando sincronização e carga inicial...")
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT url FROM urls_monitoradas")
        urls = [row[0] for row in cursor.fetchall()]
        for url in urls:
            for ad in buscar_anuncios(url):
                cursor.execute("INSERT OR IGNORE INTO anuncios_vistos (id, preco) VALUES (?, ?)", (ad['id'], ad['preco']))
        conn.commit()
        conn.close()
        log_sistema("Motor", "Sincronização inicial em background concluída com sucesso.")
    except Exception as e:
        log_sistema("Motor", f"Erro na carga inicial: {e}")

    while True:
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT url, apelido FROM urls_monitoradas")
            filtros_ativos = cursor.fetchall()
        
            if filtros_ativos:
                log_sistema("Motor", f"Iniciando varredura em {len(filtros_ativos)} filtro(s) ativo(s)...")
                for url, apelido in filtros_ativos:
                    anuncios = buscar_anuncios(url)
                
                    for ad in anuncios:
                        cursor.execute("SELECT preco FROM anuncios_vistos WHERE id = ?", (ad['id'],))
                        resultado = cursor.fetchone()
                        
                        if not resultado:
                            log_sistema("Motor", f"🚨 NOVO ({apelido}): {ad['titulo']} por {ad['preco']}")
                            enviar_alerta_direto(f"[{apelido}] {ad['titulo']}", ad['preco'], ad['url'])
                            
                            cursor.execute("INSERT INTO anuncios_vistos (id, preco) VALUES (?, ?)", (ad['id'], ad['preco']))
                            conn.commit()
                        else:
                            preco_antigo_str = resultado[0]
                            preco_atual_num = limpar_string_preco(ad['preco'])
                            preco_antigo_num = limpar_string_preco(preco_antigo_str)
                            
                            if 0.0 < preco_atual_num < preco_antigo_num:
                                log_sistema("Motor", f"📉 PREÇO BAIXOU ({apelido}): {ad['titulo']}")
                                enviar_alerta_direto(f"[{apelido}] {ad['titulo']}", ad['preco'], ad['url'], baixa_preco=True, preco_antigo=preco_antigo_str)
                                
                                cursor.execute("UPDATE anuncios_vistos SET preco = ? WHERE id = ?", (ad['preco'], ad['id']))
                                conn.commit()
            conn.close()

        except Exception as e:
            log_sistema("Motor", f"Erro durante a rodada de varredura: {e}")
            try: conn.close()
            except Exception: pass
            
        intervalo_aleatorio = random.randint(30, 300)
        log_sistema("Motor", f"Varredura finalizada. Próximo ciclo em {intervalo_aleatorio} segundos.")
        time.sleep(intervalo_aleatorio)


# ==============================================================================
# INICIALIZAÇÃO DO SISTEMA
# ==============================================================================
if __name__ == "__main__":
    # Remove instâncias órfãs antigas do processo para liberar o Token
    meu_pid = os.getpid()
    import subprocess
    try:
        output = subprocess.check_output(["pgrep", "-f", "OLX_Sniper.py"]).decode()
        for pid in output.split():
            if int(pid) != meu_pid:
                os.kill(int(pid), 9)
    except Exception:
        pass

    print("=" * 60)
    print("                INICIALIZANDO OLX SNIPER v2.5                 ")
    print("=" * 60)

    # 1. Inicializa o banco local
    inicializar_banco()
    
    # 2. Dispara o Motor da OLX em segundo plano (Thread dedicada)
    thread_sniper = threading.Thread(target=loop_motor_sniper, daemon=True)
    thread_sniper.start()
    
    # 3. Executa o Bot do Telegram diretamente na Thread Principal (Estabilidade Máxima)
    log_sistema("Telegram", "Conectando servidores e iniciando escutador...")
    
    # A biblioteca configura o httpx internamente com suporte a SSL padrão.
    application = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .build()
    )
    
    application.add_handler(CommandHandler("start", comando_start))
    application.add_handler(CommandHandler("adicionar", comando_adicionar))
    application.add_handler(CommandHandler("listar", comando_listar))
    application.add_handler(CommandHandler("remover", comando_remover))
    
    # O bot assume o loop infinito principal de forma nativa e síncrona
    # Passamos os timeouts diretamente no run_polling
    application.run_polling(close_loop=True, bootstrap_retries=-1, timeout=30)