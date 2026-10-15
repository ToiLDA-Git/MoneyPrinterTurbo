"""SQLite-backed bounded render queue. Each render runs in its own process."""
from __future__ import annotations

import csv
import io
import json
import os
import random
import re
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
import zipfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

from app.services import tarot_assets as assets, tarot_registry as registry, tarot_scripts as scripts
from app.services.tarot_video import validate_cta
from app.services.tarot_voice import VoiceProfile, choose_rotation

OUTPUT_ROOT = Path(__file__).resolve().parents[2] / "output" / "tarot-reels"
STATUSES = ("draft", "pending", "running", "completed", "failed", "cancelled", "interrupted")
_PROCESS_OWNER = uuid.uuid4().hex


class StudioSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asset_dir: str = "tarot-assets"
    themes: list[str] = Field(default_factory=lambda: ["daily"], min_length=1, max_length=7)
    count: int = Field(default=1, ge=1, le=100)
    card_count: int = Field(default=1, ge=1, le=3)
    manual_card_ids: list[str] = Field(default_factory=list, max_length=3)
    duration: int = 30
    ai_scripts: bool = False
    voice: str = "vi-VN-HoaiMyNeural-Female"
    voice_profile: VoiceProfile | None = None
    random_voice_rotation: bool = False
    voice_pool: list[VoiceProfile] = Field(default_factory=list, max_length=32)
    offline: bool = False
    subtitle_style: str = "clean"
    effects: list[str] = Field(default_factory=list, max_length=12)
    opacity: float = Field(default=.5, ge=0, le=1)
    intensity: float = Field(default=1, ge=0, le=2)
    effect_duration: float = Field(default=30, ge=.1, le=60)
    smoke: bool = True
    glow: bool = True
    particles: bool = True
    flip: bool = True
    zoom: bool = True
    background: str = ""
    card_back: str = ""
    logo: str = ""
    music: str = ""
    music_volume: float = Field(default=.12, ge=0, le=1)
    cta_url: str = assets.DEFAULT_CTA_URL
    cta_text: str = Field(default="Trải bài Tarot miễn phí tại", max_length=120)
    cta_duration: float = Field(default=3, ge=1, le=8)
    hardware: bool = True
    concurrency: int = Field(default=1, ge=1, le=4)

    def checked(self):
        if self.card_count not in (1,3) or self.duration not in (30,45,60):
            raise ValueError("Choose 1/3 cards and 30/45/60 seconds")
        if any(t not in scripts.THEMES for t in self.themes):
            raise ValueError("Unknown Tarot theme")
        if self.voice_profile is not None:
            self.voice_profile.checked()
        elif self.voice not in ("vi-VN-HoaiMyNeural-Female", "vi-VN-NamMinhNeural-Male"):
            raise ValueError("Choose a supported Vietnamese voice")
        for profile in self.voice_pool:
            profile.checked()
        if self.random_voice_rotation and not self.voice_pool:
            raise ValueError('Add voices to the Voice Studio rotation pool first')
        if self.subtitle_style not in ("clean", "boxed", "word"):
            raise ValueError("Unknown subtitle style")
        validate_cta(self.cta_url)
        if self.manual_card_ids and len(self.manual_card_ids) != self.card_count:
            raise ValueError("Manual selection must contain exactly the chosen number of cards")
        return self


