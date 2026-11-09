"""Local browser for the voice recordings in object storage.

Run:  python tools/recordings_ui.py      then open http://localhost:8765

Lists every recording under voices/ in the bucket, drops exact duplicates,
groups them by the Telegram ID that uploaded them, and lets you play or
download single files (original .ogg or .mp3) or a zip per user.
Files are cached in ./recordings (git-ignored).
"""
import html
import io
import json
import os
import subprocess
import threading
import webbrowser
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

import boto3
from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT, ".env"))
CACHE = os.path.join(ROOT, "recordings")
PORT = int(os.getenv("RECORDINGS_UI_PORT", "8765"))
BUCKET = os.getenv("AWS_S3_BUCKET", "")

_index: list[dict] = []
_lock = threading.Lock()


def _client():
    return boto3.client(
        "s3",
        endpoint_url=os.getenv("S3_ENDPOINT_URL") or None,
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
        region_name=os.getenv("AWS_REGION") or "us-east-1",
    )


def _duration(path: str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
        capture_output=True, text=True,
    ).stdout.strip()
    try:
        return float(out)
    except ValueError:
        return 0.0


def build_index() -> list[dict]:
    """Download unique recordings into the cache and return them grouped by user."""
    s3 = _client()
    objects = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=BUCKET, Prefix="voices/"):
        objects.extend(o for o in page.get("Contents", []) if o["Key"].count("/") == 2)

    # Same bytes = same ETag. Keep the earliest upload of each.
    by_hash: dict[str, list[dict]] = {}
    for o in sorted(objects, key=lambda o: o["LastModified"]):
        by_hash.setdefault(o["ETag"], []).append(o)

    meta_path = os.path.join(CACHE, "index.json")
    try:
        meta = json.load(open(meta_path, encoding="utf-8"))
    except (OSError, ValueError):
        meta = {}

    users: dict[str, list[dict]] = {}
    for etag, copies in by_hash.items():
        first = copies[0]
        _, tid, filename = first["Key"].split("/")
        local = os.path.join(CACHE, tid, filename)
        if not os.path.exists(local):
            os.makedirs(os.path.dirname(local), exist_ok=True)
            s3.download_file(BUCKET, first["Key"], local)
        if etag not in meta:
            meta[etag] = {"duration": _duration(local)}
        users.setdefault(tid, []).append({
            "tid": tid,
            "file": filename,
            "date": first["LastModified"].strftime("%Y-%m-%d %H:%M"),
            "duration": meta[etag]["duration"],
            "copies": len(copies),
        })

    os.makedirs(CACHE, exist_ok=True)
    json.dump(meta, open(meta_path, "w", encoding="utf-8"))

    groups = []
    for tid, recs in users.items():
        recs.sort(key=lambda r: r["date"])
        for i, r in enumerate(recs, 1):
            r["label"] = f"{tid}_{r['date'][:10]}_{i:02d}"
        groups.append({"tid": tid, "recordings": recs,
                       "minutes": sum(r["duration"] for r in recs) / 60})
    groups.sort(key=lambda g: -g["minutes"])
    return groups


def _local_path(tid: str, filename: str) -> str | None:
    path = os.path.realpath(os.path.join(CACHE, tid, filename))
    if not path.startswith(os.path.realpath(CACHE) + os.sep) or not os.path.isfile(path):
        return None
    return path


def _mp3(path: str) -> bytes:
    cached = path.rsplit(".", 1)[0] + ".mp3"
    if not os.path.exists(cached):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", path, "-b:a", "192k", cached], check=True)
    return open(cached, "rb").read()


def _find(tid: str, filename: str) -> dict | None:
    for g in _index:
        if g["tid"] == tid:
            for r in g["recordings"]:
                if r["file"] == filename:
                    return r
    return None


def _fmt_dur(seconds: float) -> str:
    m, s = divmod(int(round(seconds)), 60)
    return f"{m}:{s:02d}"


