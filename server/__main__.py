import uvicorn

if __name__ == "__main__":
    # Loopback by default. LAN deployment must provide its own trusted access boundary.
    uvicorn.run("server.app:create_app", factory=True, host="127.0.0.1", port=8000)
