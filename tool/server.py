"""Loopback-only local application, with streaming media and portable folder imports."""
import argparse
import json
import mimetypes
import os
import re
import secrets
import signal
import shutil
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse, parse_qs, quote

import ai
import jobs
import reviews
from captions import read_cues, segment_cues
from packages import TOOL, ROOT, STATE, atomic_json, safe_path, validate, list_packages, read_package, package_folder, save_package, library_root, migrate_library

TOKEN = secrets.token_urlsafe(32)
IMPORTS = {}
STATIC = TOOL / "static"


def progress_path(folder):
    return reviews.progress_path(folder, STATE)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        if args and str(args[1] if len(args)>1 else "").startswith("5"):
            super().log_message(fmt, *args)

    def permitted(self, mutation=False):
        hosts = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        if self.headers.get("Host") not in hosts:
            self.reply({"error": "仅允许本机访问"}, 403)
            return False
        origin = self.headers.get("Origin")
        if origin and origin not in {"http://" + h for h in hosts}:
            self.reply({"error": "不允许跨站访问"}, 403)
            return False
        if mutation and not secrets.compare_digest(self.headers.get("X-Local-Token", ""), TOKEN):
            self.reply({"error": "页面已失效，请刷新后再试"}, 403)
            return False
        return True

    def send_headers(self, status, length, mime="application/json; charset=utf-8", extra=None):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' blob: data:; media-src 'self' blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        for k,v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()

    def reply(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False, allow_nan=False).encode()
        self.send_headers(status, len(body))
        if self.command != "HEAD":
            self.wfile.write(body)

    def body(self, limit=2 * 1024 * 1024):
        length = int(self.headers.get("Content-Length", 0))
        if length < 0 or length > limit:
            raise ValueError("文件或请求过大")
        body = self.rfile.read(length)
        if len(body) != length:
            raise ValueError("上传不完整，请重试")
        return body

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        try:
            if not self.permitted():
                return
            parsed = urlparse(self.path)
            path = unquote(parsed.path)
            query = parse_qs(parsed.query)
            if path == "/api/status":
                return self.reply({"token": TOKEN, "ai": ai.auth_status(), "root": str(ROOT), "version": "1.3.0", "transcription": bool(jobs.importlib.util.find_spec("faster_whisper"))})
            if path == "/api/packages":
                return self.reply({"packages": list_packages()})
            if path == "/api/jobs":
                return self.reply({"jobs": jobs.snapshots()})
            if path == "/api/reviews":
                return self.reply({"items": reviews.list_reviews(STATE)})
            if path == "/api/package":
                folder = query.get("folder", [""])[0]
                data = read_package(folder)
                return self.reply(data | {"folder": folder})
            if path == "/api/progress":
                return self.reply(reviews.read_progress(query.get("folder", [""])[0], STATE))
            if path.startswith("/media/"):
                parts = path[len("/media/"):].split("/", 1)
                if len(parts) != 2:
                    raise ValueError("视频路径无效")
                folder = package_folder(parts[0])
                data = read_package(folder.name)
                allowed = {data["video"], data.get("poster"), "english.vtt", "bilingual.vtt"}
                if parts[1] not in allowed:
                    raise ValueError("素材文件不存在")
                return self.send_file(safe_path(folder, parts[1]))
            if path in ("/", "/index.html"):
                return self.send_file(STATIC / "index.html")
            if path in ("/app.js", "/style.css", "/favicon.svg"):
                return self.send_file(STATIC / path[1:])
            self.reply({"error": "页面不存在"}, 404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except (ValueError, KeyError, OSError, TypeError) as exc:
            self.reply({"error": str(exc)}, 400)

    def do_POST(self):
        try:
            if not self.permitted(True):
                self.close_connection = True
                return
            parsed = urlparse(self.path)
            path = parsed.path
            query = parse_qs(parsed.query)
            if path == "/api/import/file":
                return self.upload_file(query)
            if path == "/api/captions":
                folder = package_folder(query.get("folder", [""])[0])
                data = read_package(folder.name)
                if data["segments"]:
                    raise ValueError("此素材包已有字幕，替换字幕会改变学习进度；请在副本中编辑")
                ext = query.get("ext", [".vtt"])[0]
                if ext not in (".srt", ".vtt", ".json3"):
                    raise ValueError("请选择英文 SRT、VTT 或 JSON3 字幕")
                target = folder / ("original" + ext)
                target.write_bytes(self.body())
                data["segments"] = segment_cues(read_cues(target))
                if not data["segments"]:
                    raise ValueError("没有找到可用的英文字幕")
                data.update(status="captions_ready", caption_source="手动导入的英文字幕", notice="")
                save_package(folder, data)
                return self.reply({"ok": True})
            data = json.loads(self.body() or b"{}")
            if not isinstance(data, dict):
                raise ValueError("请求格式无效")
            if path == "/api/reviews/add":
                return self.reply(reviews.add_review(data))
            if path == "/api/reviews/update":
                return self.reply(reviews.update_review(data))
            if path == "/api/reviews/delete":
                return self.reply(reviews.delete_review(data, STATE))
            if path == "/api/download":
                details = {"url": jobs.normalize_url(data.get("url", "")), "browser": data.get("browser", ""), "analyze": data.get("analyze", True) is True}
                if details["browser"] not in ("", "chrome", "safari", "edge", "firefox"):
                    raise ValueError("浏览器选项无效")
                return self.reply(jobs.launch("download", details), 202)
            if path == "/api/analyze":
                folder = data.get("folder", "")
                package = read_package(folder)
                upgrade = data.get("upgrade", False)
                segment_id = data.get("segment_id")
                if not isinstance(upgrade, bool):
                    raise ValueError("请选择是否升级精讲")
                if segment_id is not None and (not isinstance(segment_id, str) or not any(s["id"] == segment_id for s in package["segments"])):
                    raise ValueError("找不到要升级的语句")
                return self.reply(jobs.launch("analysis", {"folder": folder, "upgrade": upgrade, "segment_id": segment_id}), 202)
            if path == "/api/cancel":
                jobs.cancel(data.get("id"))
                return self.reply({"ok": True})
            if path == "/api/progress":
                folder = data.get("folder", "")
                progress = data.get("progress", {})
                return self.reply(reviews.save_progress(folder, progress, STATE))
            if path == "/api/import/start":
                manifest = validate(data.get("manifest"))
                for existing in list_packages():
                    if existing["id"] == manifest["id"]:
                        return self.reply({"existing": existing["folder"]})
                import_id = secrets.token_hex(12)
                folder = STATE / "imports" / import_id
                folder.mkdir(parents=True)
                for name in (manifest["video"], manifest.get("poster")):
                    if name:
                        safe_path(folder, name)
                atomic_json(folder / "manifest.json", manifest)
                IMPORTS[import_id] = {"folder": folder, "manifest": manifest, "received": set()}
                return self.reply({"id": import_id, "files": [f for f in (manifest["video"], manifest.get("poster")) if f]})
            if path == "/api/import/finish":
                item = IMPORTS.get(data.get("id"))
                if not item:
                    raise ValueError("导入已失效，请重新选择文件夹")
                folder, manifest = item["folder"], item["manifest"]
                validate(manifest, folder)
                save_package(folder, manifest)
                dest = package_folder(f'imported-{manifest["id"]}-{data["id"][:6]}')
                if dest.exists():
                    raise ValueError("目标素材包已存在，请重新导入")
                folder.rename(dest)
                del IMPORTS[data["id"]]
                return self.reply({"folder": dest.name})
            self.reply({"error": "操作不存在"}, 404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except (ValueError, KeyError, OSError, TypeError) as exc:
            self.reply({"error": str(exc)}, 400)
        except Exception:
            self.reply({"error": "处理失败，请查看本地终端后重试"}, 500)

    def upload_file(self, query):
        item = IMPORTS.get(query.get("id", [""])[0])
        name = query.get("path", [""])[0]
        if not item or name not in {item["manifest"]["video"], item["manifest"].get("poster")}:
            raise ValueError("导入文件无效")
        length = int(self.headers.get("Content-Length", 0))
        if length <= 0 or length > 8 * 1024 ** 3:
            self.close_connection = True
            raise ValueError("文件为空或大于 8 GB")
        target = safe_path(item["folder"], name)
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(target.name + ".uploading")
        try:
            with temp.open("wb") as out:
                remaining = length
                while remaining:
                    chunk = self.rfile.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise ValueError("上传被中断，请重试")
                    out.write(chunk)
                    remaining -= len(chunk)
            temp.replace(target)
        finally:
            temp.unlink(missing_ok=True)
        return self.reply({"ok": True})

    def send_file(self, file):
        if not file.is_file():
            return self.reply({"error": "文件不存在"}, 404)
        size = file.stat().st_size
        start, end, status = 0, size - 1, 200
        extra = {"Accept-Ranges": "bytes"}
        header = self.headers.get("Range")
        if header:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", header)
            try:
                if not match or not any(match.groups()):
                    raise ValueError()
                if match[1]:
                    start = int(match[1])
                    end = min(int(match[2]), size - 1) if match[2] else size - 1
                else:
                    suffix = int(match[2])
                    if suffix <= 0:
                        raise ValueError()
                    start = max(0, size - suffix)
                if start >= size or end < start:
                    raise ValueError()
            except ValueError:
                self.send_headers(416, 0, extra={"Content-Range": f"bytes */{size}"})
                return
            status = 206
            extra["Content-Range"] = f"bytes {start}-{end}/{size}"
        length = max(0, end - start + 1)
        self.send_headers(status, length, mimetypes.guess_type(file.name)[0] or "application/octet-stream", extra)
        if self.command == "HEAD":
            return
        with file.open("rb") as stream:
            stream.seek(start)
            while length:
                chunk = stream.read(min(length, 256 * 1024))
                if not chunk:
                    break
                self.wfile.write(chunk)
                length -= len(chunk)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    migrated = migrate_library()
    reviews.list_reviews(STATE)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.daemon_threads = True
    url = f"http://127.0.0.1:{server.server_port}"
    print(f"\n  Still · 慢慢听\n  {url}\n  素材包目录：{library_root()}\n  复习库目录：{ROOT / 'review'}\n  关闭此窗口即可停止工具。\n", flush=True)
    if migrated:
        print(f"  已将 {len(migrated)} 个原有素材包整理到 source，学习进度保持不变。", flush=True)
    if not args.no_browser:
        threading.Timer(.6, lambda: webbrowser.open(url)).start()
    def interrupt(signum, frame):
        raise KeyboardInterrupt()
    for signum in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(signum, interrupt)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        for job in list(jobs.JOBS.values()):
            jobs.cancel(job.id)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