PAGE_CSS = """
:root { --bg:#f6f7f9; --card:#fff; --text:#1c1f24; --muted:#6b7280; --line:#e5e7eb; --accent:#2563eb; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#111317; --card:#1b1e24; --text:#e8eaed; --muted:#9aa0a6; --line:#2c3038; --accent:#60a5fa; }
}
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--text); font:15px/1.5 system-ui, sans-serif; }
main { max-width:960px; margin:0 auto; padding:24px 16px 64px; }
h1 { font-size:22px; margin:0 0 4px; }
.sub { color:var(--muted); margin-bottom:20px; }
.bar { display:flex; gap:8px; flex-wrap:wrap; margin-bottom:20px; }
a.btn, button.btn { display:inline-block; padding:7px 14px; border-radius:8px; border:1px solid var(--line);
  background:var(--card); color:var(--text); text-decoration:none; font:inherit; cursor:pointer; }
a.btn.primary { background:var(--accent); border-color:var(--accent); color:#fff; }
section { background:var(--card); border:1px solid var(--line); border-radius:12px; margin-bottom:16px; overflow:hidden; }
section header { display:flex; justify-content:space-between; align-items:center; gap:12px; flex-wrap:wrap;
  padding:14px 16px; border-bottom:1px solid var(--line); }
section header h2 { font-size:16px; margin:0; }
section header .meta { color:var(--muted); font-size:13px; }
.row { display:grid; grid-template-columns:150px 60px 1fr auto; gap:12px; align-items:center;
  padding:8px 16px; border-bottom:1px solid var(--line); }
.row:last-child { border-bottom:0; }
.row .date, .row .dur { color:var(--muted); font-size:13px; font-variant-numeric:tabular-nums; }
.row audio { width:100%; height:34px; }
.row .dl a { color:var(--accent); text-decoration:none; font-size:13px; margin-left:8px; }
.dup { color:var(--muted); font-size:12px; }
@media (max-width:640px) {
  .row { grid-template-columns:1fr auto; }
  .row audio { grid-column:1 / -1; }
}
"""


def render() -> str:
    total = sum(len(g["recordings"]) for g in _index)
    minutes = sum(g["minutes"] for g in _index)
    parts = [
        "<!doctype html><html><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1'>",
        f"<title>Voice Recordings</title><style>{PAGE_CSS}</style></head><body><main>",
        "<h1>Voice recordings</h1>",
        f"<div class='sub'>{total} unique recordings · {minutes:.1f} min · {len(_index)} Telegram users "
        f"· bucket <code>{html.escape(BUCKET)}</code></div>",
        "<div class='bar'><a class='btn primary' href='/zip/all'>Download everything (MP3 zip)</a>",
        "<a class='btn' href='/refresh'>Refresh from storage</a></div>",
    ]
    for g in _index:
        tid = html.escape(g["tid"])
        parts.append(
            f"<section><header><div><h2>Telegram {tid}</h2>"
            f"<div class='meta'>{len(g['recordings'])} recordings · {g['minutes']:.1f} min</div></div>"
            f"<a class='btn' href='/zip/{tid}'>Download all (MP3 zip)</a></header>"
        )
        for r in g["recordings"]:
            src = f"/file/{tid}/{html.escape(r['file'])}"
            dup = f" <span class='dup'>×{r['copies']} copies</span>" if r["copies"] > 1 else ""
            parts.append(
                f"<div class='row'><span class='date'>{r['date']}{dup}</span>"
                f"<span class='dur'>{_fmt_dur(r['duration'])}</span>"
                f"<audio controls preload='none' src='{src}'></audio>"
                f"<span class='dl'><a href='{src}?dl=mp3'>MP3</a><a href='{src}?dl=ogg'>OGG</a></span></div>"
            )
        parts.append("</section>")
    parts.append("</main></body></html>")
    return "".join(parts)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, body: bytes, ctype: str, filename: str | None = None, status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        global _index
        url = urlparse(self.path)
        parts = [unquote(p) for p in url.path.strip("/").split("/") if p]

        if not parts:
            return self._send(render().encode("utf-8"), "text/html; charset=utf-8")

        if parts == ["refresh"]:
            with _lock:
                _index = build_index()
            self.send_response(302)
            self.send_header("Location", "/")
            self.end_headers()
            return

        if parts[0] == "file" and len(parts) == 3:
            path = _local_path(parts[1], parts[2])
            rec = _find(parts[1], parts[2])
            if not path or not rec:
                return self._send(b"not found", "text/plain", status=404)
            if url.query == "dl=mp3":
                return self._send(_mp3(path), "audio/mpeg", rec["label"] + ".mp3")
            body = open(path, "rb").read()
            name = rec["label"] + ".ogg" if url.query == "dl=ogg" else None
            return self._send(body, "audio/ogg", name)

        if parts[0] == "zip" and len(parts) == 2:
            groups = _index if parts[1] == "all" else [g for g in _index if g["tid"] == parts[1]]
            if not groups:
                return self._send(b"not found", "text/plain", status=404)
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
                for g in groups:
                    for r in g["recordings"]:
                        path = _local_path(g["tid"], r["file"])
                        if path:
                            z.writestr(f"{g['tid']}/{r['label']}.mp3", _mp3(path))
            return self._send(buf.getvalue(), "application/zip", f"recordings_{parts[1]}.zip")

        self._send(b"not found", "text/plain", status=404)


def main():
    global _index
    if not BUCKET:
        raise SystemExit("AWS_S3_BUCKET is not set in .env")
    print(f"Loading recordings from {BUCKET} into {CACHE} ...")
    _index = build_index()
    print(f"{sum(len(g['recordings']) for g in _index)} unique recordings ready.")
    url = f"http://localhost:{PORT}"
    print(f"Open {url}  (Ctrl+C to stop)")
    threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
