"""
olx_sniper.py

Motor de monitoramento (Sniper) automatizado para a plataforma OLX Brasil com integração ao Telegram.

Principais funcionalidades
--------------------------
- Varredura assíncrona e paralela de múltiplos filtros customizados da OLX.
- Detecção automatizada de novos anúncios e de redução de preços de itens existentes.
- Interface visual dinâmica (Dashboard) diretamente no terminal via biblioteca Rich.
- Tolerância a falhas físicas de rede e intervalos aleatórios para evitar rate limit/bloqueios.
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
# Puxa os dados do arquivo .env. Se não encontrar, gera um erro claro no terminal.
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
USUARIO_PERMITIDO = os.getenv("USUARIO_PERMITIDO")

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

# Gerenciadores de estado visual do Rich
console = Console()
status_sistema = "Inicializando..."
ultima_atividade = "Nenhuma atividade registrada."
proxima_varredura = "Calculando..."


def gerar_painel():
    """
    Gera a estrutura visual do painel Rich com marcadores de texto padronizados.

    Garante compatibilidade de tamanho físico estrito e alinhamento geométrico,
    evitando quebras de linhas causadas por renderização de emojis de largura dupla.

    Returns
    -------
    rich.panel.Panel
        Objeto de painel renderizável contendo as estatísticas e status atuais do sistema.
    """
    conteudo = Text()
    
    conteudo.append(">>> OLX SNIPER - DASHBOARD ATIVO\n", style="bold cyan")
    conteudo.append("-" * 56 + "\n", style="bright_black")
    
    conteudo.append("[*] Status Telegram: ", style="bold green")
    conteudo.append("Online & Escutando\n", style="green")
    
    # Tratamento rígido do texto de status do motor
    status_texto = status_sistema
    if "Aguardando próximo ciclo" in status_texto:
        status_texto = "Dormindo (Aguardando...)"
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
        
    evento_formatado = evento_texto.ljust(54)
    conteudo.append(f"{evento_formatado}\n", style="italic white")
    
    return Panel(
        conteudo, 
        border_style="cyan", 
        title="[b]OLX Sniper v2.5[/b]", 
        expand=False, 
        width=60
    )


def registrar_erro(componente, message_erro, excecao=None):
    """
    Registra e formata erros internos do sistema para exibição no dashboard visual.

    Captura a falha sem interromper a execução das threads e atualiza a string global
    de última atividade de maneira limpa e estruturada.

    Parameters
    ----------
    componente : str
        Nome do módulo ou módulo funcional onde o erro ocorreu.
    message_erro : str
        Descrição simplificada da falha identificada.
    excecao : Exception, optional
        Objeto da exceção real capturada pelo bloco try-except (padrão None).

    Returns
    -------
    None
    """
    global ultima_atividade
    horario = time.strftime('%H:%M:%S')
    detalhes = f" ({type(excecao).__name__})" if excecao else ""
    ultima_atividade = f"[{horario}] ERRO: {componente} -> {message_erro}{detalhes}"


def inicializar_banco():
    """
    Inicializa o banco de dados SQLite local e cria as tabelas estruturais.

    Verifica a presença das tabelas de anúncios vistos e URLs monitoradas. Executa uma
    migração automática em cascata para adicionar colunas de preços caso o arquivo .db
    já exista de versões de código legadas.

    Returns
    -------
    None
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
    except Exception as e:
        registrar_erro("DB", "Falha nas tabelas", e)


# ==============================================================================
# COMANDOS DO TELEGRAM (THREAD A)
# ==============================================================================
async def comando_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Manipula o comando /start no chat do Telegram.

    Valida as credenciais do ID do usuário e envia um menu de ajuda formatado em HTML
    contendo os comandos disponíveis do sistema.

    Parameters
    ----------
    update : telegram.Update
        Objeto que representa uma atualização recebida do Telegram.
    context : telegram.ext.ContextTypes.DEFAULT_TYPE
        O contexto da execução atual do comando.

    Returns
    -------
    None
    """
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
    """
    Manipula o comando /adicionar para registrar novos links de filtragem da OLX.

    Salva o par (URL, Apelido) no SQLite de forma atômica e imediatamente faz uma busca
    da URL para preencher e ignorar os anúncios já existentes no momento do cadastro.

    Parameters
    ----------
    update : telegram.Update
        Objeto que representa uma atualização recebida do Telegram.
    context : telegram.ext.ContextTypes.DEFAULT_TYPE
        O contexto contendo os argumentos passados após o comando (`args`).

    Returns
    -------
    None
    """
    global ultima_atividade
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
        
        anuncios_atuais = buscar_anuncios(url_olx)
        for ad in anuncios_atuais:
            cursor.execute("INSERT OR IGNORE INTO anuncios_vistos (id, preco) VALUES (?, ?)", (ad['id'], ad['preco']))
        conn.commit()

        ultima_atividade = f"[{time.strftime('%H:%M:%S')}] ADD: Filtro '{apelido}' criado."
        await update.message.reply_text(
            f"✅ Filtro <b>{apelido}</b> adicionado com sucesso!\n"
            f"📌 {len(anuncios_atuais)} anúncios antigos catalogados e ignorados.", 
            parse_mode="HTML"
        )
    except sqlite3.IntegrityError:
        await update.message.reply_text("❌ Este apelido ou esta URL já está sendo monitorada.")
    except Exception as e:
        registrar_erro("Telegram", "Erro ao salvar URL", e)
    finally:
        conn.close()


async def comando_listar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Manipula o comando /listar enviando todos os apelidos ativos gravados no banco.

    Parameters
    ----------
    update : telegram.Update
        Objeto que representa uma atualização recebida do Telegram.
    context : telegram.ext.ContextTypes.DEFAULT_TYPE
        O contexto da execução atual do comando.

    Returns
    -------
    None
    """
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
        registrar_erro("Telegram", "Erro ao buscar filtros", e)
    finally:
        conn.close()


