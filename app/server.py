"""Study room HTTP server for local use and the public course demo."""
from __future__ import annotations
import argparse
import base64
import io
import json
import mimetypes
import os
from pathlib import Path
import re
import struct
import sys
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

APP=Path(__file__).resolve().parent
sys.path.insert(0,str(APP/"deps"))
from core import StudyStore
from ai_bridge import CodexBridge
from offline_bridge import OfflineBridge
from learning import LearningService
from admin import snapshot as admin_snapshot, code_source, graph as admin_graph, rules as admin_rules


class StudyHTTPServer(ThreadingHTTPServer):
    """Loopback server sized for a browser loading its static modules in parallel."""
    request_queue_size = 128
    daemon_threads = True


def application(workspace, demo_mode=None):
    workspace=Path(workspace).resolve()
    store=StudyStore(workspace)
    if not store.bootstrap()["courses"]:
        seed=json.loads((APP/"seed.json").read_text(encoding="utf-8"))
        for course in seed["courses"]:
            store.add_import(course,[c for c in seed["cards"] if c["courseId"]==course["id"]])
    if demo_mode is None:
        demo_mode=os.environ.get("STUDY_DEMO_MODE", "").lower() in {"1", "true", "yes"}
    bridge=OfflineBridge(workspace) if demo_mode else CodexBridge(workspace)
    service=LearningService(store,bridge,workspace)
    return store,bridge,service

def render_pdf_slides(store, material):
    """Render PDF pages lazily into managed assets; never alter the original upload."""
    if material.get("mime") != "application/pdf": raise ValueError("PDF 자료만 페이지 이미지로 볼 수 있습니다.")
    existing=material.get("slides") or {}
    if existing.get("pages"):
        # Older manifests predate dimensions. Read the managed PNG header only:
        # this preserves the original PDF and avoids an expensive re-render.
        changed=False
        for page in existing["pages"]:
            try: has_dimensions=int(page.get("width") or 0)>0 and int(page.get("height") or 0)>0
            except (TypeError,ValueError): has_dimensions=False
            if has_dimensions: continue
            try:
                image_asset=store.asset(page["assetId"])
                with Path(image_asset["path"]).open("rb") as source: header=source.read(24)
                if header[:8]!=b"\x89PNG\r\n\x1a\n" or header[12:16]!=b"IHDR": continue
                width,height=struct.unpack(">II",header[16:24])
                if width<1 or height<1: continue
                page["width"],page["height"]=width,height;changed=True
            except (KeyError,OSError,struct.error):
                continue
        if changed: store.set_material_slides(material["id"],existing)
        return existing
    try:
        import pypdfium2 as pdfium
    except ImportError as exc: raise RuntimeError("PDF 페이지 렌더러를 사용할 수 없습니다.") from exc
    asset=store.asset(material["asset_id"])
    document=pdfium.PdfDocument(asset["path"])
    if len(document)<1: raise ValueError("페이지가 없는 PDF입니다.")
    pages=[]
    try:
        for index in range(len(document)):
            image=document[index].render(scale=1.5).to_pil()
            buffer=io.BytesIO(); image.save(buffer,format="PNG",optimize=True)
            saved=store.save_asset(buffer.getvalue(),f"{material['id']}-page-{index+1:02d}.png","image/png")
            width,height=image.size
            pages.append({"page":index+1,"assetId":saved["id"],"url":f"/api/materials/{material['id']}/slides/{index+1}","width":width,"height":height})
    finally: document.close()
    manifest={"materialId":material["id"],"pages":pages}
    store.set_material_slides(material["id"],manifest)
    return manifest

