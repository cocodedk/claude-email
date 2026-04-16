"""Entry point for the claude-chat MCP SSE server.

Reads configuration from environment variables and starts uvicorn.
"""
import logging
import os
import sys

import uvicorn
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("chat_server")


def main() -> None:
    db_path = os.getenv("CHAT_DB_PATH", "claude-chat.db")
    host = os.getenv("CHAT_HOST", "127.0.0.1")
    port = int(os.getenv("CHAT_PORT", "8420"))

    logger.info("Starting claude-chat MCP server on %s:%d", host, port)
    logger.info("Database: %s", db_path)

    from chat.server import create_app
    app = create_app(db_path, host, port)

    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
