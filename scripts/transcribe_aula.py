"""Transcreve aulas do SmartPlayer: baixa só a trilha de áudio (HLS) e roda faster-whisper.

Uso:
    python -X utf8 scripts/transcribe_aula.py <sp_media_code> [<sp_media_code> ...]
    python -X utf8 scripts/transcribe_aula.py --model medium <code>

Saída: transcricoes/<pasta do manifest>/<título>.md (frontmatter + blocos com timestamp).
"""
import argparse
import asyncio
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, ".")  # roda como script solto, não como -m; precisa achar src/
from src.config import Settings
from src.smartplayer_client import SmartPlayerClient

ROOT = Path(__file__).resolve().parent.parent
TEMP_AUDIO_DIR = ROOT / "data" / "temp_audio"
OUT_DIR = ROOT / "transcricoes"
BLOCK_SECONDS = 60  # agrupa segmentos em blocos de ~1 min para chunking


def sanitize(name: str) -> str:
    name = re.sub(r"\.mp4$", "", name, flags=re.I)
    return re.sub(r'[<>:"/\\|?*]', "-", name).strip()


def load_manifest_meta(code: str) -> dict:
    m = json.loads((ROOT / "data" / "manifest.json").read_text(encoding="utf-8"))
    for v in m["videos"].values():
        if v.get("sp_media_code") == code:
            return v
    return {}


async def fetch_audio(code: str, sp: SmartPlayerClient, client: httpx.AsyncClient) -> tuple[Path, dict]:
    # usa os internos do SmartPlayerClient (_authed_headers/_base_url) porque ele não
    # expõe GET /medias/{code}; se a API do client mudar, é aqui que quebra primeiro
    h = await sp._authed_headers()
    r = await client.get(f"{sp._base_url}/medias/{code}", headers=h)
    r.raise_for_status()
    media = r.json()

    # o ganho do script mora aqui: o HLS do SP publica a trilha de áudio como
    # renderização separada, então dá pra baixar só ela em vez do vídeo inteiro
    playlist = await client.get(media["video"])
    playlist.raise_for_status()
    m = re.search(r'#EXT-X-MEDIA:TYPE=AUDIO.*?URI="([^"]+)"', playlist.text)
    if not m:
        raise RuntimeError(f"{code}: playlist sem trilha de áudio separada")
    audio_m3u8 = m.group(1)

    TEMP_AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    out = TEMP_AUDIO_DIR / f"{code}.m4a"
    # -c copy: remuxa sem reencodar (o whisper resample sozinho); -vn descarta vídeo residual
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", audio_m3u8, "-vn", "-c", "copy", str(out)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg falhou: {proc.stderr[-500:]}")
    return out, media


def transcribe(audio_path: Path, model) -> list[dict]:
    # language fixo em pt evita o whisper "detectar" inglês em aula com muito termo técnico;
    # vad_filter corta silêncio (aula tem pausa longa) e derruba bastante o tempo total
    segments, info = model.transcribe(
        str(audio_path), language="pt", vad_filter=True, beam_size=5,
    )
    out = []
    for s in segments:  # generator preguiçoso: só aqui a transcrição de fato roda
        out.append({"start": s.start, "end": s.end, "text": s.text.strip()})
    return out


def to_markdown(meta: dict, media: dict, segs: list[dict], model_name: str) -> str:
    title = sanitize(meta.get("title") or media.get("name", ""))
    folder = meta.get("panda_folder", "")
    dur = media.get("durationValue") or ""
    embed = f"https://player.scaleup.com.br/embed/{media['code']}"
    lines = [
        "---",
        f'titulo: "{title}"',
        f'modulo: "{folder}"',
        f"sp_media_code: {media['code']}",
        f"embed_url: {embed}",
        f"duracao: {dur}",
        f"transcrito_em: {time.strftime('%Y-%m-%d')}",
        f"modelo: faster-whisper {model_name} (pt, vad)",
        "---",
        "",
        f"# {title}",
        "",
    ]
    block_start, block_texts = None, []
    for s in segs:
        if block_start is None:
            block_start = s["start"]
        block_texts.append(s["text"])
        if s["end"] - block_start >= BLOCK_SECONDS:
            mm, ss = divmod(int(block_start), 60)
            hh, mm = divmod(mm, 60)
            lines.append(f"## [{hh:02d}:{mm:02d}:{ss:02d}]")
            lines.append(" ".join(block_texts))
            lines.append("")
            block_start, block_texts = None, []
    if block_texts:
        mm, ss = divmod(int(block_start), 60)
        hh, mm = divmod(mm, 60)
        lines.append(f"## [{hh:02d}:{mm:02d}:{ss:02d}]")
        lines.append(" ".join(block_texts))
        lines.append("")
    return "\n".join(lines)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("codes", nargs="+")
    ap.add_argument("--model", default="large-v3-turbo")
    ap.add_argument("--keep-audio", action="store_true")
    args = ap.parse_args()

    # import tardio: carregar faster_whisper custa segundos, não paga em --help nem em erro de arg
    from faster_whisper import WhisperModel
    # int8 na CPU: sem GPU aqui, e a perda de acurácia em pt não justifica float32
    print(f"Carregando modelo {args.model} (CPU int8)...")
    t0 = time.time()
    model = WhisperModel(args.model, device="cpu", compute_type="int8")
    print(f"Modelo carregado em {time.time()-t0:.0f}s")

    s = Settings.from_env()
    sp = SmartPlayerClient(s.sp_client_id, s.sp_client_secret, s.sp_user_code,
                           ROOT / "data" / "token_cache.json")
    async with sp:
        async with httpx.AsyncClient(timeout=60, http2=True) as client:
            for code in args.codes:
                meta = load_manifest_meta(code)
                t1 = time.time()
                audio, media = await fetch_audio(code, sp, client)
                t_dl = time.time() - t1
                size_mb = audio.stat().st_size / 2**20
                print(f"[{code[:8]}] áudio baixado: {size_mb:.0f} MB em {t_dl:.0f}s")

                t2 = time.time()
                segs = transcribe(audio, model)
                t_tr = time.time() - t2
                dur = media.get("duration") or 0
                print(f"[{code[:8]}] transcrito: {len(segs)} segmentos em {t_tr/60:.1f} min "
                      f"({dur/t_tr:.1f}x tempo real)")

                folder = meta.get("panda_folder", "sem_pasta").replace("EDUCACIONAL | ", "")
                out_dir = OUT_DIR / Path(*[sanitize(p) for p in folder.split(" / ")])
                out_dir.mkdir(parents=True, exist_ok=True)
                out_file = out_dir / f"{sanitize(meta.get('title') or media['name'])}.md"
                out_file.write_text(to_markdown(meta, media, segs, args.model), encoding="utf-8")
                print(f"[{code[:8]}] salvo: {out_file.relative_to(ROOT)}")

                if not args.keep_audio:
                    audio.unlink(missing_ok=True)


if __name__ == "__main__":
    asyncio.run(main())