def material_preview(store, material):
    """Return one lightweight preview; PDF preview never expands into all pages."""
    mime=str(material.get("mime") or "")
    if mime=="application/pdf":
        slides=material.get("slides") or {}; cached=slides.get("preview")
        if cached: return {"materialId":material["id"],"kind":"pdf","url":f"/api/materials/{material['id']}/preview/image",**cached}
        try: import pypdfium2 as pdfium
        except ImportError as exc: raise RuntimeError("PDF 페이지 렌더러를 사용할 수 없습니다.") from exc
        source=store.asset(material["asset_id"]);document=pdfium.PdfDocument(source["path"])
        try:
            if len(document)<1: raise ValueError("페이지가 없는 PDF입니다.")
            image=document[0].render(scale=1.5).to_pil();buffer=io.BytesIO();image.save(buffer,format="PNG",optimize=True)
            saved=store.save_asset(buffer.getvalue(),f"{material['id']}-preview.png","image/png")
            cached={"assetId":saved["id"],"width":image.size[0],"height":image.size[1]}
        finally: document.close()
        slides["preview"]=cached;store.set_material_slides(material["id"],slides)
        return {"materialId":material["id"],"kind":"pdf","url":f"/api/materials/{material['id']}/preview/image",**cached}
    if mime.startswith("image/"):
        from PIL import Image
        source=store.asset(material["asset_id"])
        with Image.open(source["path"]) as image: width,height=image.size
        return {"materialId":material["id"],"kind":"image","url":f"/api/materials/{material['id']}/original","width":width,"height":height}
    return {"materialId":material["id"],"kind":"text","url":f"/api/materials/{material['id']}/content"}


