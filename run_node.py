import uvicorn
from dotenv import dotenv_values
config = dotenv_values(".env")

DENARO_NODE_HOST = config.get("DENARO_NODE_HOST", "127.0.0.1")
DENARO_NODE_PORT = int(config.get("DENARO_NODE_PORT", "3006"))

if __name__ == "__main__":
    uvicorn.run("denaro.node.main:app", host=DENARO_NODE_HOST, port=DENARO_NODE_PORT, reload=True)