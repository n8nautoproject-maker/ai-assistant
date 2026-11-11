"""Speech-to-text adapter (Yandex SpeechKit) - pure HTTP client, no
database access. Uses its own YANDEX_API_KEY/YANDEX_FOLDER_ID, independent
of whichever LLM_PROVIDER is currently configured (a Yandex Cloud AI
Studio key works for both LLM and SpeechKit, but LLM_PROVIDER can point
elsewhere - e.g. DeepSeek - while SpeechKit still needs a real Yandex key).
"""

import asyncio
import os
import tempfile

import httpx

from app.config import get_settings

SHORT_API_URL = "https://stt.api.cloud.yandex.net/speech/v1/stt:recognize"

# The short/sync endpoint hard-rejects audio past ~30s - confirmed live
# ("audio duration should be less than 30s" from the API itself) on a real
# 54s voice message that otherwise got a bare "couldn't transcribe" with no
# explanation. Yandex's long-running (async) recognition has no such cap,
# but requires the audio to already be sitting in Yandex Object Storage -
# also confirmed live ("you should specify not empty audio URI", inline
# base64 content is rejected outright by that endpoint). Standing up a
# whole Object Storage bucket + service account just for this wasn't worth
# it: splitting into sub-30s chunks with ffmpeg (already installed on the
# server) and calling the SAME already-working short endpoint per chunk
# needs no new external infrastructure or credentials at all.
CHUNK_SECONDS = 25  # safety margin under the 30s cap


class SpeechToTextError(Exception):
    pass


async def transcribe_browser_audio(audio_bytes: bytes, lang: str = "ru-RU") -> str:
    """Convert Mini App microphone audio to Ogg/Opus via ffmpeg before
    reusing the short-recognize path already proven with Telegram voice
    messages. MediaRecorder's output container varies by browser/OS
    (webm/opus on Chromium, mp4/aac on Safari/WKWebView) and SpeechKit
    only reliably accepts what Telegram already sends, so we normalize
    instead of gambling on format auto-detection.
    """
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = os.path.join(tmpdir, "input")
            output_path = os.path.join(tmpdir, "output.ogg")
            with open(input_path, "wb") as f:
                f.write(audio_bytes)
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-y", "-i", input_path,
                "-c:a", "libopus", output_path,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
            if proc.returncode != 0 or not os.path.exists(output_path):
                raise SpeechToTextError("audio conversion failed")
            with open(output_path, "rb") as f:
                converted = f.read()
    except OSError as exc:
        raise SpeechToTextError(f"audio conversion failed: {exc}") from exc
    return await transcribe(converted, lang)


async def transcribe(audio_bytes: bytes, lang: str = "ru-RU") -> str:
    try:
        return await _transcribe_short(audio_bytes, lang)
    except SpeechToTextError:
        chunks = await _split_audio(audio_bytes)
        if len(chunks) <= 1:
            # Splitting didn't produce more than one piece - the original
            # failure wasn't a length problem, re-raise the real error
            # instead of masking it behind a second, identical failure.
            raise
        texts = [await _transcribe_short(chunk, lang) for chunk in chunks]
        return " ".join(t for t in texts if t)


async def _transcribe_short(audio_bytes: bytes, lang: str) -> str:
    settings = get_settings()
    async with httpx.AsyncClient() as client:
        response = await client.post(
            SHORT_API_URL,
            params={"topic": "general", "folderId": settings.yandex_speechkit_folder_id, "lang": lang},
            content=audio_bytes,
            headers={"Authorization": f"Api-Key {settings.yandex_api_key}"},
        )
    if response.status_code >= 400:
        raise SpeechToTextError(f"transcribe failed: {response.status_code} {response.text}")
    data = response.json()
    if data.get("error_code"):
        raise SpeechToTextError(f"transcribe failed: {data}")
    return data.get("result", "")


async def _get_duration_seconds(path: str) -> float | None:
    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", path,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await proc.communicate()
    try:
        return float(stdout.decode().strip())
    except ValueError:
        return None


async def _split_audio(audio_bytes: bytes) -> list[bytes]:
    """Raises SpeechToTextError (not a raw OSError) if ffmpeg/ffprobe
    themselves are unavailable - previously a missing binary
    (FileNotFoundError from create_subprocess_exec) propagated straight
    out of transcribe(), past the only except clause bot.py has for this
    call, producing total silence instead of the honest "couldn't
    transcribe" message every other failure mode here already gets.
    """
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = os.path.join(tmpdir, "input.ogg")
            with open(input_path, "wb") as f:
                f.write(audio_bytes)

            duration = await _get_duration_seconds(input_path)
            if duration is None:
                return [audio_bytes]

            chunk_paths = []
            start = 0.0
            i = 0
            while start < duration:
                chunk_path = os.path.join(tmpdir, f"chunk_{i}.ogg")
                proc = await asyncio.create_subprocess_exec(
                    "ffmpeg", "-y", "-i", input_path,
                    "-ss", str(start), "-t", str(CHUNK_SECONDS),
                    "-c:a", "libopus", chunk_path,
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                )
                await proc.wait()
                if proc.returncode == 0 and os.path.exists(chunk_path):
                    chunk_paths.append(chunk_path)
                start += CHUNK_SECONDS
                i += 1

            if not chunk_paths:
                return [audio_bytes]

            chunks = []
            for path in chunk_paths:
                with open(path, "rb") as f:
                    chunks.append(f.read())
            return chunks
    except OSError as exc:
        raise SpeechToTextError(f"audio splitting failed: {exc}") from exc
