import os

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "app.web:create_app",
        factory=True,
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8080")),
        access_log=False,
        limit_concurrency=64,
        timeout_keep_alive=5,
        timeout_graceful_shutdown=45,
    )
