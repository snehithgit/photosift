import uvicorn

from laptop.config import load_settings

if __name__ == "__main__":
    settings = load_settings()
    uvicorn.run(
        "laptop.app:create_app",
        factory=True,
        host=settings.bindHost,
        port=settings.port,
        access_log=False,
    )