async def comando_remover(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Manipula o comando /remover deletando um filtro monitorado a partir do seu Apelido.

    Parameters
    ----------
    update : telegram.Update
        Objeto que representa uma atualização recebida do Telegram.
    context : telegram.ext.ContextTypes.DEFAULT_TYPE
        O contexto contendo o argumento do apelido em `args[0]`.

    Returns
    -------
    None
    """
    global ultima_atividade
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
            ultima_atividade = f"[{time.strftime('%H:%M:%S')}] DEL: Filtro '{apelido_alvo}' removido."
            await update.message.reply_text(f"❌ Filtro <b>{apelido_alvo}</b> removido com sucesso!", parse_mode="HTML")
        else:
            await update.message.reply_text(f"⚠️ Nenhum filtro encontrado com o apelido <b>{apelido_alvo}</b>.", parse_mode="HTML")
    except Exception as e:
        registrar_erro("Telegram", "Erro ao deletar filtro", e)
    finally:
        conn.close()


def rodar_escutador_telegram():
    """
    Inicializa o loop de polling assíncrono para os comandos do bot do Telegram.

    Configura timeouts customizados e ignora de maneira forçada verificações estritas de
    SSL caso ocorram divergências de infraestrutura de rede local no Linux.

    Returns
    -------
    None
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    requisicao_customizada = None
    
    try:
        requisicao_customizada = HTTPXRequest(
            backend_kwargs={"verify": False},
            read_timeout=20.0,
            connect_timeout=40.0
        )
    except Exception:
        pass

    builder = Application.builder().token(TELEGRAM_TOKEN)
    if requisicao_customizada:
        builder.request(requisicao_customizada)
        
    application = builder.build()
    application.add_handler(CommandHandler("start", comando_start))
    application.add_handler(CommandHandler("adicionar", comando_adicionar))
    application.add_handler(CommandHandler("listar", comando_listar))
    application.add_handler(CommandHandler("remover", comando_remover))
    
    application.run_polling(close_loop=False, bootstrap_retries=-1, timeout=30)


# ==============================================================================
# MOTOR DO SNIPER / SCRAPER (THREAD B)
# ==============================================================================
def enviar_alerta_direto(titulo, preco, url, baixa_preco=False, preco_antigo=None):
    """
    Despacha mensagens de notificação estruturadas em HTML diretamente ao Telegram do usuário.

    Diferencia visualmente alertas padrões de "Novo Item Encontrado" de atualizações
    específicas de "Preço Baixou" contendo tags de rasura no valor antigo.

    Parameters
    ----------
    titulo : str
        Título do anúncio que será exibido no card.
    preco : str
        Preço do anúncio formatado como texto (ex: 'R$ 850').
    url : str
        URL direta do anúncio na OLX.
    baixa_preco : bool, optional
        Sinalizador indicando se o card refere-se a uma queda de preço (padrão False).
    preco_antigo : str, optional
        Preço anterior armazenado antes da redução (padrão None).

    Returns
    -------
    None
    """
    url_api = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    
    if not titulo:
        titulo = "Anuncio sem titulo cadastrado"

    if baixa_preco:
        topo_alerta = f"📉 <b>PRECO BAIXOU!</b> 📉\n💰 Antes: <s>{preco_antigo}</s> -> <b>Agora: {preco}</b>"
    else:
        topo_alerta = "🚨 <b>NOVO ANUNCIO ENCONTRADO</b> 🚨"

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
        registrar_erro("Notificador", "Falha no card Telegram", e)


def limpar_string_preco(preco_str):
    """
    Limpa strings de valores monetários da OLX e as converte em floats comparáveis.

    Filtra caracteres de texto como 'R$', espaços em branco e pontos de milhar,
    descartando frações centavós para evitar ruídos de arredondamento.

    Parameters
    ----------
    preco_str : str
        String original de preço extraída da raspagem.

    Returns
    -------
    float
        Valor numérico puro convertido. Retorna 0.0 se não for informada uma quantia válida.
    """
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
    """
    Efetua a raspagem HTTP da página e mapeia a árvore JSON oculta de anúncios da OLX.

    Utiliza `curl_cffi` para simular as assinaturas criptográficas TLS do Chrome,
    contornando erros 403. Varre as três rotas de nós estruturais do `__NEXT_DATA__`
    e resolve variações entre chaves dinâmicas como 'title' e 'subject'.

    Parameters
    ----------
    url_alvo : str
        A URL de listagem de busca da OLX que será analisada.

    Returns
    -------
    list of dict
        Lista contendo dicionários estruturados de anúncios encontrados, contendo
        chaves de 'id', 'titulo', 'preco' e 'url'. Retorna [] em caso de erros ou HTTP 502.
    """
    try:
        time.sleep(2.0)
        response = curl_requests.get(url_alvo, headers=HEADERS, timeout=15, impersonate="chrome124")
        
        if response.status_code == 502:
            return []
        elif response.status_code != 200:
            registrar_erro("Scraper", f"OLX retornou HTTP {response.status_code}")
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
                titulo_extraido = ad.get('title') or ad.get('subject') or "Anuncio sem titulo"
                lista_estruturada.append({
                    'id': str(ad.get('listId')),
                    'titulo': titulo_extraido,
                    'preco': ad.get('price', 'N/I'),
                    'url': ad.get('url')
                })
        return lista_estruturada
    except Exception as e: 
        registrar_erro("Scraper", "Timeout ou falha de conexao", e)
        return []


def loop_motor_sniper():
    """
    Executa o loop infinito de monitoramento (Thread B) em background.

    Gerencia o ciclo de vida das conexões locais do SQLite. Compara IDs e preços atuais
    com dados históricos, efetuando comandos INSERT para novos itens e comandos UPDATE
    para variações econômicas de redução de preço.

    Returns
    -------
    None
    """
    global status_sistema, ultima_atividade, proxima_varredura
    
    status_sistema = "Sincronizando banco..."
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
        ultima_atividade = f"[{time.strftime('%H:%M:%S')}] Sincronizacao concluida."
    except Exception as e:
        registrar_erro("Motor", "Erro na carga inicial", e)

    while True:
        try:
            status_sistema = "Varrendo a OLX..."
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT url, apelido FROM urls_monitoradas")
            filtros_ativos = cursor.fetchall()
        
            if filtros_ativos:
                for url, apelido in filtros_ativos:
                    anuncios = buscar_anuncios(url)
                
                    for ad in anuncios:
                        cursor.execute("SELECT preco FROM anuncios_vistos WHERE id = ?", (ad['id'],))
                        resultado = cursor.fetchone()
                        
                        if not resultado:
                            ultima_atividade = f"[{time.strftime('%H:%M:%S')}] NOVO ({apelido}): {ad['titulo']}"
                            enviar_alerta_direto(f"[{apelido}] {ad['titulo']}", ad['preco'], ad['url'])
                            
                            cursor.execute("INSERT INTO anuncios_vistos (id, preco) VALUES (?, ?)", (ad['id'], ad['preco']))
                            conn.commit()
                        else:
                            preco_antigo_str = resultado[0]
                            preco_atual_num = limpar_string_preco(ad['preco'])
                            preco_antigo_num = limpar_string_preco(preco_antigo_str)
                            
                            if 0.0 < preco_atual_num < preco_antigo_num:
                                ultima_atividade = f"[{time.strftime('%H:%M:%S')}] BAIXOU ({apelido}): {ad['titulo']}"
                                enviar_alerta_direto(f"[{apelido}] {ad['titulo']}", ad['preco'], ad['url'], baixa_preco=True, preco_antigo=preco_antigo_str)
                                
                                cursor.execute("UPDATE anuncios_vistos SET preco = ? WHERE id = ?", (ad['preco'], ad['id']))
                                conn.commit()
            conn.close()

        except Exception as e:
            registrar_erro("Motor", "Erro na rodada", e)
            try: conn.close()
            except Exception: pass
            
        status_sistema = "Dormindo..."
        intervalo_aleatorio = random.randint(180, 300)
        
        for restante in range(intervalo_aleatorio, 0, -1):
            proxima_varredura = f"{restante // 60}m {restante % 60}s ({intervalo_aleatorio}s)"
            time.sleep(1)


# ==============================================================================
# INICIALIZAÇÃO DO SISTEMA
# ==============================================================================
if __name__ == "__main__":
    inicializar_banco()
    
    # Executa o comando clear do linux para remover sujeiras visuais antigas
    os.system('clear' if os.name == 'posix' else 'cls')
    
    thread_sniper = threading.Thread(target=loop_motor_sniper, daemon=True)
    thread_sniper.start()

    # Cria o contexto de tela alternativa fixa (screen=True) gerenciado pelo Live
    with Live(gerar_painel(), screen=True, auto_refresh=True) as live:
        
        while True:
            live.update(gerar_painel())
            time.sleep(1)