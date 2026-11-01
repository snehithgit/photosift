from starlette.responses import JSONResponse


class BoundedBody:
    """Reject oversized requests before JSON/base64 parsing; never trust Content-Length."""

    def __init__(self, app, limit=16 * 1024 * 1024):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in ("POST", "PATCH", "PUT"):
            return await self.app(scope, receive, send)
        chunks, total = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            total += len(chunk)
            if total > self.limit:
                return await JSONResponse(
                    {"detail": "Request exceeds the bounded payload limit"},
                    status_code=413,
                )(scope, receive, send)
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        consumed = False

        async def bounded_receive():
            nonlocal consumed
            if consumed:
                return await receive()
            consumed = True
            return {
                "type": "http.request",
                "body": b"".join(chunks),
                "more_body": False,
            }

        return await self.app(scope, bounded_receive, send)
