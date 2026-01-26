import uvicorn
import logging
from denaro.constants import DENARO_NODE_HOST, DENARO_NODE_PORT
# Suppress uvicorn's default logging and warnings
# Configure uvicorn loggers to suppress WARNING messages
uvicorn_loggers = [
    logging.getLogger("uvicorn"),
    logging.getLogger("uvicorn.error"),
    logging.getLogger("uvicorn.access"),
    logging.getLogger("uvicorn.asgi"),
]

for uvicorn_logger in uvicorn_loggers:
    # Only show ERROR and CRITICAL, suppress WARNING
    uvicorn_logger.setLevel(logging.ERROR) 
    # Remove handlers to prevent duplicate output
    uvicorn_logger.handlers = []

if __name__ == "__main__":
    uvicorn.run(
        "denaro.node.main:app", 
        host=DENARO_NODE_HOST, 
        port=DENARO_NODE_PORT, 
        reload=False,
        access_log=False,
        log_config=None
    )