class BatchStore:
    def __init__(self, root=None):
        self.root = Path(root or OUTPUT_ROOT).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "studio.sqlite3"
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS batches(id TEXT PRIMARY KEY, created TEXT NOT NULL, settings TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs(
                    batch_id TEXT NOT NULL, id INTEGER NOT NULL, theme TEXT NOT NULL,
                    status TEXT NOT NULL, progress REAL NOT NULL DEFAULT 0,
                    payload TEXT NOT NULL, error TEXT NOT NULL DEFAULT '',
                    attempt INTEGER NOT NULL DEFAULT 0, pid INTEGER, owner TEXT,
                    updated REAL NOT NULL, PRIMARY KEY(batch_id,id),
                    FOREIGN KEY(batch_id) REFERENCES batches(id));
                CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status,updated);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.db_path, timeout=20)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=20000")
        try:
            with db:
                yield db
        finally:
            db.close()

    def batch_dir(self, batch_id):
        if not re.fullmatch(r"batch-\d{8}-\d{6}-[a-f0-9]{8}", batch_id):
            raise ValueError("Invalid batch identifier")
        path = (self.root / batch_id).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("Unsafe batch path")
        return path

    def batches(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM batches ORDER BY created DESC")]

    def settings(self, batch_id):
        with self.connect() as db:
            row = db.execute("SELECT settings FROM batches WHERE id=?", (batch_id,)).fetchone()
        if row is None:
            raise ValueError("Batch not found")
        return StudioSettings.model_validate_json(row["settings"]).checked()

    def jobs(self, batch_id):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM jobs WHERE batch_id=? ORDER BY id", (batch_id,)).fetchall()
        result = []
        for row in rows:
            job = dict(row)
            job["payload"] = json.loads(job["payload"])
            result.append(job)
        return result

    def job(self, batch_id, job_id):
        return next((j for j in self.jobs(batch_id) if j["id"] == job_id), None)

    def patch(self, batch_id, job_id, **updates):
        allowed = {"status", "progress", "payload", "error", "pid", "owner"}
        if not updates or set(updates)-allowed:
            raise ValueError("Invalid job update")
        if "status" in updates and updates["status"] not in STATUSES:
            raise ValueError("Unknown job state")
        if "payload" in updates:
            updates["payload"] = json.dumps(updates["payload"], ensure_ascii=False)
        updates["updated"] = time.time()
        with self.connect() as db:
            db.execute("UPDATE jobs SET " + ",".join(f"{k}=?" for k in updates) + " WHERE batch_id=? AND id=?",
                       [*updates.values(), batch_id, job_id])

    def create(self, settings: StudioSettings, manual_scripts=None, progress=None):
        settings = settings.checked().model_copy(deep=True)
        report = registry.discover(settings.asset_dir)
        settings.asset_dir = report["root"]
        available = [cid for cid in report["cards"] if cid not in report["duplicates"]]
        if len(available) < settings.card_count:
            raise ValueError("Not enough unambiguous local card images")
        if manual_scripts and len(manual_scripts) != settings.count:
            raise ValueError("Provide one manual script per video, separated by a line containing ---")
        if manual_scripts and len(set(s.strip() for s in manual_scripts)) != len(manual_scripts):
            raise ValueError("Manual batch scripts must be distinct")
        if settings.manual_card_ids:
            registry.selected_cards(report, settings.manual_card_ids)
        registry.validate_required(report, effects=settings.effects, background=settings.background,
                                   card_back=settings.card_back, logo=settings.logo, music=settings.music)
        now = datetime.now(ZoneInfo("Asia/Ho_Chi_Minh"))
        batch_id = f"batch-{now:%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}"
        directory = self.batch_dir(batch_id)
        directory.mkdir()
        with self.connect() as db:
            db.execute("INSERT INTO batches VALUES(?,?,?)", (batch_id, now.isoformat(), settings.model_dump_json()))
            for index in range(settings.count):
                db.execute("INSERT INTO jobs(batch_id,id,theme,status,payload,updated) VALUES(?,?,?,?,?,?)",
                           (batch_id,index+1,settings.themes[index%len(settings.themes)],"draft","{}",time.time()))
        rng, used = random.Random(uuid.uuid4().hex), set()
        profiles = (choose_rotation(settings.voice_pool, settings.count, rng)
                    if settings.random_voice_rotation else
                    [settings.voice_profile or VoiceProfile(voice_id=settings.voice)]*settings.count)
        for index in range(settings.count):
            cid = settings.manual_card_ids or scripts.select_random(available, settings.card_count, rng, used)
            if manual_scripts:
                mentioned = list(dict.fromkeys(m["id"] for m in assets.mentions(manual_scripts[index])))
                if len(mentioned) == settings.card_count:
                    cid = mentioned
            used.add(tuple(sorted(cid)))
            try:
                selected = registry.selected_cards(report,cid)
                metadata = scripts.generate(settings.themes[index%len(settings.themes)],selected,settings.duration,index,
                                            ai=settings.ai_scripts,manual_script=manual_scripts[index] if manual_scripts else "",
                                            cta_url=settings.cta_url,cta_text=settings.cta_text,cta_duration=settings.cta_duration)
                metadata["visual"] = scripts.visual_variant(index, rng.randrange(1_000_000))
                metadata["audio"] = ""
                metadata["srt"] = ""
                metadata['voice_profile'] = profiles[index].model_dump()
                self.patch(batch_id,index+1,payload=metadata)
            except Exception as exc:
                self.patch(batch_id,index+1,status="failed",error=f"Script preparation: {str(exc)[:600]}",payload={"card_ids":cid,"script":manual_scripts[index] if manual_scripts else "",'voice_profile':profiles[index].model_dump()})
            if progress:
                progress((index+1)/settings.count)
        self.export(batch_id)
        return batch_id

    def edit(self, batch_id, job_id, script=None, audio=None, srt=None):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status,payload FROM jobs WHERE batch_id=? AND id=?",(batch_id,job_id)).fetchone()
            if row is None or row["status"] in ("pending","running"):
                raise ValueError("Pause pending jobs before editing; running jobs cannot be edited")
            payload = json.loads(row["payload"])
            if script is not None:
                scripts.validate_script(script,payload.get("card_ids",[]))
                if script.strip() != payload.get("script", "").strip():
                    # Narration and cues belong to the exact script they were supplied for.
                    payload["audio"] = ""
                    payload["srt"] = ""
                payload["script"] = script.strip()
                payload["fingerprint"] = scripts.hashlib.sha256(script.strip().encode()).hexdigest()
            if audio is not None:
                payload["audio"] = audio
            if srt is not None:
                payload["srt"] = srt
            db.execute("UPDATE jobs SET payload=?,status='draft',progress=0,error='',updated=? WHERE batch_id=? AND id=?",
                       (json.dumps(payload,ensure_ascii=False),time.time(),batch_id,job_id))
        self.export(batch_id)

    def enqueue(self,batch_id, *, failed_only=False, job_id=None):
        settings = self.settings(batch_id)
        report = registry.discover(settings.asset_dir)
        registry.validate_required(report, effects=settings.effects, background=settings.background,
                                   card_back=settings.card_back, logo=settings.logo, music=settings.music)
        ready = []
        states = {"failed","interrupted"} if failed_only else {"draft"}
        for job in self.jobs(batch_id):
            if job_id is not None and job["id"] != job_id:
                continue
            if job_id is not None:
                if job["status"] in ("pending","running"):
                    continue
            elif job["status"] not in states:
                continue
            try:
                VoiceProfile.model_validate(job['payload'].get('voice_profile') or
                    (settings.voice_profile.model_dump() if settings.voice_profile else {'voice_id':settings.voice})).checked()
                scripts.validate_script(job["payload"].get("script",""),job["payload"].get("card_ids",[]))
                registry.selected_cards(report,job["payload"]["card_ids"])
                if settings.offline and not all(job["payload"].get(k) for k in ("audio","srt")):
                    raise ValueError("Offline mode needs matching narration audio and SRT for this job")
                ready.append(job["id"])
            except ValueError as exc:
                self.patch(batch_id,job["id"],status="failed",error=str(exc))
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            queued = db.execute("SELECT COUNT(*) FROM jobs WHERE status='pending'").fetchone()[0]
            if queued + len(ready) > 100:
                raise ValueError("Render queue is full (100 pending jobs); finish or cancel another batch first")
            for jid in ready:
                db.execute("UPDATE jobs SET status='pending',progress=0,error='',updated=? WHERE batch_id=? AND id=? AND status NOT IN ('running','pending')",
                           (time.time(),batch_id,jid))
        self.export(batch_id)
        return len(ready)

    def regenerate_script(self,batch_id,job_id):
        job=self.job(batch_id,job_id)
        if not job or job['status'] in ('pending','running'):
            raise ValueError('Only idle jobs can regenerate scripts')
        settings=self.settings(batch_id)
        report=registry.discover(settings.asset_dir)
        selected=registry.selected_cards(report,job['payload'].get('card_ids',[]))
        metadata=scripts.generate(job['theme'],selected,settings.duration,job_id-1+100*(job['attempt']+1),
                                  ai=settings.ai_scripts,cta_url=settings.cta_url,cta_text=settings.cta_text,cta_duration=settings.cta_duration)
        metadata['visual']=scripts.visual_variant(job_id+job['attempt']*100,random.randrange(1_000_000))
        metadata.update(audio='',srt='')
        if job['payload'].get('voice_profile'):
            metadata['voice_profile']=job['payload']['voice_profile']
        self.patch(batch_id,job_id,payload=metadata,status='draft',progress=0,error='')
        self.export(batch_id)

    def set_voice(self, batch_id, job_id, profile: VoiceProfile):
        profile.checked()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT status,payload FROM jobs WHERE batch_id=? AND id=?',(batch_id,job_id)).fetchone()
            if row is None or row['status'] in ('pending','running'):
                raise ValueError('Only idle jobs can change voice')
            payload=json.loads(row['payload'])
            previous=payload.get('voice_profile')
            if previous:
                previous=VoiceProfile.model_validate(previous).model_dump()
            selected=profile.model_dump()
            if previous == selected:
                return
            payload['voice_profile']=selected
            if not previous or {k:v for k,v in previous.items() if k!='volume'} != {k:v for k,v in selected.items() if k!='volume'}:
                payload.update(audio='',srt='')
            db.execute("UPDATE jobs SET payload=?,status='draft',progress=0,error='',updated=? WHERE batch_id=? AND id=?",
                       (json.dumps(payload,ensure_ascii=False),time.time(),batch_id,job_id))
        self.export(batch_id)

    def save_input(self,batch_id,job_id,kind,data,extension):
        if kind not in ('audio','srt') or extension.lower() not in ({'.mp3','.wav','.m4a','.ogg'} if kind=='audio' else {'.srt'}):
            raise ValueError('Unsupported narration/subtitle input')
        if not data or len(data)>100*1024*1024:
            raise ValueError('Input files must be 1 byte–100 MB')
        job=self.job(batch_id,job_id)
        if not job or job['status'] in ('pending','running'):
            raise ValueError('Only idle jobs can update narration inputs')
        relative=f'.inputs/video-{job_id:03}/{kind}-{uuid.uuid4().hex}{extension.lower()}'
        assets._write(self.batch_dir(batch_id)/relative,data)
        self.edit(batch_id,job_id,**{kind:relative})
        return relative

    def cancel_pending(self,batch_id):
        with self.connect() as db:
            count = db.execute("UPDATE jobs SET status='cancelled',error='Cancelled before rendering',updated=? WHERE batch_id=? AND status IN ('draft','pending')",(time.time(),batch_id)).rowcount
        self.export(batch_id)
        return count

    def claim(self, concurrency=1):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT COUNT(*) FROM jobs WHERE status='running'").fetchone()[0] >= concurrency:
                return None
            row=db.execute("SELECT batch_id,id FROM jobs WHERE status='pending' ORDER BY updated,id LIMIT 1").fetchone()
            if row is None:
                return None
            db.execute("UPDATE jobs SET status='running',attempt=attempt+1,pid=?,owner=?,progress=1,updated=? WHERE batch_id=? AND id=?",
                       (os.getpid(),_PROCESS_OWNER,time.time(),row['batch_id'],row['id']))
            return row['batch_id'],row['id']

    def recover(self):
        with self.connect() as db:
            running = [dict(r) for r in db.execute("SELECT batch_id,id,pid,updated FROM jobs WHERE status='running'")]
        for row in running:
            if time.time()-row['updated'] < 15:
                continue
            if not process_alive(row['pid']):
                self.patch(row['batch_id'],row['id'],status='interrupted',error='Render process stopped. Retry this job; completed outputs are preserved.')

    def output(self,batch_id,job_id):
        return self.batch_dir(batch_id)/f"video-{job_id:03}.mp4"

    def export(self,batch_id):
        directory=self.batch_dir(batch_id)
        rows=self.jobs(batch_id)
        stream=io.StringIO(newline='')
        fields=['id','theme','duration','status','progress','title','hook','card_ids','caption','hashtags','error','file']
        writer=csv.DictWriter(stream,fieldnames=fields)
        writer.writeheader()
        duration=self.settings(batch_id).duration
        for job in rows:
            data={key:job['payload'].get(key,'') for key in ('title','hook','card_ids','caption','hashtags')}
            for key in ('card_ids','hashtags'):
                data[key]=' | '.join(data[key]) if isinstance(data[key],list) else data[key]
            actual_duration=job['payload'].get('render',{}).get('output_duration',duration)
            row={**data,'id':job['id'],'theme':job['theme'],'duration':actual_duration,'status':job['status'],'progress':job['progress'],'error':job['error'],
                 'file':self.output(batch_id,job['id']).name if job['status']=='completed' else ''}
            # Spreadsheet-safe export of all user-authored text.
            writer.writerow({k:("'"+v if isinstance(v,str) and v.startswith(('=','+','-','@')) else v) for k,v in row.items()})
        assets._write(directory/'metadata.csv', ('\ufeff'+stream.getvalue()).encode('utf-8'))
        assets._write(directory/'scripts.json',json.dumps([{'id':j['id'],'theme':j['theme'],'status':j['status'],**j['payload']} for j in rows],ensure_ascii=False,indent=2).encode())
        assets._write(directory/'captions.txt','\n\n'.join(f"Video {j['id']:03}\n{j['payload'].get('caption','')}" for j in rows).encode('utf-8'))

    def zip_completed(self,batch_id):
        self.export(batch_id)
        directory=self.batch_dir(batch_id)
        target=directory/'completed.zip'
        temporary=directory/f'.zip-{uuid.uuid4().hex}.tmp'
        try:
            with zipfile.ZipFile(temporary,'w',compression=zipfile.ZIP_STORED) as archive:
                for job in self.jobs(batch_id):
                    file=self.output(batch_id,job['id'])
                    if job['status']=='completed' and file.is_file():
                        archive.write(file,file.name)
                for name in ('metadata.csv','scripts.json','captions.txt'):
                    archive.write(directory/name,name)
            os.replace(temporary,target)
        finally:
            temporary.unlink(missing_ok=True)
        return target


def process_alive(pid):
    if not pid:
        return False
    if os.name=='nt':
        import ctypes
        handle=ctypes.windll.kernel32.OpenProcess(0x1000,False,int(pid))
        if not handle:
            return False
        try:
            code=ctypes.c_ulong()
            return bool(ctypes.windll.kernel32.GetExitCodeProcess(handle,ctypes.byref(code))) and code.value==259
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(int(pid),0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


class BatchManager:
    """At most four processes and 100 persisted pending jobs, never 100 futures."""
    def __init__(self,store=None,concurrency=1):
        self.store=store or BatchStore()
        self.concurrency=max(1,min(4,int(concurrency)))
        self._stop=threading.Event()
        self._children={}
        self._thread=threading.Thread(target=self._loop,daemon=True,name='tarot-render-dispatch')
        self._thread.start()

    def configure(self,concurrency):
        self.concurrency=max(1,min(4,int(concurrency)))

    def _loop(self):
        while not self._stop.is_set():
            try:
                self.store.recover()
                for key,(process,handle) in list(self._children.items()):
                    if process.poll() is not None:
                        handle.close()
                        job=self.store.job(*key)
                        if job and job['status']=='running':
                            self.store.patch(*key,status='failed',error=f'Render worker exited ({process.returncode}); inspect worker.log. Possible decoder or memory failure.')
                        self.store.export(key[0])
                        del self._children[key]
                while len(self._children)<self.concurrency:
                    key=self.store.claim(self.concurrency)
                    if key is None:
                        break
                    directory=self.store.batch_dir(key[0])/'.work'/f'video-{key[1]:03}'
                    directory.mkdir(parents=True,exist_ok=True)
                    handle=open(directory/'worker.log','ab')
                    try:
                        process=subprocess.Popen([sys.executable,'-X','utf8','-m','app.services.tarot_studio_worker',
                                                  '--root',str(self.store.root),'--batch',key[0],'--job',str(key[1])],
                                                 cwd=Path(__file__).resolve().parents[2],stdout=handle,stderr=handle,
                                                 creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                        self.store.patch(*key,pid=process.pid)
                        self._children[key]=(process,handle)
                    except Exception as exc:
                        handle.close()
                        self.store.patch(*key,status='failed',error=f'Cannot start render worker: {exc}')
            except Exception:
                from loguru import logger
                logger.exception('Tarot batch dispatcher error')
            self._stop.wait(.75)

    def close(self):
        # Workers finish independently; do not kill an already rendering video.
        self._stop.set()
        self._thread.join(timeout=3)
