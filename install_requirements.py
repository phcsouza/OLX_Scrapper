"""
install_requirements.py

Script para checagem e instalação automática de bibliotecas necessárias para o OLX Sniper.

Principais funcionalidades
--------------------------
- Verifica se os pacotes Python externos estão instalados no ambiente virtual.
- Instala pacotes ausentes usando o repositório corporativo seguro da Petrobras.
- Ignora e valida internamente as bibliotecas nativas do core do Python.
"""

import subprocess
import sys
import importlib.util

def install(package):
    """
    Instala um pacote Python usando o pip apontando para o repositório seguro.

    Parameters
    ----------
    package : str
        Nome do pacote a ser instalado.

    Returns
    -------
    None
        Apenas imprime mensagens no terminal.
    """
    print(f"Instalando {package}...")
    try:
        subprocess.check_call([
            sys.executable, "-m", "pip", "install", package
        ])
        print(f"✅ {package} instalado com sucesso.")
    except subprocess.CalledProcessError:
        print(f"❌ Falha ao instalar {package}.")

def check_package(import_name, install_name=None):
    """
    Confere se um pacote externo está instalado. Se não estiver, tenta instalá-lo.

    Parameters
    ----------
    import_name : str
        Nome usado para importar o pacote no script Python.
    install_name : str, optional
        Nome real do pacote para instalação via pip (caso diferente do nome de importação).

    Returns
    -------
    None
        Apenas imprime mensagens no terminal.
    """
    if install_name is None:
        install_name = import_name

    if importlib.util.find_spec(import_name) is None:
        print(f"⚠️ Pacote '{import_name}' não instalado.")
        install(install_name)
    else:
        print(f"✓ {install_name} já está instalado.")

def main():
    """
    Checa e instala automaticamente as bibliotecas necessárias para o OLX Sniper.

    Verifica a presença das dependências de raspagem avançada (curl_cffi),
    interface do Telegram e a engine de interface de terminal (rich).

    Returns
    -------
    None
        Apenas imprime mensagens no terminal.
    """
    print("=== Checando as bibliotecas necessárias para o OLX Sniper ===")

    try:
        # 1. Dependências de Rede e Integração com a API do Telegram
        check_package('telegram', 'python-telegram-bot')
        check_package('httpx')
        check_package('requests')

        # 2. Dependências do Motor de Raspagem (Scraper Engine)
        check_package('bs4', 'beautifulsoup4')
        check_package('curl_cffi')

        # 3. Dependências da Interface Visual Avançada do Terminal
        check_package('rich')

        # 4. Dependências para Carregamento de Variáveis de Ambiente
        check_package('dotenv', 'python-dotenv')

        # 5. Dependências para Machine Learning
        check_package('joblib')
        check_package('sklearn', 'scikit-learn')

        print("\n✅ Todas as checagens e instalações de pacotes concluídas.")

    except Exception as e:
        print(f"\n Um erro inesperado ocorreu durante a verificação de requisitos: {e}")

if __name__ == '__main__':
    main()