def handler_for(store,bridge,service,port,demo_context=None,allowed_hosts=None,allowed_origins=None):
    allowed_hosts=set(allowed_hosts or {f"127.0.0.1:{port}",f"localhost:{port}"})
    allowed_origins=set(allowed_origins or {f"http://127.0.0.1:{port}",f"http://localhost:{port}"})
    class Handler(BaseHTTPRequestHandler):
        server_version="StudyRoom/0.1"
        def log_message(self, fmt, *args):
            # Do not log questions, filenames, answers, URLs with learner data.
            pass

        def _security(self,write=False):
            host=self.headers.get("Host", "")
            if host not in allowed_hosts:
                raise PermissionError("허용되지 않은 접속입니다.")
            origin=self.headers.get("Origin")
            if origin and origin not in allowed_origins:
                raise PermissionError("다른 페이지에서 보낸 요청은 받지 않습니다.")
            if write and self.headers.get("X-Study-Client")!="study-room":
                raise PermissionError("학습실 화면에서 다시 요청해주세요.")
            requested=urllib.parse.urlsplit(self.path).path
            # Production has one real store.  The optional demo API remains injectable
            # for isolated tests, while legacy demo pages redirect before routing.
            self.workspace_kind="demo" if requested.startswith("/demo/api") else "live"
            if self.workspace_kind=="demo" and demo_context is None:
                raise KeyError("데모 공간이 아직 준비되지 않았습니다.")
            self.context=demo_context if self.workspace_kind=="demo" else (store,bridge,service)

        def _headers(self,status,mime,size):
            self.send_response(status)
            self.send_header("Content-Type",mime)
            self.send_header("Content-Length",str(size))
            self.send_header("Cache-Control","no-store")
            self.send_header("X-Content-Type-Options","nosniff")
            self.send_header("Referrer-Policy","no-referrer")
            self.send_header("Content-Security-Policy","default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; font-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            self.end_headers()

        def _json(self,value,status=200):
            content=json.dumps(value,ensure_ascii=False,default=str).encode("utf-8")
            self._headers(status,"application/json; charset=utf-8",len(content))
            self.wfile.write(content)

        def _text(self,value,status=200):
            content=str(value).encode("utf-8")
            self._headers(status,"text/plain; charset=utf-8",len(content))
            self.wfile.write(content)

        def _file(self,path,mime=None):
            target=Path(path)
            self._headers(200,mime or mimetypes.guess_type(str(path))[0] or "application/octet-stream",target.stat().st_size)
            with target.open('rb') as stream:
                while chunk:=stream.read(1024*1024): self.wfile.write(chunk)

        def _body(self):
            size=int(self.headers.get("Content-Length","0"))
            if size<=0 or size>29_000_000: raise ValueError("파일이나 요청이 너무 큽니다. PDF는 20MB, 사진은 8MB 이하로 추가해주세요.")
            if "application/json" not in self.headers.get("Content-Type",""): raise ValueError("지원하지 않는 입력 형식입니다.")
            data=json.loads(self.rfile.read(size))
            if not isinstance(data,dict): raise ValueError("입력 형식을 확인해주세요.")
            return data

        def _error(self,exc):
            status=403 if isinstance(exc,PermissionError) else 404 if isinstance(exc,(KeyError,FileNotFoundError)) else 400 if isinstance(exc,(ValueError,TypeError)) else 500
            self._json({"error":str(exc)[:600]},status)

        def do_GET(self):
            try:
                self._security()
                store,bridge,service=self.context
                parsed=urllib.parse.urlsplit(self.path)
                path=urllib.parse.unquote(parsed.path)
                if path in ("/demo","/demo/"):
                    self.send_response(302); self.send_header("Location","/"); self.send_header("Cache-Control","no-store"); self.end_headers(); return
                if path in ("/demo/admin","/demo/admin/"):
                    self.send_response(302); self.send_header("Location","/admin"); self.send_header("Cache-Control","no-store"); self.end_headers(); return
                if self.workspace_kind=="demo": path=path[5:] or "/"
                query=urllib.parse.parse_qs(parsed.query)
                def param(k): return query.get(k,[""])[0]
                if path=="/api/bootstrap":
                    data=store.bootstrap()
                    data["status"]=dict(bridge.status)
                    data["activeQuiz"]=service.public_quiz(data.get("activeQuiz"))
                    data["workspaceKind"]=self.workspace_kind
                    data["pendingJobs"]=service.pending_jobs()
                    return self._json(data)
                if path=="/api/daily": return self._json(service.daily_scope(param("courseId"),param("weekId") or None))
                if path=="/api/materials":
                    if not param("courseId"): raise ValueError("과목을 선택해주세요.")
                    return self._json({"materials":store.materials(param("courseId"),param("weekId") or None),"limits":{"maxFiles":8,"maxBytes":20_000_000}})
                match=re.fullmatch(r"/api/materials/([\w-]+)/preview(?:/image)?",path)
                if match:
                    material=store.material(match[1])
                    if not material: raise FileNotFoundError("자료를 찾을 수 없습니다.")
                    preview=material_preview(store,material)
                    if path.endswith("/image"):
                        if preview["kind"]!="pdf": raise ValueError("PDF 미리보기 이미지가 아닙니다.")
                        asset=store.asset(preview["assetId"]); return self._file(asset["path"],asset["mime"])
                    return self._json(preview)
                match=re.fullmatch(r"/api/materials/([\w-]+)/slides/(\d+)",path)
                if match:
                    material=store.material(match[1])
                    if not material: raise FileNotFoundError("자료를 찾을 수 없습니다.")
                    page=int(match[2]); manifest=render_pdf_slides(store,material)
                    entry=next((p for p in manifest["pages"] if p["page"]==page),None)
                    if not entry: raise ValueError("PDF 페이지 범위를 확인해주세요.")
                    asset=store.asset(entry["assetId"]); return self._file(asset["path"],asset["mime"])
                match=re.fullmatch(r"/api/materials/([\w-]+)/(content|original|slides)",path)
                if match:
                    material=store.material(match[1])
                    if not material: raise FileNotFoundError("자료를 찾을 수 없습니다.")
                    if match[2]=="original":
                        asset=store.asset(material["asset_id"]); return self._file(asset["path"],asset["mime"])
                    if match[2]=="content":
                        if not material.get("text_asset_id"): raise ValueError("이 자료에서 읽을 텍스트를 추출하지 못했습니다.")
                        asset=store.asset(material["text_asset_id"]); return self._file(asset["path"],"text/plain; charset=utf-8")
                    return self._json(render_pdf_slides(store,material))
                if path=="/api/admin": return self._json(admin_snapshot(store,service,self.workspace_kind))
                if path=="/api/admin/graph":
                    if not param("courseId"): raise ValueError("과목을 선택해주세요.")
                    number=param("weekNumber")
                    return self._json(admin_graph(store,param("courseId"),int(number) if number else None))
                if path=="/api/admin/rules": return self._json(admin_rules())
                if path=="/api/admin/code": return self._json(code_source(param("file")))
                if path=="/api/status": return self._json(bridge.status)
                if path=="/api/history": return self._json(store.history(param("courseId")))
                if path=="/api/reading":
                    if not param("courseId"): raise ValueError("과목을 선택해주세요.")
                    return self._json(service.reading_completions(param("courseId")))
                if path=="/api/attempts":
                    if not param("courseId"): raise ValueError("과목을 선택해주세요.")
                    return self._json(store.attempts(param("courseId"),param("weekId") or None,param("cardId") or None))
                if path=="/api/records": return self._json(store.records(param("courseId") or None))
                match=re.fullmatch(r"/api/records/([^/]+)",path)
                if match: return self._json(store.record_detail(urllib.parse.unquote(match[1]),param("courseId") or None))
                if path=="/api/calendar": return self._json(service.calendar(param("month"),param("courseId") or None))
                if path=="/api/chats": return self._json(store.chat_messages(param("courseId"),param("weekId")))
                if path.startswith("/api/jobs/"): return self._json(service.get_job(path.split("/")[-1]))
                if path.startswith("/api/quiz/"):
                    quiz=store.get_quiz(path.split("/")[-1])
                    if not quiz: raise KeyError("묶음을 찾지 못했습니다.")
                    return self._json(service.public_quiz(quiz))
                if path.startswith("/api/assets/"):
                    asset=store.asset(path.split("/")[-1])
                    if not asset: raise FileNotFoundError("첨부 파일을 찾지 못했습니다.")
                    return self._file(asset["path"],asset["mime"])
                if path=="/api/source":
                    card=store.get_card(param("cardId"))
                    source=card.get("source",{})
                    if param("attachment"):
                        # Only references already present in this card's source may be opened.
                        if source.get("assetId"): raise PermissionError("연결된 로컬 원문이 아닙니다.")
                        note=(APP.parent/source.get("path","")).resolve()
                        allowed=(APP/"course-notes.md").resolve()
                        if note != allowed: raise PermissionError("허용되지 않은 자료입니다.")
                        references=re.findall(r"\]\(([^)]+)\)",note.read_text(encoding="utf-8"))
                        attachment=param("attachment")
                        if attachment not in references or re.match(r"[a-z]+:",attachment,re.I): raise PermissionError("원문에 연결되지 않은 자료입니다.")
                        target=(note.parent/urllib.parse.unquote(attachment.split('#')[0])).resolve()
                        if not target.is_relative_to(allowed): raise PermissionError("허용되지 않은 경로입니다.")
                        return self._file(target)
                    if source.get("assetId"):
                        asset=store.asset(source["assetId"])
                        return self._file(asset["path"],asset["mime"])
                    target=(APP.parent/source.get("path","")).resolve()
                    allowed=(APP/"course-notes.md").resolve()
                    if target != allowed: raise PermissionError("이 자료는 학습실에 연결된 공개 요약이 아닙니다.")
                    text=target.read_text(encoding="utf-8")
                    locator=str(source.get("locator") or "").strip()
                    if locator and locator in text:
                        start=text.index(locator)
                        end=text.find("\n# ",start+len(locator))
                        text=text[start:] if end<0 else text[start:end]
                    return self._text(text)
                if path=="/requirements":
                    document=APP.parent.parent/"USER_REQUIREMENTS.html"
                    if not document.is_file(): raise FileNotFoundError("요구사항 문서를 찾지 못했습니다.")
                    return self._file(document,"text/html; charset=utf-8")
                if path=="/": return self._file(APP/"web/index.html","text/html; charset=utf-8")
                if path in ("/admin","/admin/"): return self._file(APP/"web/admin.html","text/html; charset=utf-8")
                if path in ("/architecture","/architecture/"): return self._file(APP/"web/architecture.html","text/html; charset=utf-8")
                if path in ("/wireframe","/wireframe/"): return self._file(APP/"web/wireframe.html","text/html; charset=utf-8")
                if path.startswith("/web/"):
                    target=(APP/path.lstrip("/")).resolve()
                    if not target.is_relative_to((APP/"web").resolve()): raise PermissionError("허용되지 않은 경로입니다.")
                    return self._file(target)
                raise FileNotFoundError("페이지를 찾지 못했습니다.")
            except (BrokenPipeError,ConnectionResetError): pass
            except Exception as exc: self._error(exc)

        def do_PUT(self):
            self.do_POST()

        def do_PATCH(self):
            self.do_POST()

        def do_POST(self):
            try:
                self._security(write=True)
                store,bridge,service=self.context
                path=urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)
                if self.workspace_kind=="demo": path=path[5:] or "/"
                data=self._body()
                if path=="/api/ui": return self._json(store.save_ui(data))
                rid=str(data.get("requestId", ""))
                if path not in ("/api/assets",) and not path.endswith("/cancel") and path!="/api/reconnect":
                    if not rid or len(rid)>180: raise ValueError("요청 식별자가 필요합니다.")
                if path=="/api/reconnect": return self._json(service.job("reconnect:"+(rid or os.urandom(8).hex()),lambda c,p:bridge.connect()))
                if path=="/api/courses": return self._json(store.create_course(data.get("title"),rid))
                match=re.fullmatch(r"/api/courses/([\w-]+)/lessons/([\w-]+)",path)
                if match: return self._json(store.update_lesson(match[1],match[2],data,rid))
                if path=="/api/chat": return self._json(service.job("chat:"+rid,lambda c,p:service.chat(data,c,p),metadata={"kind":"chat","courseId":data.get("courseId")}))
                if path=="/api/quiz": return self._json(service.job("quiz:"+rid,lambda c,p:service.start_quiz(data,c,p),metadata={"kind":"quiz","courseId":data.get("courseId")}))
                if path=="/api/exposure":
                    if data.get("cardId"):
                        card=store.get_card(data["cardId"])
                        if card["courseId"]!=data["courseId"]: raise ValueError("선택 범위가 달라졌습니다.")
                    return self._json(store.event("exposure",data["courseId"],{**data,"summary":{"preview":"대표 예제 확인","skip":"지금은 넘어감","read":"내용 읽기","reopen":"풀이 중 설명 열람"}.get(data.get("kind"),"내용 확인")},rid))
                if path=="/api/reading/complete":
                    return self._json(service.complete_reading(data))
                match=re.fullmatch(r"/api/attempts/([\w:-]+)/note",path)
                if match: return self._json(store.save_attempt_note(match[1],data.get("text",""),rid))
                match=re.fullmatch(r"/api/reviews/([^/]+)/start",path)
                if match:
                    if not data.get("month"): raise ValueError("복습 일정의 월을 확인해주세요.")
                    return self._json(service.job("review:"+rid,lambda c,p:service.start_scheduled_review(match[1],data,c,p),metadata={"kind":"quiz","courseId":data.get("courseId")}))
                if path=="/api/assets":
                    blob=base64.b64decode(data.get("data",""),validate=True)
                    if len(blob)>8_000_000: raise ValueError("사진은 8MB 이하로 첨부해주세요.")
                    from PIL import Image
                    image=Image.open(io.BytesIO(blob)); image.verify()
                    mime={"PNG":"image/png","JPEG":"image/jpeg","WEBP":"image/webp"}.get(image.format)
                    if not mime: raise ValueError("PNG, JPG, WebP 사진을 사용해주세요.")
                    return self._json(store.save_asset(blob,Path(data.get("name","answer.png")).name,mime))
                if path=="/api/import":
                    text=str(data.get("text", ""))
                    uploaded=[]
                    entries=data.get("materials") or []
                    if entries:
                        pasted_note=text
                        if not isinstance(entries,list) or not 1<=len(entries)<=8: raise ValueError("자료는 한 번에 1~8개까지 추가할 수 있습니다.")
                        total=0; parts=[]
                        for entry in entries:
                            if entry.get("role","supplement") not in ("primary","supplement"): raise ValueError("자료 역할을 확인해주세요.")
                            blob=base64.b64decode(entry.get("data",""),validate=True); total+=len(blob)
                            if total>20_000_000: raise ValueError("한 번 등록하는 자료의 합계는 20MB 이하입니다.")
                            filename=Path(entry.get("name","material")).name; suffix=Path(filename).suffix.lower(); mime=entry.get("mime") or "application/octet-stream"; extracted=""
                            if suffix==".pdf":
                                mime="application/pdf"
                                from pypdf import PdfReader
                                reader=PdfReader(io.BytesIO(blob));
                                if len(reader.pages)>150: raise ValueError("PDF는 150쪽 이하로 나누어 추가해주세요.")
                                extracted="\n".join(f"[p.{i+1}]\n"+(p.extract_text() or "") for i,p in enumerate(reader.pages))
                            elif suffix in (".txt",".md"):
                                extracted=blob.decode("utf-8-sig"); mime="text/plain; charset=utf-8"
                            elif mime.startswith("image/"): extracted=""
                            else: raise ValueError("PDF, TXT, Markdown, PNG, JPG, WebP 자료를 사용해주세요.")
                            asset_id=store.save_asset(blob,filename,mime)["id"]; text_asset=store.save_asset(extracted.encode("utf-8"),filename+".txt","text/plain; charset=utf-8")["id"] if extracted else None
                            uploaded.append({"role":entry.get("role","supplement"),"title":entry.get("title") or Path(filename).stem,"filename":filename,"mime":mime,"assetId":asset_id,"textAssetId":text_asset})
                            if extracted: parts.append(f"\n\n## {filename}\n{extracted}")
                        if pasted_note.strip():
                            note_asset=store.save_asset(pasted_note.encode("utf-8"),"보조 메모.txt","text/plain; charset=utf-8")["id"]
                            uploaded.append({"role":"supplement","title":"함께 추가한 메모","filename":"보조 메모.txt","mime":"text/plain; charset=utf-8","assetId":note_asset,"textAssetId":note_asset})
                        text=(pasted_note+"\n\n"+"".join(parts)).strip(); data={**data,"materials":uploaded,"fileName":uploaded[0]["filename"]}
                        if not text.strip(): raise ValueError("사진 자료는 저장했지만 이 실행 환경에서는 내용을 읽어 카드로 만들 수 없습니다. 텍스트 자료를 함께 추가해주세요.")
                    source={"label":str(data.get("title") or data.get("fileName") or "사용자가 추가한 자료"),"path":"","locator":"등록한 자료","origin":"user"}
                    if entries:
                        source["assetId"]=store.save_asset(text.encode("utf-8"),"combined-input.txt","text/plain; charset=utf-8")["id"]
                    elif data.get("fileData"):
                        blob=base64.b64decode(data["fileData"],validate=True)
                        if len(blob)>20_000_000: raise ValueError("자료는 20MB 이하로 나누어 추가해주세요.")
                        filename=Path(data.get("fileName","material.txt")).name
                        suffix=Path(filename).suffix.lower()
                        if suffix==".pdf":
                            from pypdf import PdfReader
                            reader=PdfReader(io.BytesIO(blob))
                            if len(reader.pages)>150: raise ValueError("한 수업 분량의 PDF(150쪽 이하)를 추가해주세요.")
                            text="\n".join(f"[p.{i+1}]\n"+(p.extract_text() or "") for i,p in enumerate(reader.pages))
                            mime="application/pdf"
                        elif suffix in (".txt",".md"):
                            text=blob.decode("utf-8-sig"); mime="text/plain; charset=utf-8"
                        else: raise ValueError("PDF, TXT, Markdown 자료를 추가해주세요.")
                        source["assetId"]=store.save_asset(blob,filename,mime)["id"]
                        extracted_id=store.save_asset(text.encode("utf-8"),filename+".txt","text/plain; charset=utf-8")["id"]
                        data={**data,"materials":[{"role":"primary","title":source["label"],"filename":filename,"mime":mime,"assetId":source["assetId"],"textAssetId":extracted_id}]}
                    else:
                        source["assetId"]=store.save_asset(text.encode("utf-8"),"material.txt","text/plain; charset=utf-8")["id"]
                        data={**data,"materials":[{"role":"primary","title":source["label"],"filename":"material.txt","mime":"text/plain; charset=utf-8","assetId":source["assetId"],"textAssetId":source["assetId"]}]}
                    return self._json(service.job("import:"+rid,lambda c,p:service.import_text(data,text,source,c,p),"자료를 읽고 공부할 순서로 정리하고 있습니다."))
                match=re.fullmatch(r"/api/cards/([\w-]+)(/undo)?",path)
                if match:
                    if match[2]: result=store.undo_card(match[1],data["version"],rid)
                    else: result=store.edit_card(match[1],data["version"],data["patch"],data.get("reason","직접 수정"),rid)
                    return self._json(store.get_card(result["id"]))
                match=re.fullmatch(r"/api/quiz/([\w-]+)/(answer|hint|finish|pause|review|resume)",path)
                if match:
                    qid,action=match.groups()
                    if not store.get_quiz(qid): raise KeyError("풀던 묶음을 찾지 못했습니다.")
                    if action=="pause": return self._json(store.event("quiz_paused",store.get_quiz(qid)["courseId"],{"quizId":qid,"summary":"풀이를 보관하고 잠시 멈춤"},rid))
                    if action=="resume": return self._json(service.resume_quiz(qid))
                    fn={"answer":service.submit,"hint":service.hint,"finish":service.finish,"review":service.review}[action]
                    return self._json(service.job(qid+":"+action+":"+rid,lambda c,p:fn(qid,data,c,p)))
                match=re.fullmatch(r"/api/jobs/([\w-]+)/cancel",path)
                if match: return self._json(service.cancel_job(match[1]))
                raise FileNotFoundError("요청한 기능을 찾지 못했습니다.")
            except (BrokenPipeError,ConnectionResetError): pass
            except Exception as exc: self._error(exc)
    return Handler


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--port",type=int,default=int(os.environ.get("PORT", "8765")))
    parser.add_argument("--host",default=os.environ.get("HOST", "127.0.0.1"))
    parser.add_argument("--workspace",default=str(APP.parent))
    parser.add_argument("--no-browser",action="store_true")
    args=parser.parse_args()
    store,bridge,service=application(args.workspace)
    hosts={f"127.0.0.1:{args.port}",f"localhost:{args.port}"}
    origins={f"http://127.0.0.1:{args.port}",f"http://localhost:{args.port}"}
    public_url=os.environ.get("RENDER_EXTERNAL_URL") or os.environ.get("PUBLIC_ORIGIN")
    if public_url:
        parsed=urllib.parse.urlsplit(public_url)
        hosts.add(parsed.netloc); origins.add(f"{parsed.scheme}://{parsed.netloc}")
    hosts.update({item.strip() for item in os.environ.get("ALLOWED_HOSTS","").split(",") if item.strip()})
    server=StudyHTTPServer((args.host,args.port),handler_for(store,bridge,service,args.port,allowed_hosts=hosts,allowed_origins=origins))
    threading.Thread(target=bridge.connect,daemon=True).start()
    display_host="127.0.0.1" if args.host=="0.0.0.0" else args.host
    print(f"학습실 http://{display_host}:{args.port}",flush=True)
    if not args.no_browser: webbrowser.open(f"http://127.0.0.1:{args.port}")
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally:
        bridge.close()
        server.server_close()

if __name__=="__main__": main()
