"""
ws_manager.py — Motor Gelato
==============================

Gerenciador de conexões WebSocket para comunicação em tempo real entre
os módulos do sistema (Balcão ↔ Cozinha ↔ Gestão).

Design decision — Por que WebSockets?
    O FastAPI suporta nativamente WebSockets via Starlette. Usamos esse
    mecanismo em vez de polling HTTP porque:
    1. Latência: alertas de reposição chegam instantaneamente à cozinha.
    2. Eficiência: sem overhead de requisições repetidas a cada N segundos.
    3. Bidirecionalidade: o servidor pode empurrar dados sem solicitação.

    A arquitetura é mantida simples (sem pub/sub externo como Redis) pois
    o sistema opera em rede local (LAN), não requerendo escala horizontal.
"""

from fastapi import WebSocket
from typing import Any


class ConnectionManager:
    """Gerencia o ciclo de vida das conexões WebSocket ativas.

    Mantém uma lista em memória de todas as conexões abertas e provê
    métodos para conectar, desconectar e fazer broadcast de mensagens JSON.

    Attributes:
        active_connections: Lista de WebSockets atualmente conectados.

    Example:
        manager = ConnectionManager()

        @app.websocket("/ws/producao")
        async def ws_endpoint(websocket: WebSocket):
            await manager.connect(websocket)
            try:
                while True:
                    await websocket.receive_text()
            except WebSocketDisconnect:
                manager.disconnect(websocket)
    """

    def __init__(self) -> None:
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        """Aceita e registra uma nova conexão WebSocket.

        Args:
            websocket: A conexão WebSocket recém-aberta pelo cliente.
        """
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        """Remove uma conexão encerrada da lista de ativos.

        Args:
            websocket: A conexão WebSocket que foi fechada ou perdeu sinal.
        """
        self.active_connections.remove(websocket)

    async def broadcast(self, message: dict[str, Any]) -> None:
        """Envia uma mensagem JSON para todos os clientes conectados.

        Itera sobre uma cópia da lista de conexões (``active_connections.copy()``)
        para evitar erros de mutação caso uma conexão seja removida durante
        a iteração assíncrona — condição de corrida possível em ambientes
        com múltiplos clientes conectando/desconectando simultaneamente.

        Em caso de falha no envio (cliente desconectado silenciosamente),
        a conexão é removida automaticamente da lista para evitar acúmulo
        de conexões zumbi.

        Args:
            message: Dicionário que será serializado como JSON e enviado
                     a todos os clientes. Exemplos de chaves de ação:
                     ``{"acao": "piscar", "sabor": "PRODUTO A"}``
                     ``{"acao": "produzido", "sabor": "PRODUTO A"}``
                     ``{"acao": "atualizar_cardapio"}``
        """
        for connection in self.active_connections.copy():
            try:
                await connection.send_json(message)
            except Exception:
                # Conexão morta: remove silenciosamente para evitar acúmulo.
                self.disconnect(connection)


# Instância global do gerenciador — compartilhada entre todos os endpoints.
manager = ConnectionManager()

# Conjunto em memória de alertas ativos de reposição.
# Mantido em RAM (não no banco de dados) por ser estado efêmero:
# um alerta é criado pelo balcão e consumido quando a cozinha produz.
# Em caso de reinicialização do servidor, os alertas são perdidos
# (comportamento aceitável — o balcão pode reativar manualmente).
alertas_ativos: set[str] = set()
