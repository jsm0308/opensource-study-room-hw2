"""Learner workflows: bounded context, frozen questions, evidence-first feedback."""
from __future__ import annotations
import copy
import hashlib
import json
import re
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
import threading
import uuid
from ai_bridge import AIError, Cancelled

LECTURE_NOTE_SOURCE = ""
LECTURE_NOTES_PATH = Path(__file__).parent / "web" / "lecture-notes.json"
OVERLAY_DIR = Path(__file__).parent.parent / "app-derived" / "review-overlay"

def now():
    return datetime.now(timezone.utc).isoformat()

def uid(prefix):
    return prefix + "_" + uuid.uuid4().hex[:16]

def obj(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}

STR = {"type": "string"}
BOOL = {"type": "boolean"}
def arr(items):
    return {"type": "array", "items": items}

CHECK = obj({"id": STR, "requirementId": STR, "label": STR, "essential": BOOL})
ITEM = obj({"cardId": STR, "requirementIds": arr(STR), "familyId": STR, "title": STR, "prompt": STR, "answer": STR, "explanation": STR, "difficulty": {"type":"integer","minimum":1,"maximum":5}, "checks": arr(CHECK)})
ITEMS_SCHEMA = obj({"questions": arr(ITEM)})
CHAT_SCHEMA = obj({"answer": STR, "suggestion": STR, "reason": STR})
GRADE_SCHEMA = obj({"checks": arr(obj({"id": STR, "status":{"type":"string","enum":["met","not_met","unknown"]},"evidence":STR})), "confirmed": STR, "missing": STR, "nextAction": STR, "explanation": STR})
VERIFY_SCHEMA = obj({"valid": BOOL, "issues": arr(STR)})
IMPORT_SCHEMA = obj({"overview":STR,"reviewMarkdown":STR,"preview":obj({"prompt":STR,"solution":STR}),"cards":arr(obj({"title":STR,"goal":STR,"core":STR,"details":arr(obj({"title":STR,"text":STR,"behavior":STR,"level":{"type":"integer","minimum":1,"maximum":5}})),"figureType":{"type":"string","enum":["neuron","xor","layers","outputs","loss","bias","none"]},"caption":STR}))})


