"""Local durable store for the study room.

Cards are editable JSON; evidence is append-only SQLite.  A small revision ledger makes a
card write recoverable when the process stops between filesystem and database work.
"""
from __future__ import annotations

import json, sqlite3, threading, uuid, sys, math
from copy import deepcopy
from datetime import datetime, timezone, timedelta
from fractions import Fraction
from pathlib import Path
from typing import Any

from assessment import evaluate_attempt

_deps = Path(__file__).with_name("deps")
if _deps.exists(): sys.path.insert(0, str(_deps))
try:
    from fsrs.card import Card
    from fsrs.rating import Rating
    from fsrs.scheduler import Scheduler
except ImportError:
    Card = Rating = Scheduler = None


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class StudyStore:
    def __init__(self, root: str | Path | None = None):
        self.root = Path(root or Path(__file__).resolve().parents[1])
        self.data = self.root / "data"
        self.data.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data / "learning.sqlite"
        self._lock = threading.RLock()  # one in-process writer; SQLite serializes processes
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._schema()
        self._recover_pending_revisions()

    def close(self) -> None:
        self.conn.close()

    def _schema(self) -> None:
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY, kind TEXT NOT NULL, course_id TEXT,
          payload TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS requests(id TEXT PRIMARY KEY, result TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS ui(id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS chats(id TEXT PRIMARY KEY, course_id TEXT, week_id TEXT, card_id TEXT, role TEXT, text TEXT, proposal TEXT, created_at TEXT);
        CREATE TABLE IF NOT EXISTS quizzes(id TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS assets(id TEXT PRIMARY KEY, name TEXT, mime TEXT, path TEXT, created_at TEXT);
        CREATE TABLE IF NOT EXISTS card_revisions(id TEXT PRIMARY KEY, card_id TEXT, old_json TEXT, new_json TEXT,
          path TEXT, status TEXT, created_at TEXT);
        CREATE TABLE IF NOT EXISTS attempts(id TEXT PRIMARY KEY, course_id TEXT, card_id TEXT, family_id TEXT,
          observed_at TEXT, independent INTEGER, fresh INTEGER, valid INTEGER, invalidated INTEGER DEFAULT 0,
          payload TEXT NOT NULL, evaluation TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS learner_states(address TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS recall_events(id TEXT PRIMARY KEY, address TEXT NOT NULL, rating TEXT NOT NULL,
          observed_at TEXT NOT NULL, source_attempt_id TEXT, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS recall_cards(address TEXT PRIMARY KEY, card_json TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS materials(id TEXT PRIMARY KEY, course_id TEXT NOT NULL, week_id TEXT NOT NULL, role TEXT NOT NULL,
          title TEXT NOT NULL, filename TEXT NOT NULL, mime TEXT NOT NULL, asset_id TEXT NOT NULL, text_asset_id TEXT, review_path TEXT, slides TEXT, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS attempt_requirement_invalidations(attempt_id TEXT NOT NULL, address TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(attempt_id,address));
        CREATE TABLE IF NOT EXISTS attempt_notes(attempt_id TEXT PRIMARY KEY, text TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS attempt_note_revisions(id TEXT PRIMARY KEY, attempt_id TEXT NOT NULL, text TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS attempt_feedback(attempt_id TEXT PRIMARY KEY, feedback TEXT NOT NULL, answer_snapshot TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS review_plans(id TEXT PRIMARY KEY, course_id TEXT NOT NULL, week_id TEXT NOT NULL, card_id TEXT NOT NULL, address TEXT NOT NULL, due_at TEXT NOT NULL, reason TEXT NOT NULL, status TEXT NOT NULL, completed_at TEXT, quiz_id TEXT, created_at TEXT NOT NULL, UNIQUE(course_id,card_id,address,status));
        """)
        self.conn.commit()

    def _request(self, request_id: str | None):
        if not request_id: return None
        row = self.conn.execute("SELECT result FROM requests WHERE id=?", (request_id,)).fetchone()
        return json.loads(row["result"]) if row else None

    def _remember(self, request_id: str | None, result: Any) -> Any:
        if request_id:
            self.conn.execute("INSERT INTO requests VALUES (?,?,?)", (request_id, json.dumps(result), utcnow()))
        return result

    def _card_path(self, card_id: str) -> Path:
        for path in (self.data / "courses").glob(f"*/cards/{card_id}.json"):
            return path
        raise KeyError(f"카드를 찾을 수 없습니다: {card_id}")

    def _read_json(self, path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))

    def _atomic_json(self, path: Path, value: dict[str, Any]) -> None:
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp.replace(path)

    def _recover_pending_revisions(self) -> None:
        for row in self.conn.execute("SELECT * FROM card_revisions WHERE status='pending'"):
            path = Path(row["path"]); intended = json.loads(row["new_json"])
            if not path.exists() or self._read_json(path) != intended: self._atomic_json(path, intended)
            self.conn.execute("UPDATE card_revisions SET status='done' WHERE id=?", (row["id"],))
        self.conn.commit()

    def bootstrap(self) -> dict[str, Any]:
        courses, cards = [], []
        course_root = self.data / "courses"
        for path in sorted(course_root.glob("*/course.json")):
            course = self._read_json(path); courses.append(course)
            cards.extend(self._present_card(self._read_json(p)) for p in sorted(path.parent.glob("cards/*.json")))
        return {"courses": courses, "cards": cards, "ui": self.get_ui(), "status": {"connected": False, "message": "AI 연결 확인 필요"}, "activeQuiz": self.active_quiz()}

    def get_card(self, card_id: str) -> dict[str, Any]: return self._present_card(self._read_json(self._card_path(card_id)))

    def _present_card(self, card: dict[str, Any]) -> dict[str, Any]:
        """Attach derived state without ever writing learning state back into card JSON."""
        shown = deepcopy(card)
        requirements = shown.get("requirements") or []
        states = []
        for req in requirements:
            address = "|".join(str(req.get(k) or "") for k in ("detailId", "behavior", "conditions"))
            row = self.conn.execute("SELECT value FROM learner_states WHERE address=?", (f"{card.get('courseId','')}::{address}",)).fetchone()
            state=json.loads(row["value"]) if row else {"label":"미평가", "confirmed":False, "level":0}
            recall=self.conn.execute("SELECT card_json FROM recall_cards WHERE address=?",(f"{card.get('courseId','')}::{address}",)).fetchone()
            if recall:
                due=datetime.fromisoformat(json.loads(recall["card_json"])["due"])
                state["retention"]={"label":"확인 권장" if due <= datetime.now(timezone.utc) else "유지 예상","nextReviewAt":due.isoformat()}
            else: state["retention"]={"label":"기록 부족"}
            states.append(state)
        total = sum(1 for req in requirements if req.get("required", True))
        confirmed = sum(bool(s["confirmed"]) for req, s in zip(requirements, states) if req.get("required", True))
        observed = any(state.get("observed") for state in states)
        shown["state"] = {"label": "목표 확인" if total and confirmed == total else ("학습 중" if observed else "미평가"),
                          "confirmed": confirmed, "total": total,
                          "nextAction": (card.get("state") or {}).get("nextAction") or ("한 가지를 먼저 확인해 보세요." if confirmed < total else "필요할 때 복습하세요.")}
        shown["requirementStates"] = {req.get("id"): state for req, state in zip(requirements, states)}
        shown["state"]["correctionNeeded"] = any(state.get("correctionNeeded") for state in states)
        return shown

    def edit_card(self, card_id: str, expected_version: int, patch: dict[str, Any], reason: str | None, request_id: str | None) -> dict[str, Any]:
        with self._lock:
            previous = self._request(request_id)
            if previous is not None: return previous
            path = self._card_path(card_id); old = self._read_json(path)
            if old.get("version") != expected_version: raise ValueError("카드가 다른 곳에서 바뀌었습니다. 새 내용을 확인하세요.")
            allowed = {"core", "title", "goal", "details"}
            if set(patch) - allowed: raise ValueError("허용되지 않은 카드 수정입니다.")
            new = deepcopy(old); new.update(deepcopy(patch)); new["version"] = expected_version + 1
            revision = str(uuid.uuid4())
            self.conn.execute("INSERT INTO card_revisions VALUES (?,?,?,?,?,?,?)", (revision, card_id, json.dumps(old,ensure_ascii=False), json.dumps(new,ensure_ascii=False), str(path), "pending", utcnow()))
            self.conn.commit(); self._atomic_json(path, new)
            self.conn.execute("UPDATE card_revisions SET status='done' WHERE id=?", (revision,))
            self._invalidate_changed_details(old, new, "카드 내용 변경")
            self.event("card_edited", old.get("courseId"), {"cardId":card_id,"reason":reason,"revision":revision}, None)
            result = self._remember(request_id, new); self.conn.commit(); return result

    def undo_card(self, card_id: str, expected_version: int, request_id: str | None) -> dict[str, Any]:
        with self._lock:
            previous=self._request(request_id)
            if previous is not None: return previous
            current=self._read_json(self._card_path(card_id))
            if current.get("version") != expected_version: raise ValueError("카드 버전이 달라 되돌릴 수 없습니다.")
            row=self.conn.execute("SELECT * FROM card_revisions WHERE card_id=? AND status='done' ORDER BY created_at DESC LIMIT 1",(card_id,)).fetchone()
            if not row: raise ValueError("되돌릴 카드 수정 이력이 없습니다.")
            restored=json.loads(row["old_json"]); restored["version"]=expected_version+1
            path=self._card_path(card_id); rev=str(uuid.uuid4())
            self.conn.execute("INSERT INTO card_revisions VALUES (?,?,?,?,?,?,?)",(rev,card_id,json.dumps(current,ensure_ascii=False),json.dumps(restored,ensure_ascii=False),str(path),"pending",utcnow())); self.conn.commit()
            self._atomic_json(path,restored); self.conn.execute("UPDATE card_revisions SET status='done' WHERE id=?",(rev,))
            self._invalidate_changed_details(current, restored, "카드 수정 되돌림")
            result=self._remember(request_id,restored); self.conn.commit(); return result

    def get_ui(self):
        row=self.conn.execute("SELECT value FROM ui WHERE id=1").fetchone(); return json.loads(row["value"]) if row else {}
    def save_ui(self, value: dict[str,Any]):
        with self._lock: self.conn.execute("INSERT INTO ui VALUES(1,?,?) ON CONFLICT(id) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",(json.dumps(value,ensure_ascii=False),utcnow())); self.conn.commit(); return {"saved":True}
    def event(self, kind, course_id, payload, request_id=None):
        with self._lock:
            old=self._request(request_id)
            if old is not None:return old
            eid=str(uuid.uuid4()); result={"saved":True,"id":eid}
            self.conn.execute("INSERT INTO events VALUES(?,?,?,?,?)",(eid,kind,course_id,json.dumps(payload,ensure_ascii=False),utcnow()))
            # Reading or a short explanation unlocks a correction attempt, but previews/skips do not.
            if payload.get("cardId") and (kind == "card_explained" or (kind == "exposure" and payload.get("kind") in {"read","reopen","explanation"})):
                try:
                    card=self._read_json(self._card_path(payload["cardId"]))
                    for requirement in card.get("requirements",[]): self._replay(self._address(requirement),course_id)
                except KeyError: pass
            self._remember(request_id,result); self.conn.commit();return result
    def history(self, course_id):
        rows=self.conn.execute("SELECT id,kind,payload,created_at FROM events WHERE course_id=? ORDER BY created_at DESC",(course_id,)); return {"events":[{"id":r["id"],"type":r["kind"],"createdAt":r["created_at"],**json.loads(r["payload"])} for r in rows]}
    def ensure_review_plan(self, course_id, week_id, card_id, address, due_at, reason):
        """One conservative first-review reminder per unresolved target, without claiming mastery."""
        with self._lock:
            row=self.conn.execute("SELECT * FROM review_plans WHERE course_id=? AND card_id=? AND address=? AND status='scheduled'",(course_id,card_id,address)).fetchone()
            if row: return dict(row)
            value={"id":"baseline:"+uuid.uuid4().hex,"course_id":course_id,"week_id":week_id,"card_id":card_id,"address":address,"due_at":due_at,"reason":reason,"status":"scheduled","completed_at":None,"quiz_id":None,"created_at":utcnow()}
            self.conn.execute("INSERT INTO review_plans VALUES(:id,:course_id,:week_id,:card_id,:address,:due_at,:reason,:status,:completed_at,:quiz_id,:created_at)",value); self.conn.commit(); return value
    def review_plans(self, course_id=None):
        rows=self.conn.execute("SELECT * FROM review_plans"+(" WHERE course_id=?" if course_id else "")+" ORDER BY due_at",(course_id,) if course_id else ())
        return [dict(r) for r in rows]
    def complete_review_plan(self, plan_id, quiz_id):
        with self._lock:
            self.conn.execute("UPDATE review_plans SET status='completed',completed_at=?,quiz_id=? WHERE id=? AND status='scheduled'",(utcnow(),quiz_id,plan_id)); self.conn.commit()
    def reopen_review_plan(self, plan_id):
        """Make an invalidated completed plan runnable again without duplicating its target."""
        with self._lock:
            plan=self.conn.execute("SELECT * FROM review_plans WHERE id=?",(plan_id,)).fetchone()
            if not plan: raise KeyError("복습 일정을 찾을 수 없습니다.")
            plan=dict(plan)
            sibling=self.conn.execute("SELECT * FROM review_plans WHERE course_id=? AND card_id=? AND address=? AND status='scheduled' AND id<>? ORDER BY due_at DESC LIMIT 1",(plan["course_id"],plan["card_id"],plan["address"],plan_id)).fetchone()
            if sibling:
                # Keep the old audit row; the newer single scheduled plan is authoritative.
                return dict(sibling)
            if plan["status"]!="scheduled":
                self.conn.execute("UPDATE review_plans SET status='scheduled',completed_at=NULL,quiz_id=NULL WHERE id=?",(plan_id,)); self.conn.commit()
                plan.update(status="scheduled",completed_at=None,quiz_id=None)
            return plan
    def attempts(self, course_id, week_id=None, card_id=None, attempt_ids=None):
        clauses=["a.course_id=?"];args=[course_id]
        if card_id: clauses.append("a.card_id=?");args.append(card_id)
        if attempt_ids is not None:
            if not attempt_ids:return {"attempts":[]}
            clauses.append("a.id IN ("+",".join("?" for _ in attempt_ids)+")");args.extend(attempt_ids)
        rows=self.conn.execute("SELECT a.*,n.text AS note FROM attempts a LEFT JOIN attempt_notes n ON n.attempt_id=a.id WHERE "+" AND ".join(clauses)+" ORDER BY a.observed_at DESC",args).fetchall()
        # Quiz records are large immutable JSON blobs: parse each only once per history request.
        questions={}; legacy_answers={}
        for quiz_row in self.conn.execute("SELECT id,value FROM quizzes ORDER BY updated_at DESC"):
            quiz=json.loads(quiz_row["value"])
            for question in quiz.get("questions",[]): questions.setdefault(question.get("id"),(question,quiz_row["id"]))
            for answer in quiz.get("answers",{}).values():
                attempt_id=answer.get("attemptId")
                if attempt_id: legacy_answers.setdefault(attempt_id,answer)
        ids=[row["id"] for row in rows]; feedback_by_attempt={}; notes_by_attempt={}; revisions_by_attempt={}
        if ids:
            marks=",".join("?" for _ in ids)
            for row in self.conn.execute(f"SELECT attempt_id,feedback,answer_snapshot FROM attempt_feedback WHERE attempt_id IN ({marks})",ids): feedback_by_attempt[row["attempt_id"]]=row
            for row in self.conn.execute(f"SELECT attempt_id,text,updated_at FROM attempt_notes WHERE attempt_id IN ({marks})",ids): notes_by_attempt[row["attempt_id"]]=row
            for row in self.conn.execute(f"SELECT attempt_id,text,updated_at FROM attempt_note_revisions WHERE attempt_id IN ({marks}) ORDER BY updated_at",ids): revisions_by_attempt.setdefault(row["attempt_id"],[]).append(row)
        cards={}; courses={}
        def card_for(identifier):
            if identifier not in cards: cards[identifier]=self._read_json(self._card_path(identifier))
            return cards[identifier]
        def course_for(identifier):
            if identifier not in courses: courses[identifier]=self._read_json(self.data/"courses"/identifier/"course.json")
            return courses[identifier]
        result=[]
        for row in rows:
            try: card=card_for(row["card_id"])
            except KeyError: continue
            if week_id and card.get("weekId")!=week_id: continue
            payload=json.loads(row["payload"]); evaluation=json.loads(row["evaluation"]); course=course_for(row["course_id"])
            week=next((w for w in course.get("weeks",[]) if w.get("id")==card.get("weekId")),{})
            pair=questions.get(payload.get("questionId")); question,quiz_id=(deepcopy(pair[0]),pair[1]) if pair else (None,None)
            saved=feedback_by_attempt.get(row["id"]); legacy=None if saved else legacy_answers.get(row["id"])
            quiz_feedback=deepcopy(legacy.get("feedback")) if legacy else None
            answer_snapshot={k:deepcopy(legacy[k]) for k in ("text","comment","assetId","submittedAt","versions") if k in legacy} if legacy else None
            note=notes_by_attempt.get(row["id"]); revisions=revisions_by_attempt.get(row["id"],[])
            result.append({"id":row["id"],"quizId":quiz_id,"courseId":row["course_id"],"courseTitle":course.get("title",""),"weekId":card.get("weekId"),"weekTitle":week.get("title",""),"cardId":row["card_id"],"cardTitle":card.get("title",""),"questionId":payload.get("questionId"),"question":question,"observedAt":row["observed_at"],"text":payload.get("text",""),"assetId":payload.get("assetId"),"independent":bool(row["independent"]),"fresh":bool(row["fresh"]),"invalidated":bool(row["invalidated"]),"synthetic":bool(payload.get("synthetic")),"seedBatch":payload.get("seedBatch"),"evaluation":evaluation,"feedback":json.loads(saved["feedback"]) if saved else (quiz_feedback or evaluation.get("feedback")),"answerSnapshot":json.loads(saved["answer_snapshot"]) if saved else answer_snapshot,"note":{"text":note["text"],"updatedAt":note["updated_at"]} if note else {"text":"","updatedAt":None},"noteRevisions":[{"text":r["text"],"updatedAt":r["updated_at"]} for r in revisions]})
        return {"attempts":result}

    def records(self, course_id=None):
        """Lightweight index: quiz rows stay compact until one is opened."""
        # The index must not call bootstrap(): bootstrap presents every card and
        # its derived learner state, which turns a small records list into N+1 IO.
        courses={path.parent.name:self._read_json(path) for path in self.data.joinpath("courses").glob("*/course.json")}
        cards={}
        def card_for(identifier):
            if identifier not in cards:
                try: cards[identifier]=self._read_json(self._card_path(identifier))
                except KeyError: cards[identifier]={}
            return cards[identifier]
        quiz_rows=[]; linked_attempts=set(); feedback={}
        attempt_questions=[(row["id"],row["course_id"],json.loads(row["payload"]).get("questionId")) for row in self.conn.execute("SELECT id,course_id,payload FROM attempts"+(" WHERE course_id=?" if course_id else ""),(course_id,) if course_id else ())]
        for row in self.conn.execute("SELECT id,value FROM quizzes ORDER BY updated_at DESC"):
            quiz=json.loads(row["value"])
            if course_id and quiz.get("courseId")!=course_id: continue
            quiz_rows.append((row["id"],quiz))
            question_ids=[q.get("id") for q in quiz.get("questions",[]) if q.get("id")]
            if question_ids: linked_attempts.update(identifier for identifier,attempt_course,question_id in attempt_questions if attempt_course==quiz.get("courseId") and question_id in question_ids)
        if linked_attempts:
            marks=','.join('?' for _ in linked_attempts)
            feedback={row["attempt_id"]:json.loads(row["feedback"]) for row in self.conn.execute(f"SELECT attempt_id,feedback FROM attempt_feedback WHERE attempt_id IN ({marks})",list(linked_attempts))}
        summaries=[]
        for quiz_id,quiz in quiz_rows:
            course=courses.get(quiz.get("courseId"),{}); week=next((w for w in course.get("weeks",[]) if w.get("id")==quiz.get("weekId")),{})
            answers=quiz.get("answers",{}); questions=quiz.get("questions",[]); evaluated=passed=held=wrong=0
            for question in questions:
                answer=answers.get(question.get("id"),{}); attempt_id=answer.get("attemptId"); result=feedback.get(attempt_id,answer.get("feedback"))
                if not result: continue
                evaluated+=1; held+=bool(result.get("held")); passed+=result.get("passed") is True
                if result.get("passed") is False and not result.get("held"):
                    wrong+=1
            schedule=quiz.get("reviewSchedule") or {}; kind=schedule.get("kind") or ("lessonReview" if quiz.get("daily") else "quiz")
            summaries.append({"id":quiz_id,"courseId":quiz.get("courseId"),"courseTitle":course.get("title",""),"weekId":quiz.get("weekId"),"weekTitle":week.get("title",""),"weekNumber":week.get("weekNumber"),"lessonNumber":week.get("lessonNumber"),"reviewKind":kind,"status":quiz.get("status"),"createdAt":quiz.get("createdAt"),"finishedAt":quiz.get("finishedAt"),"totalQuestions":len(questions),"submittedQuestions":sum(bool(a.get("submitted")) for a in answers.values()),"evaluatedQuestions":evaluated,"passedQuestions":passed,"heldQuestions":held,"wrongQuestions":wrong,"synthetic":bool(quiz.get("synthetic")),"seedBatch":quiz.get("seedBatch")})
        legacy={}
        sql="SELECT id,course_id,card_id,observed_at,payload,evaluation FROM attempts"+(" WHERE course_id=?" if course_id else "")
        for row in self.conn.execute(sql,(course_id,) if course_id else ()):
            if row["id"] in linked_attempts: continue
            card=card_for(row["card_id"]); course=courses.get(row["course_id"],{}); week=next((w for w in course.get("weeks",[]) if w.get("id")==card.get("weekId")),{})
            key=f"legacy:{row['course_id']}:{row['card_id']}"; item=legacy.setdefault(key,{"id":key,"courseId":row["course_id"],"courseTitle":course.get("title",""),"weekId":card.get("weekId"),"weekTitle":week.get("title",""),"weekNumber":week.get("weekNumber"),"lessonNumber":week.get("lessonNumber"),"reviewKind":"legacy","cardId":row["card_id"],"title":card.get("title",""),"attemptCount":0,"createdAt":row["observed_at"],"finishedAt":row["observed_at"],"latestAt":row["observed_at"],"evaluatedQuestions":0,"passedQuestions":0,"heldQuestions":0,"wrongQuestions":0,"synthetic":False,"seedBatch":None})
            item["attemptCount"]+=1; item["createdAt"]=min(item["createdAt"],row["observed_at"]); item["finishedAt"]=item["latestAt"]=max(item["latestAt"],row["observed_at"])
            payload=json.loads(row["payload"]); evaluation=json.loads(row["evaluation"]); item["synthetic"]|=bool(payload.get("synthetic")); item["seedBatch"]=item["seedBatch"] or payload.get("seedBatch")
            summary=evaluation.get("feedback") or {}; item["evaluatedQuestions"]+=1; item["passedQuestions"]+=summary.get("passed") is True; item["heldQuestions"]+=bool(summary.get("held")); item["wrongQuestions"]+=summary.get("passed") is False and not summary.get("held")
        return {"quizzes":summaries,"legacy":sorted(legacy.values(),key=lambda item:item["latestAt"],reverse=True)}

    def record_detail(self, record_id, course_id=None):
        if record_id.startswith("legacy:"):
            _,stored_course,card_id=record_id.split(":",2)
            if course_id and course_id!=stored_course: raise ValueError("선택한 과목의 기록이 아닙니다.")
            known={(quiz.get("courseId"),question.get("id")) for row in self.conn.execute("SELECT value FROM quizzes") for quiz in [json.loads(row["value"])] for question in quiz.get("questions",[]) if question.get("id")}
            linked={row["id"] for row in self.conn.execute("SELECT id,course_id,payload FROM attempts") if (row["course_id"],json.loads(row["payload"]).get("questionId")) in known}
            attempts=[a for a in self.attempts(stored_course,card_id=card_id)["attempts"] if a["id"] not in linked]
            summary=next((item for item in self.records(stored_course)["legacy"] if item["id"]==record_id),None)
            return {"record":summary,"legacy":summary,"attempts":attempts}
        quiz=self.get_quiz(record_id)
        if not quiz: raise KeyError("테스트 기록을 찾지 못했습니다.")
        if course_id and course_id!=quiz.get("courseId"): raise ValueError("선택한 과목의 기록이 아닙니다.")
        question_ids=[q.get("id") for q in quiz.get("questions",[]) if q.get("id")]
        if question_ids:
            attempt_ids=[row["id"] for row in self.conn.execute("SELECT id,payload FROM attempts WHERE course_id=?",(quiz.get("courseId"),)) if json.loads(row["payload"]).get("questionId") in question_ids]
        else: attempt_ids=[]
        attempts=self.attempts(quiz.get("courseId"),attempt_ids=attempt_ids)["attempts"]
        summary=next((item for item in self.records(quiz.get("courseId"))["quizzes"] if item["id"]==record_id),None)
        return {"record":summary,"quiz":summary,"attempts":attempts}

    def _question_snapshot(self, question_id):
        if not question_id: return None, None
        for row in self.conn.execute("SELECT id,value FROM quizzes ORDER BY updated_at DESC"):
            quiz=json.loads(row["value"])
            question=next((q for q in quiz.get("questions",[]) if q.get("id")==question_id),None)
            if question:
                return deepcopy(question), row["id"]
        return None, None
    def _quiz_attempt_snapshot(self, attempt_id):
        """Legacy fallback only; new grading writes an immutable attempt_feedback row."""
        for row in self.conn.execute("SELECT value FROM quizzes ORDER BY updated_at DESC"):
            quiz=json.loads(row["value"])
            for answer in quiz.get("answers",{}).values():
                if answer.get("attemptId")==attempt_id:
                    return deepcopy(answer.get("feedback")), {k:deepcopy(answer[k]) for k in ("text","comment","assetId","submittedAt","versions") if k in answer}
        return None, None
    def save_attempt_feedback(self, attempt_id, feedback, answer_snapshot):
        """Keep the learner-facing AI feedback tied to this immutable assessment ID."""
        with self._lock:
            if not self.conn.execute("SELECT 1 FROM attempts WHERE id=?",(attempt_id,)).fetchone(): raise KeyError("답안을 찾을 수 없습니다.")
            self.conn.execute("INSERT OR IGNORE INTO attempt_feedback VALUES(?,?,?,?)",(attempt_id,json.dumps(feedback,ensure_ascii=False),json.dumps(answer_snapshot,ensure_ascii=False),utcnow()))
            self.conn.commit()
    def save_attempt_note(self, attempt_id, text, request_id=None):
        with self._lock:
            old=self._request(request_id)
            if old is not None: return old
            if not self.conn.execute("SELECT 1 FROM attempts WHERE id=?",(attempt_id,)).fetchone(): raise KeyError("답안을 찾을 수 없습니다.")
            text=str(text).strip()
            if len(text)>6000: raise ValueError("오답 정리는 6,000자 이내로 적어주세요.")
            stamp=utcnow()
            self.conn.execute("INSERT INTO attempt_notes VALUES(?,?,?) ON CONFLICT(attempt_id) DO UPDATE SET text=excluded.text,updated_at=excluded.updated_at",(attempt_id,text,stamp))
            self.conn.execute("INSERT INTO attempt_note_revisions VALUES(?,?,?,?)",(str(uuid.uuid4()),attempt_id,text,stamp))
            result={"saved":True,"note":{"text":text,"updatedAt":stamp}}
            self._remember(request_id,result); self.conn.commit(); return result
    def chat_messages(self, course_id, week_id):
        rows=self.conn.execute("SELECT * FROM chats WHERE course_id=? AND week_id=? ORDER BY created_at",(course_id,week_id)); return {"messages":[{"role":r["role"],"text":r["text"],"cardId":r["card_id"],"createdAt":r["created_at"],"proposal":json.loads(r["proposal"]) if r["proposal"] else None} for r in rows]}
    def save_chat(self,course_id,week_id,card_id,role,text,proposal=None):
        with self._lock:self.conn.execute("INSERT INTO chats VALUES(?,?,?,?,?,?,?,?)",(str(uuid.uuid4()),course_id,week_id,card_id,role,text,json.dumps(proposal,ensure_ascii=False) if proposal else None,utcnow()));self.conn.commit()
    def create_quiz(self,q):
        with self._lock:self.conn.execute("INSERT INTO quizzes VALUES(?,?,?)",(q["id"],json.dumps(q,ensure_ascii=False),utcnow()));self.conn.commit();return q
    def get_quiz(self,qid):
        row=self.conn.execute("SELECT value FROM quizzes WHERE id=?",(qid,)).fetchone(); return json.loads(row["value"]) if row else None
    def save_quiz(self,q):
        with self._lock:self.conn.execute("INSERT INTO quizzes VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",(q["id"],json.dumps(q,ensure_ascii=False),utcnow()));self.conn.commit();return q
    def active_quiz(self):
        rows=self.conn.execute("SELECT value FROM quizzes ORDER BY updated_at DESC");
        for r in rows:
            q=json.loads(r["value"])
            if q.get("status")=="active":return q
        return None
    def save_asset(self,blob,name,mime):
        aid=str(uuid.uuid4()); suffix=Path(name).suffix or ".bin"; folder=self.data/"assets";folder.mkdir(exist_ok=True);path=folder/(aid+suffix);path.write_bytes(blob)
        with self._lock:self.conn.execute("INSERT INTO assets VALUES(?,?,?,?,?)",(aid,name,mime,str(path),utcnow()));self.conn.commit()
        return {"id":aid,"url":f"/api/assets/{aid}"}
    def asset(self,aid):
        r=self.conn.execute("SELECT * FROM assets WHERE id=?",(aid,)).fetchone();return dict(r) if r else None
    def add_material(self, course_id, week_id, role, title, filename, mime, asset_id, text_asset_id=None, review_path=None, slides=None):
        if role not in {"primary","supplement"}: raise ValueError("자료 역할이 올바르지 않습니다.")
        mid=str(uuid.uuid4())
        self.conn.execute("INSERT INTO materials VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(mid,course_id,week_id,role,title,filename,mime,asset_id,text_asset_id,review_path,json.dumps(slides or {},ensure_ascii=False),utcnow())); self.conn.commit(); return mid
    def materials(self, course_id, week_id=None):
        sql="SELECT * FROM materials WHERE course_id=?"+(" AND week_id=?" if week_id else "")+" ORDER BY created_at"; args=(course_id,week_id) if week_id else (course_id,)
        return [{**dict(r),"courseId":r["course_id"],"weekId":r["week_id"],"assetId":r["asset_id"],"textAssetId":r["text_asset_id"],"slides":json.loads(r["slides"] or "{}"),"textStatus":"extracted" if r["text_asset_id"] else "unreadable"} for r in self.conn.execute(sql,args)]
    def material(self, material_id):
        row=self.conn.execute("SELECT * FROM materials WHERE id=?",(material_id,)).fetchone()
        if not row: return None
        value=dict(row); value["slides"]=json.loads(value["slides"] or "{}"); return value
    def set_material_slides(self, material_id, slides):
        self.conn.execute("UPDATE materials SET slides=? WHERE id=?",(json.dumps(slides,ensure_ascii=False),material_id)); self.conn.commit()
    def add_import(self,course_dict,cards):
        cid=course_dict["id"];base=self.data/"courses"/cid;(base/"cards").mkdir(parents=True,exist_ok=True);self._atomic_json(base/"course.json",course_dict)
        for card in cards:self._atomic_json(base/"cards"/(card["id"]+".json"),card)

    def create_course(self, title, request_id=None):
        with self._lock:
            old=self._request(request_id)
            if old is not None:return old
            title=str(title or "").strip()
            if not 1<=len(title)<=120: raise ValueError("과목 이름은 1~120자로 입력해주세요.")
            if any(self._read_json(path).get("title")==title for path in self.data.joinpath("courses").glob("*/course.json")):
                raise ValueError("같은 이름의 과목이 이미 있습니다.")
            course={"id":"course_"+uuid.uuid4().hex[:16],"title":title,"weeks":[],"scopeStatus":"unconfirmed"}
            self.add_import(course,[]); result={"course":course};self._remember(request_id,result);self.conn.commit();return result

    def update_lesson(self, course_id, week_id, patch, request_id=None):
        with self._lock:
            old=self._request(request_id)
            if old is not None:return old
            path=self.data/"courses"/course_id/"course.json"
            if not path.exists(): raise KeyError("과목을 찾지 못했습니다.")
            course=self._read_json(path); lesson=next((w for w in course.get("weeks",[]) if w.get("id")==week_id),None)
            if not lesson: raise KeyError("수업을 찾지 못했습니다.")
            try: week_number=int(patch.get("weekNumber"));lesson_number=int(patch.get("lessonNumber"))
            except (TypeError,ValueError): raise ValueError("주차와 강 번호를 확인해주세요.")
            if week_number<1 or lesson_number not in (1,2): raise ValueError("주차는 1 이상, 강은 1 또는 2로 입력해주세요.")
            for other in course["weeks"]:
                if other.get("id")!=week_id and other.get("weekNumber")==week_number and other.get("lessonNumber")==lesson_number:
                    raise ValueError("같은 주차·강으로 등록된 수업이 이미 있습니다.")
            peer=next((w for w in course["weeks"] if w.get("id")!=week_id and w.get("weekNumber")==week_number and w.get("reviewGroupId")),None)
            lesson["weekNumber"],lesson["lessonNumber"]=week_number,lesson_number
            lesson["reviewGroupId"]=(peer or {}).get("reviewGroupId") or f"{course_id}-week-{week_number:02}"
            if "title" in patch:
                title=str(patch["title"] or "").strip()
                if not 1<=len(title)<=120: raise ValueError("수업 이름은 1~120자로 입력해주세요.")
                lesson["title"]=title
            course["weeks"].sort(key=lambda w:(int(w.get("weekNumber") or 10**6),int(w.get("lessonNumber") or 10**6),w.get("title","")))
            self._atomic_json(path,course);result={"course":course,"lesson":deepcopy(lesson)};self._remember(request_id,result);self.conn.commit();return result

    def assess(self, attempt: dict[str, Any]) -> dict[str, Any]:
        """Append immutable scoring evidence and replay affected address states."""
        with self._lock:
            if self.conn.execute("SELECT 1 FROM attempts WHERE id=?",(attempt["id"],)).fetchone():
                row=self.conn.execute("SELECT evaluation FROM attempts WHERE id=?",(attempt["id"],)).fetchone(); return json.loads(row["evaluation"])
            evaluation=evaluate_attempt(attempt)
            checks = [check for requirement in evaluation["requirements"] for check in requirement["checks"]]
            met = sum(check.get("status") == "met" for check in checks)
            total = len(checks)
            missing = [check.get("label") or check.get("id") for check in checks if check.get("status") == "not_met"]
            unreadable = [check.get("label") or check.get("id") for check in checks if check.get("status") == "unknown"]
            evaluation["feedback"] = {
                "questionId": attempt.get("questionId"),
                "confirmed": "확인된 근거가 있습니다." if met else "확인된 항목이 아직 없습니다.",
                "missing": " · ".join(unreadable or missing) or "없음",
                "nextAction": "판독이 어려운 부분을 확인해 주세요." if unreadable else ("한 가지 빠진 근거를 보완해 다시 설명해 보세요." if missing else "다음 문제로 넘어가도 됩니다."),
                "met": met, "total": total, "passed": evaluation["passed"], "held": evaluation["held"], "checks": checks,
            }
            valid=bool(attempt.get("independent") and attempt.get("fresh",True))
            self.conn.execute("INSERT INTO attempts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(attempt["id"],attempt.get("courseId"),attempt.get("cardId"),attempt.get("familyId"),attempt.get("observedAt",utcnow()),int(bool(attempt.get("independent"))),int(bool(attempt.get("fresh",True))),int(valid),0,json.dumps(attempt,ensure_ascii=False),json.dumps(evaluation,ensure_ascii=False),utcnow()))
            states={}
            for result in evaluation["requirements"]:
                address=self._address(result);states[address]=self._replay(address, attempt.get("courseId"))
            evaluation["states"]=states; self.conn.execute("UPDATE attempts SET evaluation=? WHERE id=?",(json.dumps(evaluation,ensure_ascii=False),attempt["id"]));self.conn.commit();return evaluation

    def invalidate_attempt(self, attempt_id: str, reason: str) -> dict[str,Any]:
        with self._lock:
            row=self.conn.execute("SELECT payload,evaluation,course_id FROM attempts WHERE id=?",(attempt_id,)).fetchone()
            if not row: raise KeyError("답안을 찾을 수 없습니다.")
            self.conn.execute("UPDATE attempts SET invalidated=1 WHERE id=?",(attempt_id,)); payload=json.loads(row["payload"]); addresses=[self._address(r) for r in json.loads(row["evaluation"])["requirements"]]
            states={a:self._replay(a,row["course_id"]) for a in addresses}
            for address in addresses: self._rebuild_recall(row["course_id"], address)
            self.event("attempt_invalidated",row["course_id"],{"attemptId":attempt_id,"reason":reason},None);self.conn.commit();return {"states":states}

    # Route-friendly name: a correction preserves the original answer and its assessment,
    # then adds an invalidation event and derives state again from valid evidence.
    def correct_attempt(self, attempt_id: str, reason: str) -> dict[str, Any]:
        return self.invalidate_attempt(attempt_id, reason)

    def _invalidate_changed_details(self, old: dict[str, Any], new: dict[str, Any], reason: str) -> None:
        """A wording-only title change keeps evidence; changed factual details do not."""
        before={d.get("id"): d for d in old.get("details", [])}; after={d.get("id"): d for d in new.get("details", [])}
        changed={key for key in set(before)|set(after) if before.get(key) != after.get(key)}
        if old.get("core") != new.get("core"): changed |= set(before) | set(after)
        if not changed: return
        rows=self.conn.execute("SELECT id,payload,evaluation FROM attempts WHERE card_id=? AND invalidated=0",(old.get("id"),)).fetchall()
        addresses=set()
        for row in rows:
            evidence=json.loads(row["evaluation"])
            affected=[self._address(r) for r in evidence.get("requirements",[]) if r.get("detailId") in changed]
            if affected:
                for address in affected:
                    self.conn.execute("INSERT OR IGNORE INTO attempt_requirement_invalidations VALUES(?,?,?,?)",(row["id"],address,reason,utcnow())); addresses.add(address)
        for address in addresses:
            self._rebuild_recall(old.get("courseId"), address)
            self._replay(address, old.get("courseId"))
        self.event("evidence_invalidated",old.get("courseId"),{"cardId":old.get("id"),"details":sorted(changed),"reason":reason},None)

    def record_recall(self, course_id: str, address: str, rating: str, source_attempt_id: str, observed_at: str | None = None) -> dict[str, Any]:
        """Apply FSRS only to an observed independent recall, never to reading or hints."""
        if Scheduler is None: raise RuntimeError("FSRS 라이브러리를 읽을 수 없습니다.")
        if rating not in {"Again", "Hard", "Good", "Easy"}: raise ValueError("회상 평점이 올바르지 않습니다.")
        row=self.conn.execute("SELECT independent,fresh,invalidated,evaluation FROM attempts WHERE id=?",(source_attempt_id,)).fetchone()
        if not row or not row["independent"] or not row["fresh"] or row["invalidated"]: raise ValueError("독립 회상 근거가 없어 복습 일정을 바꾸지 않습니다.")
        if address not in {self._address(r) for r in json.loads(row["evaluation"]).get("requirements", [])}: raise ValueError("이 답안에 없는 회상 항목입니다.")
        when=datetime.fromisoformat(observed_at) if observed_at else datetime.now(timezone.utc)
        if when.tzinfo is None: raise ValueError("회상 시각은 UTC 시간대여야 합니다.")
        key=f"{course_id}::{address}"
        if self.conn.execute("SELECT 1 FROM recall_events WHERE address=? AND source_attempt_id=?",(key,source_attempt_id)).fetchone():
            saved=self.conn.execute("SELECT card_json FROM recall_cards WHERE address=?",(key,)).fetchone()
            return {"nextReviewAt":json.loads(saved["card_json"])["due"] if saved else None,"library":"fsrs 6.3.2"}
        saved=self.conn.execute("SELECT card_json FROM recall_cards WHERE address=?",(key,)).fetchone()
        card=Card.from_dict(json.loads(saved["card_json"])) if saved else Card()
        card, _log=Scheduler(enable_fuzzing=False).review_card(card, getattr(Rating,rating), review_datetime=when.astimezone(timezone.utc))
        self.conn.execute("INSERT INTO recall_cards VALUES(?,?,?) ON CONFLICT(address) DO UPDATE SET card_json=excluded.card_json,updated_at=excluded.updated_at",(key,json.dumps(card.to_dict()),utcnow()))
        self.conn.execute("INSERT INTO recall_events VALUES(?,?,?,?,?,?)",(str(uuid.uuid4()),key,rating,when.isoformat(),source_attempt_id,utcnow())); self.conn.commit()
        return {"nextReviewAt":card.due.isoformat(),"library":"fsrs 6.3.2"}

    def _rebuild_recall(self, course_id: str | None, address: str) -> None:
        """Derived FSRS state is disposable; raw recall events remain the audit record."""
        if Scheduler is None: return
        key=f"{course_id or ''}::{address}"
        self.conn.execute("DELETE FROM recall_cards WHERE address=?",(key,))
        card = None
        events=self.conn.execute("SELECT * FROM recall_events WHERE address=? ORDER BY observed_at,id",(key,)).fetchall()
        for event in events:
            attempt=self.conn.execute("SELECT invalidated FROM attempts WHERE id=?",(event["source_attempt_id"],)).fetchone()
            blocked=self.conn.execute("SELECT 1 FROM attempt_requirement_invalidations WHERE attempt_id=? AND address=?",(event["source_attempt_id"],address)).fetchone()
            if not attempt or attempt["invalidated"] or blocked: continue
            card = Card() if card is None else card
            at=datetime.fromisoformat(event["observed_at"]).astimezone(timezone.utc)
            card,_=Scheduler(enable_fuzzing=False).review_card(card,getattr(Rating,event["rating"]),review_datetime=at)
        if card:
            self.conn.execute("INSERT INTO recall_cards VALUES(?,?,?)",(key,json.dumps(card.to_dict()),utcnow()))

    @staticmethod
    def _address(result): return "|".join(str(result.get(k) or "") for k in ("detailId","behavior","conditions"))
    def _replay(self,address,course_id=None):
        evidence=[]
        for row in self.conn.execute("SELECT * FROM attempts WHERE invalidated=0 AND course_id IS ? ORDER BY observed_at",(course_id,)):
            evaluation=json.loads(row["evaluation"])
            for result in evaluation["requirements"]:
                blocked=self.conn.execute("SELECT 1 FROM attempt_requirement_invalidations WHERE attempt_id=? AND address=?",(row["id"],address)).fetchone()
                if self._address(result)==address and not result["held"] and not blocked:
                    evidence.append({"id":row["id"],"cardId":row["card_id"],"passed":bool(result["passed"]),"independent":bool(row["independent"]),"fresh":bool(row["fresh"]),"familyId":row["family_id"],"level":result.get("level"),"behavior":result.get("behavior"),"observedAt":row["observed_at"]})
        valid=[e for e in evidence if e["independent"] and e["fresh"]][-3:]
        successes=[e for e in valid if e["passed"]]
        def level_number(value):
            try: return int(value)
            except (TypeError, ValueError): return {"기본 객관식 풀기":1,"도움 없이 떠올리기":2,"이유까지 설명하기":3,"기본 문제 혼자 풀기":4,"변형·복합 문제 풀기":5}.get(str(value),0)
        level=max((level_number(e["level"]) for e in successes),default=0)
        families={e["familyId"] for e in successes if e["familyId"]}
        recall_delayed = False
        if len(successes) >= 2 and str(successes[-1].get("behavior", "")).lower() in {"recall", "회상"}:
            first = datetime.fromisoformat(successes[-2]["observedAt"])
            last = datetime.fromisoformat(successes[-1]["observedAt"])
            recall_delayed = last - first >= timedelta(hours=24)
        # Level 4-5 always need another genuine item family.  For recall, two same-family
        # successes only count when the second was delayed by the stated 24 hours.
        family_requirement = (level < 4 or len(families)>=2) and (str(address.split("|")[1]).lower() not in {"recall", "회상"} or len(families)>=2 or recall_delayed)
        confirmed=len(successes)>=2 and family_requirement
        consecutive=0
        for item in reversed(valid):
            if item["passed"]: break
            consecutive += 1
        theta=0.0
        # One address update per attempt; assisted, repeated and unreadable work has no theta effect.
        for item in evidence:
            if not (item["independent"] and item["fresh"]): continue
            source=self.conn.execute("SELECT evaluation,payload FROM attempts WHERE id=?",(item["id"],)).fetchone()
            result=next(r for r in json.loads(source["evaluation"])["requirements"] if self._address(r)==address)
            difficulty=next((r.get("difficulty",3) for r in json.loads(source["payload"])["requirements"] if r.get("id")==result["id"]),3)
            b={1:-2,2:-1,3:0,4:1,5:2}.get(int(difficulty),0)
            q=float(Fraction(result["raw"])); qhat=1/(1+math.exp(-(theta-b))); theta=max(-4,min(4,theta+0.5*(q-qhat)))
        last_failure=next((item for item in reversed(valid) if not item["passed"]),None); reviewed=False
        if last_failure:
            for event in self.conn.execute("SELECT kind,payload FROM events WHERE course_id=? AND created_at>?",(course_id,last_failure["observedAt"])):
                payload=json.loads(event["payload"])
                if payload.get("cardId")==last_failure["cardId"] and (event["kind"] == "card_explained" or (event["kind"] == "exposure" and payload.get("kind") in {"read","reopen","explanation"})): reviewed=True; break
        state={"label":"확인" if confirmed else ("학습 중" if evidence else "미평가"),"confirmed":confirmed,"level":level if confirmed else 0,"recent":valid,"successes":len(successes),"families":len(families),"observed":bool(evidence),"reconfirm":bool(valid and not valid[-1]["passed"]),"consecutiveFailures":consecutive,"correctionNeeded":consecutive>=2,"reviewedSinceFailure":reviewed,"retryEligible":not consecutive or reviewed,"theta":theta}
        db_address=f"{course_id or ''}::{address}"
        self.conn.execute("INSERT INTO learner_states VALUES(?,?,?) ON CONFLICT(address) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",(db_address,json.dumps(state,ensure_ascii=False),utcnow()))
        return state
