"""Real-time speech-invoice-capture — WS /api/invoices/voice-line/ws.

A thin proxy: browser audio chunks in, Deepgram partial/final transcript
JSON out, unmodified. The Deepgram secret key stays server-side; the
browser never sees it. See app/services/speech/stream.py for the Deepgram
side of the proxy and why containerised (webm/opus) audio needs no extra
encoding params.

WebSocket auth: a browser's native WebSocket API can't set a custom
Authorization header on connect, so the access token travels as a query
param instead (`?token=...`) and is decoded the same way `get_current_user`
decodes a bearer token for ordinary requests.
"""

from __future__ import annotations

import asyncio
import contextlib

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from sqlalchemy import select

from app.db import SessionLocal
from app.models import User
from app.security import JWTError, decode_access_token
from app.services.speech.keyterms import top_item_keyterms
from app.services.speech.stream import StreamNotConfigured, open_deepgram_stream

router = APIRouter(prefix="/api/invoices", tags=["invoices"])


def _authenticate(token: str) -> User | None:
    try:
        payload = decode_access_token(token)
    except JWTError:
        return None
    user_id = payload.get("sub")
    if not user_id:
        return None
    with SessionLocal() as session:
        user = session.scalar(select(User).where(User.id == user_id))
        if user is None or not user.is_active:
            return None
        session.expunge(user)
        return user


@router.websocket("/voice-line/ws")
async def voice_line_stream(ws: WebSocket, token: str = "") -> None:
    user = _authenticate(token)
    if user is None:
        await ws.close(code=status.WS_1008_POLICY_VIOLATION, reason="Unauthorized")
        return

    with SessionLocal() as session:
        keyterms = top_item_keyterms(session, user.tenant_id)

    await ws.accept()

    try:
        dg = await open_deepgram_stream(keyterms)
    except StreamNotConfigured as exc:
        await ws.send_json({"type": "Error", "message": str(exc)})
        await ws.close(code=status.WS_1011_INTERNAL_ERROR)
        return

    async def browser_to_deepgram() -> None:
        try:
            while True:
                chunk = await ws.receive_bytes()
                await dg.send(chunk)
        except WebSocketDisconnect:
            pass
        finally:
            with contextlib.suppress(Exception):  # best-effort teardown
                await dg.close()

    async def deepgram_to_browser() -> None:
        # Deepgram socket closed/errored — let the other task finish instead
        # of raising here.
        with contextlib.suppress(Exception):
            async for message in dg:
                text = message if isinstance(message, str) else message.decode("utf-8", "ignore")
                await ws.send_text(text)

    try:
        await asyncio.gather(browser_to_deepgram(), deepgram_to_browser())
    finally:
        with contextlib.suppress(Exception):  # already closed
            await ws.close()