class LearningService:
    def __init__(self, store, ai, workspace):
        self.store, self.ai = store, ai
        self.workspace = Path(workspace).resolve()
        self.lock = threading.RLock()
        self.jobs = {}
        self.requests = {}
        self.jobs_lock = threading.RLock()
        self.quiz_locks = {}
        self.quiz_locks_guard = threading.Lock()
        self.job_path = self.workspace / "data" / "pending-jobs.json"
        self.job_path.parent.mkdir(parents=True, exist_ok=True)
        if self.job_path.exists():
            try:
                recovered = json.loads(self.job_path.read_text(encoding="utf-8"))
                for j in recovered:
                    if j["status"] == "running":
                        j["status"] = "error"
                        j["error"] = "학습실이 다시 시작됐습니다. 입력은 보관돼 있습니다. 다시 시도해주세요."
                    j["cancel"] = threading.Event()
                    self.jobs[j["id"]] = j
                    self.requests[j["key"]] = j["id"]
            except (ValueError, OSError):
                pass

    def _save_jobs(self):
        rows = [{k:v for k,v in j.items() if k not in ("cancel",)} for j in self.jobs.values()]
        tmp = self.job_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(rows[-300:], ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.job_path)

    def job(self, key, function, message="함께 확인하고 있습니다.", metadata=None):
        if not key or len(key) > 180:
            raise ValueError("요청 식별자가 필요합니다.")
        with self.jobs_lock:
            metadata = metadata or {}
            if metadata.get("kind") == "quiz":
                existing = next((j for j in self.jobs.values() if j["status"] == "running" and j.get("kind") == "quiz" and j.get("courseId") == metadata.get("courseId")), None)
                if existing:
                    return {"jobId": existing["id"]}
            if key in self.requests:
                old = self.jobs[self.requests[key]]
                if old["status"] in ("running", "done"):
                    return {"jobId": old["id"]}
            jid = uid("job")
            stamp=now()
            job = {"id":jid,"key":key,"status":"running","phase":"queued","message":message,"createdAt":stamp,"updatedAt":stamp,"cancel":threading.Event(),**metadata}
            self.jobs[jid] = job
            self.requests[key] = jid
            self._save_jobs()
        def progress(msg):
            with self.jobs_lock:
                if job["message"] != msg:
                    phase="generating" if "생성" in msg else "validating" if "검증" in msg else "working"
                    job.update(message=msg, phase=phase, updatedAt=now())
        def run():
            try:
                with self.jobs_lock: job.update(phase="working",startedAt=now(),updatedAt=now())
                result = function(job["cancel"], progress)
                with self.jobs_lock:
                    job["result"] = result
                    job.update(status="done",phase="completed")
            except Cancelled as exc:
                with self.jobs_lock:
                    job.update(status="cancelled",phase="cancelled", error=str(exc))
            except Exception as exc:
                with self.jobs_lock:
                    job.update(status="error",phase="failed", error=str(exc)[:700])
            finally:
                with self.jobs_lock:
                    job["finishedAt"] = now()
                    self._save_jobs()
        threading.Thread(target=run, daemon=True).start()
        return {"jobId": jid}

    def get_job(self, jid):
        with self.jobs_lock:
            if jid not in self.jobs:
                raise KeyError("요청을 찾지 못했습니다.")
            result={k:v for k,v in self.jobs[jid].items() if k not in ("key","cancel")}
            current=datetime.fromisoformat(result.get("finishedAt") or now())
            result["elapsedSec"]=max(0,int((current-datetime.fromisoformat(result["createdAt"])).total_seconds()))
            result["stageElapsedSec"]=max(0,int((current-datetime.fromisoformat(result.get("updatedAt",result["createdAt"]))).total_seconds()))
            finished=[]
            for item in self.jobs.values():
                if item.get("id")==jid or item.get("kind")!=result.get("kind") or item.get("status")!="done" or not item.get("finishedAt"): continue
                try: finished.append(max(1,int((datetime.fromisoformat(item["finishedAt"])-datetime.fromisoformat(item["createdAt"])).total_seconds()*1000)))
                except (TypeError,ValueError): continue
            if finished:
                finished.sort(); expected=finished[len(finished)//2]; basis="recent"
            else:
                expected=45000 if result.get("kind")=="quiz" else 30000; basis="initial"
            result["timing"]={"startedAt":result.get("startedAt") or result["createdAt"],"elapsedMs":result["elapsedSec"]*1000,"expectedMs":expected,"lowerMs":max(1000,int(expected*.65)),"upperMs":int(expected*1.5),"estimated":True,"estimateBasis":basis,"sampleCount":len(finished)}
            return result

    def pending_jobs(self):
        with self.jobs_lock:
            return [self.get_job(jid) for jid,j in self.jobs.items() if j["status"]=="running"]

    def cancel_job(self, jid):
        with self.jobs_lock:
            self.jobs[jid]["cancel"].set()
        return {"cancelled":True}

    def run_quiz_action(self, quiz_id, action):
        """Serialize mutations of one quiz without blocking another quiz's saved evidence."""
        with self.quiz_locks_guard:
            lock = self.quiz_locks.setdefault(quiz_id, threading.RLock())
        with lock:
            return action()

    def _scope(self, course_id, week_id):
        data = self.store.bootstrap()
        course = next((c for c in data["courses"] if c["id"] == course_id), None)
        if not course:
            raise ValueError("과목을 먼저 선택해주세요.")
        weeks = course["weeks"] if week_id in (None,"all","전체") else [w for w in course["weeks"] if w["id"] == week_id]
        cards = [c for c in data["cards"] if c["courseId"] == course_id and any(c["weekId"] == w["id"] for w in weeks)]
        if not cards:
            raise ValueError("이 범위에 읽을 내용이 아직 없습니다. 자료를 추가해주세요.")
        return course, weeks, cards

    def daily_scope(self, course_id, week_id=None):
        """Cards actually touched today in Seoul, never an inferred whole-course scope."""
        # Today is a course-wide learning record; the selected week only matters for fallback.
        course,weeks,cards=self._scope(course_id,None)
        today=datetime.now(ZoneInfo("Asia/Seoul")).date()
        card_ids={c["id"] for c in cards}; studied=set(); tested=set()
        history=self.store.history(course_id).get("events",[])
        for event in history:
            try: day=datetime.fromisoformat(event["createdAt"].replace("Z","+00:00")).astimezone(ZoneInfo("Asia/Seoul")).date()
            except (KeyError,ValueError): continue
            if day != today: continue
            cid=event.get("cardId")
            if cid in card_ids and (event.get("type") in {"card_explained","hint","quiz_hint"} or (event.get("type")=="exposure" and event.get("kind") in {"read","reopen","explanation"})): studied.add(cid)
        # Immutable attempts are the source for testing evidence; use SQLite when available.
        for row in self.store.conn.execute("SELECT card_id,observed_at FROM attempts WHERE course_id=?",(course_id,)):
            if row["card_id"] in card_ids and datetime.fromisoformat(row["observed_at"]).astimezone(ZoneInfo("Asia/Seoul")).date()==today:
                tested.add(row["card_id"])
                studied.add(row["card_id"])
        selected=[c for c in cards if c["id"] in studied]
        counts={"studied":len(studied),"tested":len(tested & studied),"pending":len(studied-tested)}
        return {"date":today.isoformat(),"cards":[{"id":c["id"],"title":c["title"],"courseId":c["courseId"],"weekId":c["weekId"]} for c in selected],"counts":counts,**counts,"remainingCardIds":[c["id"] for c in selected if c["id"] not in tested]}

    def complete_reading(self, data):
        """Record an explicit completion; merely opening a review never counts as completion."""
        course,weeks,_=self._scope(data["courseId"],data.get("weekId"))
        if len(weeks)!=1: raise ValueError("완료할 주차를 하나 선택해주세요.")
        week=weeks[0]
        saved=self.store.event("reading_completed",course["id"],{"weekId":week["id"],"summary":"복습 문서를 끝까지 읽음"},data.get("requestId"))
        # Same-day work is deliberately not fresh evidence.  Keep one visible, conservative
        # follow-up for each still-unconfirmed target instead of pretending FSRS ran.
        due=(datetime.now(ZoneInfo("Asia/Seoul"))+timedelta(days=1)).replace(hour=9,minute=0,second=0,microsecond=0).astimezone(timezone.utc).isoformat()
        plans=[]
        for card in self._scope(course["id"],week["id"])[2]:
            for req in card.get("requirements",[]):
                state=card.get("requirementStates",{}).get(req.get("id"),{})
                if req.get("required",True) and not state.get("confirmed"):
                    plans.append(self.store.ensure_review_plan(course["id"],week["id"],card["id"],self.store._address(req),due,"읽은 직후에는 기억 확인 근거가 없어서 다음 날 짧게 다시 확인합니다."))
        return {**saved,"scheduledReviewIds":[p["id"] for p in plans]}

    def reading_completions(self, course_id):
        course,_,_=self._scope(course_id,None)
        completions={}
        for event in self.store.history(course["id"])["events"]:
            if event.get("type")=="reading_completed":
                try: day=datetime.fromisoformat(event["createdAt"].replace("Z","+00:00")).astimezone(ZoneInfo("Asia/Seoul")).date().isoformat()
                except (KeyError,TypeError,ValueError): continue
                completions.setdefault((event.get("weekId"),day),{"courseId":course["id"],"weekId":event.get("weekId"),"completedAt":event["createdAt"]})
        return {"completions":sorted(completions.values(),key=lambda item:item["completedAt"],reverse=True)}

    def calendar(self, month, course_id=None):
        """A projection of explicit completions and real FSRS dates, not a debt generator."""
        try:
            start=datetime.strptime(month,"%Y-%m").replace(tzinfo=ZoneInfo("Asia/Seoul"))
        except ValueError: raise ValueError("월은 YYYY-MM 형식으로 입력해주세요.")
        end=(start.replace(day=28)+timedelta(days=4)).replace(day=1)
        data=self.store.bootstrap(); courses=[c for c in data["courses"] if not course_id or c["id"]==course_id]
        if course_id and not courses: raise ValueError("과목을 찾을 수 없습니다.")
        cards={c["id"]:c for c in data["cards"]}; items=[]
        def quiz_evaluated(quiz):
            answers=(quiz or {}).get("answers",{}); questions=(quiz or {}).get("questions",[])
            if not questions or any(not answers.get(q.get("id"),{}).get("feedback") or answers[q["id"]]["feedback"].get("held") for q in questions): return False
            # An invalidated historical assessment remains visible in history, but cannot
            # satisfy a calendar completion tick until its re-grade is saved.
            for answer in answers.values():
                attempt_id=answer.get("attemptId")
                if attempt_id:
                    row=self.store.conn.execute("SELECT invalidated FROM attempts WHERE id=?",(attempt_id,)).fetchone()
                    if not row or row["invalidated"]: return False
            return True
        def in_month(value):
            try:
                point=datetime.fromisoformat(value.replace("Z","+00:00")).astimezone(ZoneInfo("Asia/Seoul"))
                return start <= point < end, point
            except (TypeError,ValueError,AttributeError): return False,None
        readings={}
        for course in courses:
            weeks={w["id"]:w for w in course.get("weeks",[])}
            for event in self.store.history(course["id"])["events"]:
                if event.get("type")!="reading_completed" or event.get("weekId") not in weeks: continue
                included,point=in_month(event.get("createdAt"))
                if included:
                    key=(course["id"],event["weekId"],point.date().isoformat())
                    readings.setdefault(key,{"id":"reading:"+event["id"],"kind":"reading","courseId":course["id"],"courseTitle":course.get("title",""),"weekId":event["weekId"],"weekTitle":weeks[event["weekId"]].get("title",""),"date":point.date().isoformat(),"status":"completed","completedAt":event["createdAt"],"attempted":False,"synthetic":bool(event.get("synthetic")),"seedBatch":event.get("seedBatch"),"reason":"복습 문서 완료 버튼을 눌렀습니다.","startable":False})
        items.extend(readings.values())
        # Reading day is the presentation date for the first (baseline) lesson
        # review.  Keep it independently of the plan's next-day due timestamp so a
        # month-end reading still appears in the month in which it was completed.
        lesson_dates={}
        for course in courses:
            for event in self.store.history(course["id"])["events"]:
                if event.get("type")!="reading_completed": continue
                try: day=datetime.fromisoformat(event["createdAt"].replace("Z","+00:00")).astimezone(ZoneInfo("Asia/Seoul")).date().isoformat()
                except (KeyError,TypeError,ValueError): continue
                lesson_dates[(course["id"],event.get("weekId"))]=day
        for row in self.store.conn.execute("SELECT id,value FROM quizzes ORDER BY updated_at"):
            quiz=json.loads(row["value"])
            if quiz.get("courseId") not in {c["id"] for c in courses}: continue
            fully_evaluated=quiz_evaluated(quiz)
            included,point=in_month(quiz.get("finishedAt"))
            if quiz.get("status")=="finished" and fully_evaluated and included:
                items.append({"id":"quiz:"+quiz["id"],"kind":"quiz","quizId":quiz["id"],"courseId":quiz["courseId"],"weekId":quiz.get("weekId"),"date":point.date().isoformat(),"status":"completed","completedAt":quiz["finishedAt"],"attempted":True,"synthetic":bool(quiz.get("synthetic")),"seedBatch":quiz.get("seedBatch"),"reason":"모든 문항의 평가가 완료되었습니다.","startable":False})
        for row in self.store.conn.execute("SELECT address,card_json FROM recall_cards"):
            course,address=row["address"].split("::",1)
            if course not in {c["id"] for c in courses}: continue
            due=json.loads(row["card_json"]).get("due"); included,point=in_month(due)
            if not included: continue
            target=next((c for c in cards.values() if c.get("courseId")==course and any(self.store._address(r)==address for r in c.get("requirements",[]))),None)
            if not target: continue
            review_id="review:"+course+":"+target["id"]+":"+hashlib.sha256((address+point.date().isoformat()).encode()).hexdigest()[:12]
            items.append({"id":review_id,"kind":"review","courseId":course,"weekId":target.get("weekId"),"cardId":target["id"],"date":point.date().isoformat(),"status":"scheduled","completedAt":None,"attempted":False,"reason":"독립 회상 평가에서 계산된 FSRS 복습 시점입니다.","startable":True,"target":{"requirementAddress":address,"cardTitle":target.get("title","")}})
        plans=self.store.review_plans(course_id)
        scheduled_targets={(p["course_id"],p["card_id"],p["address"]) for p in plans if p["status"]=="scheduled"}
        for plan in plans:
            included,point=in_month(plan["due_at"])
            lesson_day=lesson_dates.get((plan["course_id"],plan["week_id"]))
            reading_in_month=bool(lesson_day and start.date().isoformat() <= lesson_day < end.date().isoformat())
            baseline_on_reading_day=plan["id"].startswith("baseline:") and reading_in_month
            if not included and not baseline_on_reading_day: continue
            card=cards.get(plan["card_id"])
            if not card: continue
            # A genuine independent recall has an FSRS due date and replaces every
            # conservative baseline for this same target.
            if plan["status"]=="scheduled" and self.store.conn.execute("SELECT 1 FROM recall_cards WHERE address=?",(plan["course_id"]+"::"+plan["address"],)).fetchone():
                continue
            status,completed_at=plan["status"],plan["completed_at"]
            # Re-grading can turn a formerly completed answer into held evidence.  The
            # immutable attempts stay intact; the checklist becomes startable again.
            if status=="completed" and plan["quiz_id"]:
                quiz=self.store.get_quiz(plan["quiz_id"])
                if not quiz_evaluated(quiz):
                    # A later scheduled retry already represents this target; keep the
                    # completed historical row out of the actionable projection.
                    if (plan["course_id"],plan["card_id"],plan["address"]) in scheduled_targets: continue
                    status,completed_at="scheduled",None
            items.append({"id":plan["id"],"kind":"review","courseId":plan["course_id"],"weekId":plan["week_id"],"cardId":plan["card_id"],"date":lesson_day if baseline_on_reading_day else point.date().isoformat(),"status":status,"completedAt":completed_at,"attempted":bool(plan["quiz_id"]),"quizId":plan["quiz_id"],"reason":plan["reason"],"startable":status=="scheduled","target":{"requirementAddress":plan["address"],"cardTitle":card.get("title","")}})
        # Older stores have one conservative plan per requirement.  Keep those audit rows,
        # but present one lesson review instead of a wall of near-identical reminders.
        raw_reviews=[item for item in items if item.get("kind")=="review"]
        items=[item for item in items if item.get("kind")!="review"]
        grouped={}
        for item in raw_reviews:
            key=(item["courseId"],item.get("weekId"))
            lesson_day=lesson_dates.get(key)
            review_date=lesson_day if lesson_day and start.date().isoformat() <= lesson_day < end.date().isoformat() else item["date"]
            value=grouped.setdefault(key,{"id":"lesson-review:"+":".join([item["courseId"],str(item.get("weekId"))]),"kind":"lessonReview","courseId":item["courseId"],"weekId":item.get("weekId"),"lessonIds":[item.get("weekId")],"cardIds":[],"date":review_date,"status":"completed","completedAt":item.get("completedAt"),"attempted":False,"reason":"읽은 수업의 모든 카드를 같은 날 다시 확인합니다.","startable":False,"planIds":[],"target":{"requirementAddresses":[]}})
            if item.get("cardId") and item["cardId"] not in value["cardIds"]: value["cardIds"].append(item["cardId"])
            if item.get("id"," ").startswith("baseline:"): value["planIds"].append(item["id"])
            address=(item.get("target") or {}).get("requirementAddress")
            if address: value["target"]["requirementAddresses"].append(address)
            value["attempted"] |= bool(item.get("attempted")); value["startable"] |= bool(item.get("startable"))
            if item.get("status")!="completed": value["status"]="scheduled"; value["completedAt"]=None
        # A started lesson review supersedes its grouped legacy plans in this projection.
        finished_schedules={q.get("reviewSchedule",{}).get("id"):q for row in self.store.conn.execute("SELECT value FROM quizzes") for q in [json.loads(row["value"])] if q.get("status")=="finished" and quiz_evaluated(q)}
        completed_lessons={}
        for course in courses:
            for event in self.store.history(course["id"])["events"]:
                if event.get("type")!="lesson_completed": continue
                week_id=event.get("weekId"); quiz=self.store.get_quiz(event.get("quizId"))
                if not quiz or quiz.get("status")!="finished" or not quiz_evaluated(quiz): continue
                lesson_cards={card["id"] for card in cards.values() if card.get("courseId")==course["id"] and card.get("weekId")==week_id}
                evaluated_cards={question.get("cardId") for question in quiz.get("questions",[]) if quiz.get("answers",{}).get(question.get("id"),{}).get("feedback")}
                if lesson_cards and lesson_cards <= set(quiz.get("scopeCardIds",[])) and lesson_cards <= evaluated_cards:
                    completed_lessons.setdefault((course["id"],week_id),quiz)
        for value in grouped.values():
            value["cardIds"]=sorted(c["id"] for c in cards.values() if c.get("courseId")==value["courseId"] and c.get("weekId")==value["weekId"])
            quiz=finished_schedules.get(value["id"]) or completed_lessons.get((value["courseId"],value["weekId"]))
            if quiz:
                value.update(status="completed",completedAt=quiz.get("finishedAt"),attempted=True,startable=False,quizId=quiz["id"],synthetic=bool(quiz.get("synthetic")),seedBatch=quiz.get("seedBatch"))
        items.extend(grouped.values())
        # A weekly review is a pair of lessons, never a pile of requirement reminders.
        # Course authors can name the pair explicitly.  Older courses retain their
        # source order in adjacent pairs, but incomplete explicit groups are left alone
        # rather than guessing a different lesson belongs with them.
        for course in courses:
            completed={}
            for event in self.store.history(course["id"])["events"]:
                if event.get("type")!="lesson_completed" or event.get("weekId") in completed: continue
                completed[event["weekId"]]=event
            weeks=course.get("weeks",[])
            position={week["id"]: index for index,week in enumerate(weeks)}
            explicit={}
            for week in weeks:
                group_id=week.get("reviewGroupId")
                if group_id: explicit.setdefault(str(group_id),[]).append(week)
            pairs=[]; explicitly_grouped={week["id"] for group in explicit.values() for week in group}
            def lesson_order(week):
                try: number=int(week.get("lessonNumber"))
                except (TypeError,ValueError): number=position[week["id"]]
                return number,position[week["id"]]
            for group_id,group in explicit.items():
                group.sort(key=lesson_order)
                if len(group)==2: pairs.append((group_id,group))
            legacy=[week for week in weeks if week["id"] not in explicitly_grouped]
            for index in range(0,len(legacy)-1,2):
                pairs.append((None,legacy[index:index+2]))
            for group_id,pair in pairs:
                if any(w["id"] not in completed for w in pair): continue
                latest=max(completed[w["id"]]["createdAt"] for w in pair)
                due=(datetime.fromisoformat(latest.replace("Z","+00:00")).astimezone(ZoneInfo("Asia/Seoul"))+timedelta(days=1)).replace(hour=9,minute=0,second=0,microsecond=0)
                included,_=in_month(due.isoformat())
                if not included: continue
                review_id="weekly-review:"+course["id"]+":"+(group_id or pair[0]["id"]+":"+pair[1]["id"])
                cards_for_pair=[c["id"] for c in cards.values() if c.get("courseId")==course["id"] and c.get("weekId") in {w["id"] for w in pair}]
                quiz=finished_schedules.get(review_id)
                source_synthetic=[bool(completed[week["id"]].get("synthetic")) for week in pair]
                source_batches=[completed[week["id"]].get("seedBatch") for week in pair if completed[week["id"]].get("seedBatch")]
                # `synthetic` describes the review record itself.  A future schedule
                # has no quiz record yet, so it is synthetic only when all of its
                # lesson-completion evidence is synthetic.  `basisSynthetic` remains
                # true for mixed evidence, letting the UI distinguish a real future
                # attempt from a plan that was partly demonstrated by fixture data.
                basis_synthetic=any(source_synthetic)
                item_synthetic=bool(quiz.get("synthetic")) if quiz else all(source_synthetic)
                paired_cards=[cards[card_id] for card_id in cards_for_pair]
                traces=self._selection_trace(paired_cards)
                items.append({"id":review_id,"kind":"weeklyReview","courseId":course["id"],"reviewGroupId":group_id,"lessonIds":[w["id"] for w in pair],"cardIds":cards_for_pair,"date":due.date().isoformat(),"status":"completed" if quiz else "scheduled","completedAt":quiz.get("finishedAt") if quiz else None,"attempted":bool(quiz),"quizId":quiz.get("id") if quiz else None,"synthetic":item_synthetic,"basisSynthetic":basis_synthetic,"seedBatch":quiz.get("seedBatch") if quiz else (source_batches[0] if item_synthetic and source_batches else None),"reason":"두 수업의 모든 카드를 평가까지 마친 다음 날 오전 9시에, 오답과 확인되지 않은 연결을 함께 다시 확인합니다.","feedbackSummary":{"mistakeAttemptIds":sorted({attempt_id for trace in traces for attempt_id in trace["evidenceAttemptIds"]}),"unmetRequirementIds":sorted({requirement_id for trace in traces for requirement_id in trace["targetRequirementIds"]})},"startable":not bool(quiz)})
        # A scheduled review is the learner-facing representation of its completed
        # quiz.  Keep a legacy partial quiz visible, but do not duplicate a complete
        # lesson/weekly review as a generic quiz tile.
        represented_quizzes={item.get("quizId") for item in items if item.get("kind") in {"lessonReview","weeklyReview"} and item.get("status")=="completed" and item.get("quizId")}
        items=[item for item in items if not (item.get("kind")=="quiz" and item.get("quizId") in represented_quizzes)]
        return {"month":month,"courseId":course_id or None,"items":sorted(items,key=lambda x:(x["date"],x["kind"],x["id"]))}

    def start_scheduled_review(self, review_id, data, cancel, progress):
        plan=next((item for item in self.calendar(data["month"],data.get("courseId")).get("items",[]) if item["id"]==review_id),None)
        if not plan or not plan.get("startable"): raise ValueError("시작할 수 있는 복습 일정이 아닙니다.")
        if plan.get("kind") in {"lessonReview","weeklyReview"}:
            result=self.start_quiz({"courseId":plan["courseId"],"weekId":plan.get("weekId") or "all","scope":"selected","cardIds":plan["cardIds"],"requestId":data.get("requestId")},cancel,progress)
            if result.get("id"):
                quiz=self.store.get_quiz(result["id"]); quiz["reviewSchedule"]={"id":review_id,"kind":plan["kind"],"lessonIds":plan.get("lessonIds",[])}; quiz["reviewPlanIds"]=plan.get("planIds",[]); self.store.save_quiz(quiz)
                return self.public_quiz(quiz)
            return result
        reopened=self.store.reopen_review_plan(review_id)
        result=self.start_quiz({"courseId":plan["courseId"],"weekId":plan["weekId"],"scope":"selected","cardIds":[plan["cardId"]],"targetRequirementAddress":plan["target"]["requirementAddress"],"requestId":data.get("requestId")},cancel,progress)
        if result.get("id"):
            quiz=self.store.get_quiz(result["id"]); quiz["reviewPlanId"]=reopened["id"]; self.store.save_quiz(quiz)
            return self.public_quiz(quiz)
        return result

    def _selection_trace(self, cards):
        traces=[]
        for card in cards:
            rows=self.store.conn.execute("SELECT id,evaluation,family_id FROM attempts WHERE course_id=? AND card_id=? AND invalidated=0 ORDER BY observed_at DESC LIMIT 6",(card["courseId"],card["id"])).fetchall()
            mistakes=[]; labels=[]; families=[]
            for row in rows:
                evaluation=json.loads(row["evaluation"])
                excluded={r["address"] for r in self.store.conn.execute("SELECT address FROM attempt_requirement_invalidations WHERE attempt_id=?",(row["id"],))}
                relevant=[r for r in evaluation.get("requirements",[]) if self.store._address(r) not in excluded]
                if any(r.get("passed") is False and not r.get("held") for r in relevant):
                    mistakes.append(row["id"])
                    labels.extend(c.get("label") for r in relevant for c in r.get("checks",[]) if c.get("status")=="not_met" and c.get("label"))
                    families.append(row["family_id"])
            unmet=[rid for rid,state in card.get("requirementStates",{}).items() if not state.get("confirmed")]
            overlays=self._approved_overlays(card)
            traces.append({"cardId":card["id"],"reason":"최근 오류 우선" if mistakes else ("미확인 요구사항" if unmet else "오늘 읽은 내용 확인"),"evidenceAttemptIds":mistakes,"wrongCheckpointLabels":labels,"familyIds":[f for f in families if f],"targetRequirementIds":unmet,"approvedOverlayRefs":[{"id":item["id"],"source":item["source"]} for item in overlays]})
        return traces

    def _approved_overlays(self, card):
        """Read approved, source-bound explanations for a matching card detail only."""
        source=card.get("source") or {}
        repo=self.workspace.parent.parent
        candidates={str(source.get("path","")).replace("\\","/")}
        candidates.update(str(item).replace("\\","/") for item in source.get("originalPaths",[]) if item)
        source_path=str(source.get("path","")).replace("\\","/")
        for detail in card.get("details",[]):
            for link in re.findall(r"\]\(([^)#]+)(?:#page=\d+)?\)",str(detail.get("text",""))):
                if source_path and link.lower().endswith(".pdf"):
                    try: candidates.add((repo/Path(source_path).parent/link).resolve().relative_to(repo).as_posix())
                    except (OSError,ValueError): pass
        found=[]
        if not OVERLAY_DIR.is_dir(): return found
        for path in sorted(OVERLAY_DIR.glob("*.json")):
            try: overlay=json.loads(path.read_text(encoding="utf-8")); identity=overlay.get("source") or {}
            except (OSError,ValueError): continue
            if overlay.get("schemaVersion")!=1 or overlay.get("status")!="approved" or overlay.get("kind")!="explanation-overlay": continue
            source_identity=str(identity.get("path","")).replace("\\","/"); page=identity.get("page"); locator=str(identity.get("locator", ""))
            if source_identity not in candidates or not isinstance(page,int) or not locator: continue
            detail=next((item for item in card.get("details",[]) if locator in str(item.get("text","")) and re.search(rf"(?:p\.\s*|\[p\.){page}(?:\]|\s|—|-)",str(item.get("text","")))),None)
            if not detail: continue
            explanation=overlay.get("replaceExplanation")
            if not isinstance(explanation,str) or not explanation.strip(): continue
            found.append({"id":path.stem,"schemaVersion":1,"title":overlay.get("title",path.stem),"source":{"path":source_identity,"page":page,"sha256":identity.get("sha256"),"locator":locator},"explanation":explanation,"coverage":overlay.get("coverage",[])})
        return found

    def _context(self, card):
        context={k:card[k] for k in ("id","version","title","goal","core","details","requirements","source") if k in card}
        supplement=self._lecture_supplement(card)
        if supplement: context["lectureSupplement"]=supplement
        overlays=self._approved_overlays(card)
        if overlays: context["approvedOverlays"]=overlays
        return context

    def _lecture_supplement(self, card):
        """Read only the approved AI lecture additions for the linked source section.

        This is deliberately a prompt/snapshot layer: it never changes learner cards or
        source Markdown, and no other course can acquire these additions by title alone.
        """
        source=card.get("source") or {}
        path=str(source.get("path","")).replace("\\","/")
        if not path.endswith(LECTURE_NOTE_SOURCE) or not LECTURE_NOTES_PATH.is_file(): return None
        try:
            data=json.loads(LECTURE_NOTES_PATH.read_text(encoding="utf-8"))
        except (OSError,json.JSONDecodeError): return None
        sections=data.get("lectureAdditions",data.get("sections",data)) if isinstance(data,dict) else {}
        if not isinstance(sections,dict): return None
        headings=[value.strip() for value in re.findall(r"#\s*([^;#]+)",str(source.get("locator", "")))]
        selected=[]
        for heading in headings:
            addition=sections.get(heading)
            if not isinstance(addition,dict) or not isinstance(addition.get("text"),str): continue
            professor=[]
            for note in addition.get("professor",[]):
                if isinstance(note,dict) and note.get("text"):
                    professor.append({"label":f"[교수님 말씀 · {source.get('label','강의 자료')}/{note.get('time','시각 미상')}]","text":str(note["text"])})
            selected.append({"heading":heading,"text":addition["text"],"professorNotes":professor})
        return {"source":LECTURE_NOTE_SOURCE,"sections":selected} if selected else None

    def chat(self, data, cancel, progress):
        card = self.store.get_card(data["cardId"])
        if card["courseId"] != data["courseId"] or card["weekId"] != data["weekId"]:
            raise ValueError("질문과 선택한 수업이 다릅니다. 질문할 부분을 다시 선택해주세요.")
        if card["version"] != data.get("cardVersion"):
            raise ValueError("읽던 내용이 수정됐습니다. 새 내용을 확인한 뒤 다시 질문해주세요.")
        question = str(data.get("question", "")).strip()
        if not question or len(question)>12000:
            raise ValueError("질문을 12,000자 이내로 입력해주세요.")
        self.store.save_chat(card["courseId"],card["weekId"],card["id"],"user",question)
        history = self.store.chat_messages(card["courseId"], card["weekId"])
        if isinstance(history, dict): history=history.get("messages",[])
        prompt = "읽다가 막힌 부분을 설명해 주세요. 먼저 질문에 직접 답하고 필요한 예 하나만 덧붙이세요. 3~8문장 기본. 새 학습 목표를 만들거나 점수를 매기지 마세요. 카드에 이미 있는 내용은 중복 제안하지 마세요. 카드에 넣을 짧은 보완이 꼭 필요할 때만 suggestion, reason을 쓰고 아니면 빈 문자열.\n" + json.dumps({"card":self._context(card),"selection":str(data.get("selection", ""))[:4000],"recentConversation":[{"role":m["role"],"text":m["text"]} for m in history[-6:]],"question":question}, ensure_ascii=False)
        result = self.ai.ask(prompt, CHAT_SCHEMA, cancel=cancel, progress=progress, purpose="chat")
        if cancel.is_set(): raise Cancelled("질문을 멈췄습니다.")
        proposal = None
        if result.get("suggestion", "").strip():
            addition = result["suggestion"].strip()[:2500]
            if not any(addition == d["text"] for d in card["details"]):
                details = copy.deepcopy(card["details"])
                details.append({"id":uid("supplement"),"title":"질문에서 보완한 연결","text":addition,"supplementary":True})
                proposal = {"cardId":card["id"],"version":card["version"],"patch":{"details":details},"reason":result.get("reason", "질문에서 막힌 연결 보완")}
        self.store.save_chat(card["courseId"],card["weekId"],card["id"],"assistant",result["answer"],proposal)
        self.store.event("exposure", card["courseId"], {"kind":"explanation","cardId":card["id"],"summary":"질문한 부분의 설명을 읽음"}, data["requestId"]+":exposure")
        return {"answer":result["answer"],"proposal":proposal}

    def start_quiz(self, data, cancel, progress):
        scope_kind=data.get("scopeKind")
        if scope_kind in {"lesson","week","course"}:
            bootstrap=self.store.bootstrap(); course=next((c for c in bootstrap["courses"] if c["id"]==data.get("courseId")),None)
            if not course: raise ValueError("과목을 먼저 선택해주세요.")
            if scope_kind=="lesson":
                lesson_id=data.get("lessonId")
                weeks=[w for w in course["weeks"] if w.get("id")==lesson_id]
                if not weeks: raise ValueError("선택한 수업을 찾지 못했습니다.")
            elif scope_kind=="week":
                try: number=int(data.get("weekNumber"))
                except (TypeError,ValueError): raise ValueError("주차 번호를 선택해주세요.")
                weeks=[w for w in course["weeks"] if w.get("weekNumber")==number]
                if not weeks: raise ValueError("선택한 주차에 수업이 없습니다.")
            else: weeks=list(course["weeks"])
            week_ids={w["id"] for w in weeks}; cards=[c for c in bootstrap["cards"] if c.get("courseId")==course["id"] and c.get("weekId") in week_ids]
            if not cards: raise ValueError("이 범위에 읽을 내용이 아직 없습니다. 자료를 추가해주세요.")
        else:
            course,weeks,cards = self._scope(data["courseId"],data.get("weekId"))
        daily=data.get("scope")=="today"; selected=data.get("scope")=="selected"
        requested_ids=data.get("cardIds")
        if requested_ids is not None:
            if not selected or not isinstance(requested_ids,list) or not requested_ids: raise ValueError("복습할 카드를 확인해주세요.")
            wanted=set(requested_ids)
            cards=[c for c in cards if c["id"] in wanted]
            if len(cards)!=len(wanted): raise ValueError("선택 범위 밖의 복습 카드가 있습니다.")
        target_address=data.get("targetRequirementAddress")
        if target_address:
            cards=copy.deepcopy(cards)
            for card in cards:
                card["requirements"]=[r for r in card.get("requirements",[]) if self.store._address(r)==target_address]
            if not cards or any(not c["requirements"] for c in cards): raise ValueError("복습 대상 요구사항을 찾지 못했습니다.")
        if daily:
            course,weeks,cards=self._scope(data["courseId"],None)
            daily_data=self.daily_scope(data["courseId"]); ids={c["id"] for c in daily_data["cards"]}; cards=[c for c in cards if c["id"] in ids]
            if not cards: return {"daily":daily_data,"message":"오늘 읽은 카드가 없습니다. 먼저 한 카드를 읽거나 현재 선택 범위로 문제를 시작하세요."}
            count=len(cards)*2
        elif selected: count=len(cards)*2
        else: count = int(data.get("count",2))
        if not (daily or selected) and count not in (2,5): raise ValueError("긴 풀이 2개 또는 짧은 확인 5개를 선택해주세요.")
        mode="practice" if daily or selected else data.get("mode","practice")
        if mode not in ("practice","check"): raise ValueError("연습 또는 점검을 선택해주세요.")
        with self.lock:
            active = self.store.active_quiz()
            if active and active.get("status") == "active":
                raise ValueError("풀던 묶음이 남아 있습니다. 이어 풀거나 종료한 뒤 새로 시작해주세요.")
        # Coverage first; do not repeatedly pick easy already-confirmed details.
        ranked = sorted(cards,key=lambda c:(c.get("state",{}).get("confirmed",0),c["id"]))
        corrections=[] if (daily or selected) else [c for c in ranked if c.get("state",{}).get("correctionNeeded")]
        correction_ids={c["id"] for c in corrections}
        # Keep weak knowledge visible, but don't keep testing the same unresolved link.
        available=[c for c in ranked if c["id"] not in correction_ids]
        if corrections and not available:
            return {"needsReview":True,"cardId":corrections[0]["id"],"message":"같은 연결에서 두 번 막혔습니다. 설명과 예제를 먼저 보고 다시 풀어 보세요."}
        if available: ranked=available
        if not (daily or selected): ranked=ranked[:count]
        history=self.store.history(course["id"])
        if isinstance(history,dict): history=history.get("events",[])
        traces=self._selection_trace(ranked)
        context={"course":course["title"],"weeks":[w["title"] for w in weeks],"cards":[self._context(c) for c in ranked],"previewAlreadyVisible":[w.get("preview") for w in weeks],"mode":mode,"count":count,"selectionTrace":traces,"recentHistory":history[:20]}
        twice=daily or selected or scope_kind in {"lesson","week","course"}
        instructions=("각 카드를 정확히 두 번 출제하세요. 첫 문항은 개념을 떠올리거나 핵심 조건을 설명하게 하고, 둘째는 다른 조건의 적용 또는 흔한 오개념 구별을 요구하세요. 두 문항은 같은 familyId가 아니어야 합니다. " if twice else "각 카드를 한 번씩 출제하고 ")+"selectionTrace의 실제 오류·미확인 요구사항을 반영하세요. 오류에 쓰였던 답을 그대로 재현시키지 말고 같은 원리를 다른 조건에 적용하게 하세요. 아직 관찰하지 않은 디테일을 맞혔다고 추론하지 마세요. 첫 문항에 여러 미평가 개념을 뒤섞지 마세요. 조건·수치가 충분한 문제와 허용할 대안 풀이를 answer에 포함하세요. requirement ID를 정확히 쓰며 체크포인트는 관찰 가능한 이진 기준으로 고정하세요."
        progress("선택한 범위에서 풀어 볼 문제를 준비하고 있습니다.")
        # Four cards is the largest model call; concatenation keeps daily coverage explicit.
        questions=[]
        for start in range(0,len(ranked),4):
            batch=ranked[start:start+4]
            expected=len(batch)*(2 if twice else 1)
            batch_context={**context,"cards":[self._context(c) for c in batch],"count":expected,"selectionTrace":[t for t in traces if t["cardId"] in {c["id"] for c in batch}]}
            batch_prompt=f"현재 카드에서 정확히 {expected}개 문항을 만드세요. "+instructions+"\n"+json.dumps(batch_context,ensure_ascii=False)
            progress(f"{start+1}–{start+len(batch)} / {len(ranked)}개 내용의 문제를 준비합니다.")
            batch_questions=self.ai.ask(batch_prompt,ITEMS_SCHEMA,cancel=cancel,progress=progress)["questions"]
            if len(batch_questions)!=expected or any(sum(q["cardId"]==c["id"] for q in batch_questions)!=(2 if twice else 1) for c in batch):
                raise AIError("출제할 내용이 빠지거나 중복돼 문제를 저장하지 않았습니다. 다시 시도해주세요.")
            if twice and any(len({q['familyId'] for q in batch_questions if q['cardId']==c['id']})!=2 for c in batch):
                raise AIError("같은 유형의 문항이 반복되어 출제를 보류했습니다. 다시 시도해주세요.")
            progress(f"{start+1}–{start+len(batch)}번 문제의 조건과 풀이를 확인합니다.")
            verified=self.ai.ask("문제의 조건, 정답, 허용 대안, 체크포인트가 원문과 일치하는지 검토하세요. 계산도 확인하고 명백한 오류가 있으면 valid=false와 issues를 주세요. 같은 모델의 재검토이며 독립 검증이 아닙니다.\n"+json.dumps({"cards":batch_context["cards"],"questions":batch_questions},ensure_ascii=False),VERIFY_SCHEMA,cancel=cancel,progress=progress)
            if not verified["valid"]:
                raise AIError("출제 검토에서 오류를 발견했습니다: "+"; ".join(verified["issues"])[:400])
            questions.extend(batch_questions)
        if len(questions)!=count: raise AIError("요청한 문항 수와 달라 출제를 보류했습니다. 다시 시도해주세요.")
        if (daily or selected) and [q["cardId"] for q in questions] and any(sum(q["cardId"]==c["id"] for q in questions)!=2 for c in cards):
            raise AIError("오늘 범위의 카드가 빠지거나 중복돼 출제를 보류했습니다.")
        by_card={c["id"]:c for c in ranked}
        for q in questions:
            if q["cardId"] not in by_card: raise AIError("선택 범위 밖 문항이 있어 출제를 보류했습니다.")
            card=by_card[q["cardId"]]
            reqs={r["id"]:r for r in card["requirements"]}
            if not q["requirementIds"] or not set(q["requirementIds"])<=set(reqs): raise AIError("문항의 평가 기준 연결을 확인하지 못했습니다.")
            if not q["checks"] or len({c["id"] for c in q["checks"]})!=len(q["checks"]): raise AIError("채점 기준이 비어 있거나 중복됐습니다.")
            if {c["requirementId"] for c in q["checks"]}!=set(q["requirementIds"]): raise AIError("평가 대상과 체크포인트가 일치하지 않습니다.")
            q.update(id=uid("item"),version=1,cardVersion=card["version"],requirements=[copy.deepcopy(reqs[rid]) for rid in q["requirementIds"]])
            target_details={r["detailId"] for r in q["requirements"]}
            q["contentSnapshot"]={"core":card["core"],"details":[d for d in card["details"] if d["id"] in target_details]}
            supplement=self._lecture_supplement(card)
            if supplement: q["contentSnapshot"]["lectureSupplement"]=supplement
            overlays=self._approved_overlays(card)
            if overlays: q["contentSnapshot"]["approvedOverlays"]=overlays
            q["hash"]=hashlib.sha256(json.dumps(q,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        if cancel.is_set(): raise Cancelled("출제를 멈췄습니다.")
        scope_reference={"lessonId":data.get("lessonId")} if scope_kind=="lesson" else ({"weekNumber":data.get("weekNumber")} if scope_kind=="week" else ({} if scope_kind=="course" else None))
        quiz={"id":uid("quiz"),"courseId":course["id"],"weekId":data.get("weekId") or (weeks[0]["id"] if scope_kind=="lesson" else None),"mode":mode,"questions":questions,"answers":{},"status":"active","createdAt":now(),"verification":{"method":"same-model-review","model":self.ai.model,"checkedAt":now()},"scopeCardIds":[c["id"] for c in cards],"daily":daily,"dailyScope":self.daily_scope(course["id"],data.get("weekId")) if daily else None,"scopeKind":scope_kind,"scopeReference":scope_reference,"selectionTrace":traces}
        with self.lock:
            active=self.store.active_quiz()
            if active and active.get("status")=="active": raise ValueError("먼저 시작한 묶음이 있습니다. 이어서 풀어주세요.")
            self.store.create_quiz(quiz)
        return self.public_quiz(quiz)

    def resume_quiz(self, quiz_id):
        """Resume a paused set without replacing its frozen questions or answers."""
        with self.lock:
            quiz=self.store.get_quiz(quiz_id)
            if not quiz: raise KeyError("풀던 묶음을 찾지 못했습니다.")
            if quiz.get("status")=="finished": raise ValueError("완료한 묶음은 다시 이어 풀 수 없습니다.")
            active=self.store.active_quiz()
            paused_id=None
            if active and active.get("id")!=quiz_id:
                active["status"]="paused"; active["pausedAt"]=now(); self.store.save_quiz(active); paused_id=active["id"]
            quiz["status"]="active"; quiz.pop("pausedAt",None); self.store.save_quiz(quiz)
        result={"quiz":self.public_quiz(quiz)}
        if paused_id: result["pausedQuizId"]=paused_id
        return result

    def public_quiz(self, quiz):
        if not quiz: return None
        public={k:copy.deepcopy(quiz[k]) for k in ("id","courseId","weekId","mode","status","createdAt","summary","daily","scopeCardIds","synthetic","seedBatch") if k in quiz}
        public["coverage"]={"planned":len(quiz.get("scopeCardIds",[])),"questions":len(quiz.get("questions",[]))}
        public["questions"]=[{k:q[k] for k in ("id","cardId","title","prompt")} for q in quiz["questions"]]
        public["answers"]={}
        for qid,a in quiz.get("answers",{}).items():
            public["answers"][qid]={k:copy.deepcopy(a[k]) for k in ("text","assetId","comment","submitted","assisted","hint") if k in a}
            if a.get("feedback") and (quiz["mode"]=="practice" or quiz["status"]=="finished" or a.get("assisted")):
                public["answers"][qid]["feedback"]=a["feedback"]
        if quiz["status"]=="finished":
            public["feedback"]=[a["feedback"] for a in quiz["answers"].values() if a.get("feedback")]
        return public

    def _answer(self, quiz_id, data):
        quiz=self.store.get_quiz(quiz_id)
        if quiz["status"]!="active": raise ValueError("이미 마친 묶음입니다.")
        q=next((q for q in quiz["questions"] if q["id"]==data.get("questionId")),None)
        if not q: raise ValueError("이 묶음의 문제가 아닙니다.")
        old=quiz["answers"].get(q["id"],{})
        text=str(data.get("text",""))[:24000]
        comment=str(data.get("comment",""))[:4000]
        asset=data.get("assetId")
        if asset: self.store.asset(asset)
        if not text.strip() and not asset: raise ValueError("풀이를 적거나 사진을 첨부해주세요. 모르면 ‘모르겠어요’라고 적어도 됩니다.")
        if old.get("feedback") and (quiz["mode"]=="practice" or old.get("assisted")):
            if text==old.get("text","") and asset==old.get("assetId") and comment==old.get("comment",""):
                return quiz,q,old
            # Revising after seeing feedback is useful practice, never a new independent success.
            old["assisted"]=True
        answer={**old,"text":text,"comment":comment,"assetId":asset,"submitted":True,"submittedAt":now(),"requestId":data["requestId"],"versions":old.get("versions",[])+[{"text":text,"comment":comment,"assetId":asset,"at":now()}]}
        answer.pop("feedback",None)
        quiz["answers"][q["id"]]=answer
        self.store.save_quiz(quiz)
        return quiz,q,answer

    def _fresh(self, quiz, q, answer):
        if answer.get("assisted"): return False
        # Details already exposed in the same family cannot become new independent evidence.
        hist=self.store.history(quiz["courseId"])
        if isinstance(hist,dict): hist=hist.get("events",[])
        for e in hist:
            payload=e.get("payload",e)
            if payload.get("familyId")==q["familyId"] or ((payload.get("cardId")==q["cardId"] or q["cardId"] in payload.get("cardIds",[])) and payload.get("kind") in ("read","explanation","preview","reopen")):
                date=e.get("createdAt") or e.get("created_at") or payload.get("createdAt")
                try:
                    if datetime.now(timezone.utc)-datetime.fromisoformat(date.replace("Z","+00:00"))<timedelta(hours=24): return False
                except (ValueError,TypeError): pass
        return True

    def _grade(self, quiz, q, answer, cancel, progress, *, force=False, evidence=None, review_reason=None):
        if answer.get("feedback") and not force: return answer["feedback"]
        current=self.store.get_card(q["cardId"])
        if current["version"]!=q["cardVersion"]:
            targets={r["detailId"] for r in q["requirements"]}
            snapshot={"core":current["core"],"details":[d for d in current["details"] if d["id"] in targets]}
            # Existing quiz snapshots predate this optional layer; do not invalidate them.
            if "lectureSupplement" in q.get("contentSnapshot",{}): snapshot["lectureSupplement"]=self._lecture_supplement(current)
            if snapshot!=q.get("contentSnapshot"):
                raise AIError("출제 후 설명이 수정돼 이 문항의 평가를 보류했습니다. 새 범위로 다시 확인해주세요.")
        asset_path=None
        if answer.get("assetId"):
            a=self.store.asset(answer["assetId"])
            asset_path=a["path"] if isinstance(a,dict) else a
        review_instruction = " 이 요청의 사유는 재검토 요청일 뿐 답안의 새 근거가 아닙니다. 저장된 원답과 사진에 있는 근거만으로 다시 판정하세요." if review_reason else ""
        prompt="사전에 고정된 체크포인트로 학생의 원답을 평가하세요. 각 체크포인트 id를 정확히 한 번씩 반환하세요. 체크포인트/분모/난도/배점을 바꾸지 마세요. met는 실제 답에서 근거가 있을 때만, not_met는 틀렸거나 요구된 설명을 빠뜨렸을 때, unknown은 사진을 읽을 수 없거나 정당한 대안 풀이를 현재 기준으로 판단할 수 없을 때입니다. 사진이 있으면 실제 이미지를 읽고 풀이를 확인하세요. 판독 불명은 필요한 기호 위치를 구체적으로 알려주세요. 선행 수치 오류와 후속 올바른 원리 사용을 구별하고 이중 감점하지 마세요. 학생의 정신상태를 추정하지 마세요. confirmed=확인된 한 가지, missing=막힌 연결, nextAction=바로 할 한 가지(답을 그대로 다시 베끼는 요구 금지). 긴 강의 금지."+review_instruction+"\n"+json.dumps({"question":q,"answer":{"text":answer["text"],"comment":answer.get("comment"),"hasImage":bool(asset_path)},"reviewReason":review_reason or "","card":self._context(current)},ensure_ascii=False)
        result=self.ai.ask(prompt,GRADE_SCHEMA,image_path=asset_path,cancel=cancel,progress=progress)
        expected={c["id"]:c for c in q["checks"]}
        if len(result["checks"])!=len(expected) or {c["id"] for c in result["checks"]}!=set(expected): raise AIError("채점 근거가 사전 기준과 달라 평가를 보류했습니다.")
        if cancel.is_set(): raise Cancelled("평가를 멈췄습니다. 제출한 답안은 보관됩니다.")
        checked={c["id"]:c for c in result["checks"]}
        requirements=[]
        for req in q["requirements"]:
            checks=[{**c,**checked[c["id"]]} for c in q["checks"] if c["requirementId"]==req["id"]]
            requirements.append({**req,"difficulty":q["difficulty"],"checks":checks})
        independent = bool(evidence["independent"]) if evidence is not None else not answer.get("assisted",False)
        fresh = bool(evidence["fresh"]) if evidence is not None else self._fresh(quiz,q,answer)
        suffix = ":review:"+uid("attempt") if review_reason else (":assisted:"+str(len(answer.get("versions",[]))) if answer.get("assisted") else ":original")
        attempt={"id":quiz["id"]+":"+q["id"]+suffix,"courseId":quiz["courseId"],"questionId":q["id"],"itemVersion":q["version"],"cardId":q["cardId"],"cardVersion":q["cardVersion"],"familyId":q["familyId"],"independent":independent,"fresh":fresh,"observedAt":answer["submittedAt"],"requirements":requirements,"text":answer["text"],"assetId":answer.get("assetId"),"rubricHash":q["hash"],"model":self.ai.model,"synthetic":bool(quiz.get("synthetic")),"seedBatch":quiz.get("seedBatch")}
        computed=self.store.assess(attempt)
        if attempt["independent"] and attempt["fresh"]:
            for req_result in computed["requirements"]:
                if req_result.get("level")==2 and not req_result["held"]:
                    address="|".join(str(req_result.get(k) or "") for k in ("detailId","behavior","conditions"))
                    self.store.record_recall(quiz["courseId"],address,"Good" if req_result["passed"] else "Again",attempt["id"],attempt["observedAt"])
        held=computed["held"]
        met=sum(c["status"]=="met" for c in result["checks"])
        passed=computed["passed"]
        feedback={"questionId":q["id"],"confirmed":result["confirmed"],"missing":result["missing"],"nextAction":result["nextAction"],"explanation":result["explanation"],"met":met,"total":len(expected),"passed":None if held else passed,"held":held,"checks":[{**expected[c["id"]],**c} for c in result["checks"]],"independent":attempt["independent"],"fresh":attempt["fresh"],"stateResult":computed}
        if review_reason:
            feedback["reviewed"] = True
        if any(s.get("correctionNeeded") for s in computed.get("states",{}).values()):
            feedback["reviewCardId"]=q["cardId"]
            feedback["nextAction"]="같은 연결에서 두 번 막혔습니다. 연결된 설명과 예제를 먼저 읽고, 한 문장으로 이유를 말해 보세요."
        self.store.save_attempt_feedback(attempt["id"],feedback,{k:copy.deepcopy(answer[k]) for k in ("text","comment","assetId","submittedAt","versions") if k in answer})
        answer["feedback"]=feedback
        answer["attemptId"]=attempt["id"]
        self.store.save_quiz(quiz)
        return feedback

    def submit(self, quiz_id, data, cancel, progress):
        def action():
            with self.lock:
                quiz,q,answer=self._answer(quiz_id,data)
            feedback=None
            if quiz["mode"]=="practice" or answer.get("assisted"):
                progress("풀이에서 확인된 부분과 막힌 연결을 살펴보고 있습니다.")
                feedback=self._grade(quiz,q,answer,cancel,progress)
                # Practice can choose the next pending item; check batches remain fixed.
                if quiz["mode"]=="practice":
                    answered=[x for x in quiz["questions"] if quiz["answers"].get(x["id"],{}).get("submitted")]
                    remaining=[x for x in quiz["questions"] if not quiz["answers"].get(x["id"],{}).get("submitted")]
                    if not feedback["passed"]:
                        if feedback.get("reviewCardId"):
                            remaining.sort(key=lambda x:x["cardId"]==q["cardId"])
                        else:
                            remaining.sort(key=lambda x:(x["cardId"]!=q["cardId"],x["familyId"]==q["familyId"]))
                    quiz["questions"]=answered+remaining
                    self.store.save_quiz(quiz)
                self.store.event("next_action",quiz["courseId"],{"cardId":q["cardId"],"summary":feedback["nextAction"],"questionId":q["id"]},data["requestId"]+":next")
                self.store.event("feedback_viewed",quiz["courseId"],{"familyId":q["familyId"],"cardId":q["cardId"],"summary":"제출한 풀이의 피드백 확인"},data["requestId"]+":feedback")
            return {"quiz":self.public_quiz(quiz),"feedback":feedback}
        return self.run_quiz_action(quiz_id, action)

    def hint(self,quiz_id,data,cancel,progress):
        return self.run_quiz_action(quiz_id, lambda: self._hint(quiz_id,data,cancel,progress))

    def _hint(self,quiz_id,data,cancel,progress):
        if not data.get("text") and not data.get("assetId"):
            data={**data,"text":"[아직 답안을 작성하지 않음]"}
        with self.lock:
            quiz,q,answer=self._answer(quiz_id,data)
            if not answer.get("assisted"):
                answer["beforeHelp"]={k:copy.deepcopy(answer.get(k)) for k in ("text","assetId","comment","submittedAt")}
                # A blank response before assistance is unobserved, not a failure.
                original=copy.deepcopy(answer)
                if original["text"]!="[아직 답안을 작성하지 않음]":
                    self._grade(quiz,q,original,cancel,progress)
                    answer["beforeHelp"]["feedback"]=original.get("feedback")
                answer["assisted"]=True
                answer.pop("feedback",None)
                quiz["answers"][q["id"]]=answer
                self.store.save_quiz(quiz)
        result=self.ai.ask("정답 전체 대신 다음 한 걸음을 돕는 힌트 하나를 한국어 2~4문장으로 주세요.\n"+json.dumps({"question":q,"attempt":answer.get("beforeHelp")},ensure_ascii=False),obj({"hint":STR}),cancel=cancel,progress=progress)
        answer["hint"]=result["hint"]
        self.store.save_quiz(quiz)
        self.store.event("hint",quiz["courseId"],{"cardId":q["cardId"],"familyId":q["familyId"],"summary":"도움 전 답안을 보존하고 힌트 확인"},data["requestId"]+":hint")
        return {"hint":result["hint"],"quiz":self.public_quiz(quiz)}

    def review(self, quiz_id, data, cancel, progress):
        return self.run_quiz_action(quiz_id, lambda: self._review(quiz_id, data, cancel, progress))

    def _review(self, quiz_id, data, cancel, progress):
        quiz=self.store.get_quiz(quiz_id)
        if not quiz: raise KeyError("풀던 묶음을 찾지 못했습니다.")
        q=next((item for item in quiz["questions"] if item["id"]==data.get("questionId")),None)
        if not q: raise ValueError("이 묶음의 문제가 아닙니다.")
        answer=quiz.get("answers",{}).get(q["id"])
        if not answer or not answer.get("feedback") or not answer.get("attemptId"):
            raise ValueError("제출하고 채점된 답안만 다시 확인할 수 있습니다.")
        reason=str(data.get("reason","")).strip()
        if not reason or len(reason)>4000: raise ValueError("다시 확인할 이유를 1~4,000자로 적어주세요.")
        previous_id=answer["attemptId"]
        previous_flags={"independent":answer["feedback"].get("independent",False),"fresh":answer["feedback"].get("fresh",False)}
        progress("저장한 원답과 고정된 기준으로 다시 확인하고 있습니다.")
        # _grade appends new immutable evidence first.  Only a completed re-grade can replace the old state evidence.
        feedback=self._grade(quiz,q,answer,cancel,progress,force=True,evidence=previous_flags,review_reason=reason)
        correction=self.store.correct_attempt(previous_id,reason)
        feedback["stateResult"]["states"]=correction["states"]
        feedback["correctedAttemptId"]=previous_id
        answer["feedback"]=feedback
        self.store.save_quiz(quiz)
        self.store.event("grade_reviewed",quiz["courseId"],{"quizId":quiz_id,"questionId":q["id"],"oldAttemptId":previous_id,"newAttemptId":answer["attemptId"],"reason":reason,"summary":"저장한 원답을 고정 기준으로 다시 채점"},data["requestId"]+":review")
        return {"quiz":self.public_quiz(quiz),"feedback":feedback}

    def finish(self,quiz_id,data,cancel,progress):
        return self.run_quiz_action(quiz_id, lambda: self._finish(quiz_id,data,cancel,progress))

    def _finish(self,quiz_id,data,cancel,progress):
        quiz=self.store.get_quiz(quiz_id)
        if quiz["status"]=="finished": return {"quiz":self.public_quiz(quiz),"feedback":[a["feedback"] for a in quiz["answers"].values() if a.get("feedback")],"summary":quiz["summary"]}
        feedback=[]
        for i,q in enumerate(quiz["questions"]):
            answer=quiz["answers"].get(q["id"])
            if not answer or not answer.get("submitted"): continue
            progress(f"{i+1}번째 풀이의 근거를 확인하고 있습니다.")
            feedback.append(self._grade(quiz,q,answer,cancel,progress))
        cards=[self.store.get_card(cid) for cid in quiz.get("scopeCardIds",[])]
        total=sum(c.get("state",{}).get("total",len([r for r in c["requirements"] if r.get("required",True)])) for c in cards)
        confirmed=sum(c.get("state",{}).get("confirmed",0) for c in cards)
        pending=len(quiz["questions"])-len(feedback)
        next_action=next((f["nextAction"] for f in feedback if not f.get("passed")),"다음에는 다른 상황의 문제로 같은 원리를 확인해 보세요.")
        quiz.update(status="finished",finishedAt=now(),summary={"confirmed":confirmed,"total":total,"pending":pending,"nextAction":next_action})
        self.store.save_quiz(quiz)
        all_evaluated=bool(quiz["questions"]) and len(feedback)==len(quiz["questions"]) and all(not item.get("held") for item in feedback)
        if all_evaluated:
            # A lesson is complete only when every card was in scope *and* actually
            # received a completed question.  A forged or legacy one-question set must
            # not turn a whole lesson into a completed daily quiz.
            _,all_weeks,all_cards=self._scope(quiz["courseId"],None)
            covered=set(quiz.get("scopeCardIds",[]))
            evaluated_cards={question.get("cardId") for question in quiz["questions"] if quiz["answers"].get(question.get("id"),{}).get("feedback")}
            for week in all_weeks:
                lesson_cards={card["id"] for card in all_cards if card.get("weekId")==week["id"]}
                if lesson_cards and lesson_cards <= covered and lesson_cards <= evaluated_cards:
                    self.store.event("lesson_completed",quiz["courseId"],{"weekId":week["id"],"quizId":quiz_id,"summary":"수업의 모든 카드를 평가까지 마침","synthetic":bool(quiz.get("synthetic")),"seedBatch":quiz.get("seedBatch")},quiz_id+":lesson:"+week["id"])
        if quiz.get("reviewPlanId") and all_evaluated:
            self.store.complete_review_plan(quiz["reviewPlanId"],quiz["id"])
            plan=next((p for p in self.store.review_plans(quiz["courseId"]) if p["id"]==quiz["reviewPlanId"]),None)
            if plan:
                # A fresh recall has already produced an authoritative FSRS due date.
                fsrs=self.store.conn.execute("SELECT 1 FROM recall_cards WHERE address=?",(quiz["courseId"]+"::"+plan["address"],)).fetchone()
                if not fsrs:
                    related=[item for item in feedback if any(self.store._address(r)==plan["address"] for r in item.get("stateResult",{}).get("requirements",[]))]
                    passed=bool(related) and all(item.get("passed") for item in related)
                    delay=3 if passed else 1
                    reason=("통과했지만 독립 근거가 아직 충분하지 않아 3일 뒤 다시 확인합니다." if passed else "아직 확인되지 않은 연결이 있어 다음 날 짧게 다시 확인합니다.")
                    due=(datetime.now(ZoneInfo("Asia/Seoul"))+timedelta(days=delay)).replace(hour=9,minute=0,second=0,microsecond=0).astimezone(timezone.utc).isoformat()
                    self.store.ensure_review_plan(quiz["courseId"],plan["week_id"],plan["card_id"],plan["address"],due,reason)
        for plan_id in quiz.get("reviewPlanIds",[]):
            if all_evaluated: self.store.complete_review_plan(plan_id,quiz_id)
        self.store.event("quiz_finished",quiz["courseId"],{"quizId":quiz_id,"summary":f"연습 기록 저장: {len(feedback)}문항 평가, {pending}문항 미제출"},quiz_id+":finished")
        for q in quiz["questions"]:
            if quiz["answers"].get(q["id"],{}).get("feedback"):
                self.store.event("feedback_viewed",quiz["courseId"],{"familyId":q["familyId"],"cardId":q["cardId"],"summary":"묶음 종료 후 풀이 확인"},data["requestId"]+":"+q["id"])
        return {"quiz":self.public_quiz(quiz),"feedback":feedback,"summary":quiz["summary"]}

    def import_text(self,data,text,source,cancel,progress):
        if len(text.strip())<50: raise ValueError("읽을 수 있는 본문이 너무 적습니다. 스캔 PDF라면 필요한 내용을 텍스트로 붙여 넣어주세요.")
        if len(text)>60000: raise ValueError("한 번에 한 수업 분량(60,000자 이하)을 추가해주세요. 이번 자료는 저장만 했고 분석하지 않았습니다.")
        bootstrap=self.store.bootstrap(); courses=bootstrap["courses"]
        course=next((c for c in courses if c["id"]==data.get("courseId")),None)
        course_title=str(data.get("courseTitle","")).strip()[:120]
        if not course: course=next((c for c in courses if c["title"]==course_title),None)
        if not course and not course_title: raise ValueError("기존 과목을 선택하거나 새 과목 이름을 적어주세요.")
        course=copy.deepcopy(course) if course else {"id":uid("course"),"title":course_title,"scopeStatus":"unconfirmed","weeks":[]}
        week=next((w for w in course["weeks"] if w["id"]==data.get("weekId")),None)
        if data.get("weekId") and not week:
            raise ValueError("선택한 주차가 이 과목에 없습니다.")
        week_title=str(data.get("weekTitle","")).strip()[:120]
        if not week and not week_title: raise ValueError("기존 주차를 선택하거나 새 주차 이름을 적어주세요.")
        week=copy.deepcopy(week) if week else {"id":uid("week"),"title":week_title,"overview":"","cardIds":[],"preview":{"prompt":"","solution":""},"scopeStatus":"unconfirmed"}
        # A lesson is one of the two entries in a teaching week.  The client
        # supplies numbers; the backend alone owns the stable review grouping.
        try: week_number=int(data.get("weekNumber"))
        except (TypeError,ValueError): raise ValueError("주차 번호는 1 이상의 정수로 입력해주세요.")
        try: lesson_number=int(data.get("lessonNumber"))
        except (TypeError,ValueError): raise ValueError("강 번호는 1 또는 2로 입력해주세요.")
        if week_number<1 or lesson_number not in (1,2): raise ValueError("주차는 1 이상, 강은 1 또는 2로 입력해주세요.")
        peer_group=None
        for other in course["weeks"]:
            if other.get("id")!=week["id"] and other.get("weekNumber")==week_number and other.get("lessonNumber")==lesson_number:
                raise ValueError("같은 주차·강으로 등록된 수업이 이미 있습니다.")
            if other.get("id")!=week["id"] and other.get("weekNumber")==week_number and other.get("reviewGroupId"): peer_group=other["reviewGroupId"]
        week.update(weekNumber=week_number,lessonNumber=lesson_number,reviewGroupId=week.get("reviewGroupId") or peer_group or f"{course['id']}-week-{week_number:02}")
        existing_cards=[c for c in bootstrap["cards"] if c.get("courseId")==course["id"] and c.get("weekId")==week["id"]]
        seen=set(); existing_sources=[]
        for card in existing_cards:
            source_info=card.get("source",{}); key=source_info.get("assetId") or source_info.get("path")
            if not key or key in seen: continue
            seen.add(key)
            if source_info.get("assetId"):
                asset=self.store.asset(source_info["assetId"])
                if asset and str(asset.get("mime","")).startswith("text/") or (asset and "markdown" in str(asset.get("mime", ""))):
                    existing_sources.append({"label":source_info.get("label","기존 상세 원문"),"text":Path(asset["path"]).read_text(encoding="utf-8")})
            elif source_info.get("path"):
                repo=Path(__file__).resolve().parents[3]; target=(repo/source_info["path"]).resolve(); allowed=(repo/"course-materials").resolve()
                if target.is_relative_to(allowed) and target.suffix.lower() in {".md",".txt"}: existing_sources.append({"label":source_info.get("label","기존 원문"),"text":target.read_text(encoding="utf-8")})
        context_size=len(text)+sum(len(entry["text"]) for entry in existing_sources)
        if context_size>60000: raise ValueError("새 자료와 같은 주차의 기존 원문이 60,000자를 넘어 자르지 않고 등록을 멈췄습니다. 자료를 나누어 추가해주세요.")
        result=self.ai.ask("사용자 필기도 하나의 출처로 취급하되, 사용자가 적었다는 이유만으로 맞다고 평가하지 말고 근거를 일반 설명과 출처 차이로 바꾸어 제시하세요. 새 자료를 기존 주차 맥락과 비교해 카드와 상세 복습 해설을 만드세요. existingWeekContext는 읽기 전용이며 기존 설명·카드·기록을 반복하거나 수정하지 마세요. 새 자료가 더한 설명과 기존 내용과의 충돌을 구분하세요. reviewMarkdown은 짧은 요약이나 PPT 문구 나열이 아니라, 슬라이드만 본 학생도 단독으로 이해할 수 있는 충분한 한국어 해설입니다. 정의→왜 필요한가→조건→예제/수식→흔한 혼동 순으로 연결해 설명하세요. 자료에 명시된 교수님 말씀·녹취 보조설명은 실제 입력 근거가 있을 때만 정확히 한 줄 `[교수님 말씀 · 파일명/시각] 발화 요지` 형식으로 쓰고, 실제 입력에 없는 발화나 인용을 만들지 마세요. PPT와 같은 설명이면 일반 본문에 통합하세요. 각 설명에는 파일명과 PDF [p.N] 출처를 붙이세요. 서로 모순되는 자료는 출처와 차이를 드러내세요. 읽을 텍스트가 없는 사진이나 스캔을 확인했다고 주장하지 마세요. 각 새 카드 title과 정확히 같은 `## 제목`을 포함하세요. 수식은 $...$ 또는 $$ 블록으로 쓰세요.\n"+json.dumps({"course":course["title"],"week":week["title"],"existingCards":[self._context(c) for c in existing_cards],"existingWeekContext":existing_sources,"newSources":data.get("materials",[]),"source":text},ensure_ascii=False),IMPORT_SCHEMA,cancel=cancel,progress=progress)
        if not 1<=len(result["cards"])<=12: raise AIError("자료를 적절한 분량으로 나누지 못했습니다.")
        review_markdown=str(result.get("reviewMarkdown","")).strip()
        if len(review_markdown)<400: raise AIError("상세 복습 해설이 충분히 생성되지 않아 자료 등록을 보류했습니다.")
        section_titles={line[3:].strip() for line in review_markdown.splitlines() if line.startswith('## ')}
        missing=[raw["title"] for raw in result["cards"] if raw['title'].strip() not in section_titles]
        if missing: raise AIError("상세 복습 해설의 카드 소제목이 빠져 자료 등록을 보류했습니다: "+", ".join(missing))
        week_id=week["id"]
        review_asset=self.store.save_asset(review_markdown.encode("utf-8"),f"{course['id']}-{week_id}-review.md","text/markdown; charset=utf-8")["id"]
        cards=[]
        for raw in result["cards"]:
            cid=uid("ku")
            details=[];requirements=[]
            for d in raw["details"]:
                did=uid("detail")
                details.append({"id":did,"title":d["title"],"text":d["text"]})
                requirements.append({"id":uid("req"),"detailId":did,"behavior":d["behavior"],"conditions":"필요한 조건은 문제에서 제시, 내용 힌트 없음","level":d["level"],"required":True})
            cards.append({"id":cid,"courseId":course["id"],"weekId":week_id,"version":1,"title":raw["title"],"goal":raw["goal"],"core":raw["core"],"details":details,"requirements":requirements,"figure":{"type":raw["figureType"],"caption":raw["caption"]},"source":{"label":str(data.get("title") or source.get("label") or "상세 복습 원문"),"assetId":review_asset,"locator":"## "+raw["title"],"materialIds":[]},"reviewStatus":"ai-generated-review-needed"})
        week["overview"]=(week.get("overview","")+"\n\n"+result["overview"]).strip()
        week["cardIds"]=week.get("cardIds",[])+[c["id"] for c in cards]
        existing_week_index=next((i for i,w in enumerate(course["weeks"]) if w["id"]==week_id),None)
        if existing_week_index is None: course["weeks"].append(week)
        else: course["weeks"][existing_week_index]=week
        if cancel.is_set(): raise Cancelled("자료 정리를 멈췄습니다. 등록한 원문은 보관됩니다.")
        material_ids=[]
        for material in data.get("materials",[]):
            material_ids.append(self.store.add_material(course["id"],week_id,material.get("role","supplement"),material.get("title") or material.get("filename","자료"),material.get("filename","자료"),material.get("mime","application/octet-stream"),material["assetId"],material.get("textAssetId"),review_path=review_asset,slides=material.get("slides")))
        for card in cards: card["source"]["materialIds"]=material_ids
        self.store.add_import(course,cards)
        return {"course":course,"cards":cards,"message":"자료를 정리했습니다. 시험 범위는 아직 미확정이며, 생성한 설명을 원문과 함께 확인해주세요."}
