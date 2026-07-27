"""
main.py — Motor Gelato
=======================

Ponto de entrada da aplicação FastAPI do Motor Gelato.

Responsabilidades deste módulo:
    - Instanciar a aplicação FastAPI com metadados de documentação.
    - Configurar o middleware CORS para permitir acesso cross-origin
      (necessário para tablets e celulares na mesma rede local).
    - Registrar as rotas de arquivos HTML e recursos estáticos.
    - Configurar o endpoint WebSocket de tempo real.
    - Inicializar o banco de dados na primeira execução.
    - Incluir o router com todos os endpoints da API.
"""

import os
import multiprocessing
import webbrowser

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from config_db import iniciar_banco, base_path
from ws_manager import manager
from routes import router


# ==========================================
# INSTÂNCIA DA APLICAÇÃO
# ==========================================

app = FastAPI(
    title="Motor Gelato",
    description=(
        "ERP open-source para gestão de estoque em 3 níveis: "
        "Almoxarifado → Cozinha → Vitrine. "
        "Inclui MRP inteligente com burn rate histórico, "
        "importação de NF-e e comunicação em tempo real via WebSocket."
    ),
    version="2.0.0",
    contact={
        "name": "Motor Gelato",
        "url": "https://github.com/saldanha55/motor-gelato",
    },
    license_info={
        "name": "MIT",
    },
)


# ==========================================
# CORS — CROSS-ORIGIN RESOURCE SHARING
# ==========================================

# Permitimos todas as origens pois o sistema opera em rede local (LAN).
# Em produção web, substitua allow_origins=["*"] pelos domínios específicos.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==========================================
# INICIALIZAÇÃO DO BANCO DE DADOS
# ==========================================

# Executado uma única vez na inicialização. Cria tabelas e aplica migrações.
# É seguro chamar repetidamente — todas as operações usam IF NOT EXISTS.
iniciar_banco()


# ==========================================
# ROTAS — PÁGINAS HTML
# ==========================================

@app.get("/", include_in_schema=False)
async def raiz():
    """Serve a página inicial (Hub de módulos do sistema).

    Returns:
        FileResponse: Arquivo index.html com o hub de navegação.
    """
    return FileResponse(os.path.join(base_path, "index.html"))


@app.get("/estoque", include_in_schema=False)
async def pagina_estoque():
    """Serve o módulo de Gestão de Estoque (Almoxarifado + Cozinha + MRP).

    Returns:
        FileResponse: Arquivo estoque.html com a interface completa de gestão.
    """
    return FileResponse(os.path.join(base_path, "estoque.html"))


@app.get("/dashboard", include_in_schema=False)
async def pagina_dashboard():
    """Serve o Dashboard Operacional com visão consolidada e tempo real.

    Exibe KPIs, pipeline de 3 níveis, alertas de compra, produção do dia,
    transferências recentes e histórico de entradas em um painel unificado.
    Atualiza automaticamente a cada 30 segundos.

    Returns:
        FileResponse: Arquivo dashboard.html com o painel operacional.
    """
    return FileResponse(os.path.join(base_path, "dashboard.html"))


@app.get("/balcao", include_in_schema=False)
async def pagina_balcao():
    """Serve o módulo de Frente de Loja (PDV / Vitrine).

    Returns:
        FileResponse: Arquivo balcao.html com controle de vitrine.
    """
    return FileResponse(os.path.join(base_path, "balcao.html"))


@app.get("/producao", include_in_schema=False)
async def pagina_producao():
    """Serve o módulo de Cozinha e Produção.

    Returns:
        FileResponse: Arquivo producao.html com grade de sabores e teclado.
    """
    return FileResponse(os.path.join(base_path, "producao.html"))


@app.get("/verificador", include_in_schema=False)
async def pagina_verificador():
    """Serve a tela de verificação de estoque.

    Returns:
        FileResponse: Arquivo verificador.html.
    """
    return FileResponse(os.path.join(base_path, "verificador.html"))


# ==========================================
# ROTAS — ARQUIVOS ESTÁTICOS (CSS e JS)
# ==========================================

# StaticFiles é mais seguro que rotas manuais pois o Starlette lida
# internamente com path traversal, ETags e cache headers corretos.
# app.mount() é registrado após include_router para não conflitar.


# ==========================================
# WEBSOCKET — COMUNICAÇÃO EM TEMPO REAL
# ==========================================

@app.websocket("/ws/producao")
async def websocket_producao(websocket: WebSocket):
    """Endpoint WebSocket para comunicação em tempo real entre Cozinha e Balcão.

    Mantém a conexão aberta e delega o gerenciamento ao ConnectionManager.
    Ao desconectar, o cliente é removido da lista de conexões ativas.

    Mensagens recebidas são ignoradas (o canal é unidirecional:
    servidor → clientes via broadcast). O ``receive_text()`` serve apenas
    para manter a conexão viva e detectar desconexões.

    Args:
        websocket: Conexão WebSocket estabelecida pelo cliente.
    """
    await manager.connect(websocket)
    try:
        while True:
            # Aguarda qualquer mensagem do cliente.
            # Em caso de desconexão, WebSocketDisconnect é levantado.
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)


# ==========================================
# INCLUSÃO DAS ROTAS DA API
# ==========================================

# Todos os endpoints REST definidos em routes.py são registrados aqui.
# O FastAPI mescla o router com a aplicação principal de forma transparente.
app.include_router(router)

# Monta os diretórios de recursos estáticos DEPOIS do router para evitar
# conflitos de prefixo. O Starlette/StaticFiles lida com path traversal,
# ETags, Content-Type e cache headers automaticamente.
app.mount("/css", StaticFiles(directory=os.path.join(base_path, "css")), name="css")
app.mount("/js",  StaticFiles(directory=os.path.join(base_path, "js")),  name="js")


# ==========================================
# PONTO DE ENTRADA (EXECUÇÃO DIRETA)
# ==========================================

if __name__ == "__main__":
    # freeze_support() é necessário para compatibilidade com executáveis
    # gerados pelo PyInstaller no Windows (multiprocessing).
    multiprocessing.freeze_support()

    print("🚀 Motor Gelato iniciando em: http://localhost:8050")
    print(f"📁 Diretório de trabalho: {base_path}")
    print("📖 Documentação da API: http://localhost:8050/docs")

    webbrowser.open("http://localhost:8050/")
    uvicorn.run(app, host="0.0.0.0", port=8050)
