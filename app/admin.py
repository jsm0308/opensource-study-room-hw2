"""Read-only, workspace-scoped inspection of the actual study engine."""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from assessment import PASS
from ai_bridge import BASE

APP = Path(__file__).resolve().parent
ROOT = APP.parent

CAPABILITIES = [
    {"name":"자료 등록", "status":"implemented", "detail":"과목·주차 선택, 주자료·보조자료 최대 8개와 메모 동시 등록. 원본·추출 텍스트·상세 해설을 따로 보관합니다.", "owner":"server.py / LearningService.import_text"},
    {"name":"폴더 자동 수집", "status":"missing", "detail":"폴더에 놓은 파일을 자동 감시·수집하지 않습니다. 원문 변경 감지·중복 자동 병합은 아직 없습니다.", "owner":"미구현"},
    {"name":"읽기·질문·설명 보완", "status":"implemented", "detail":"선택 문맥과 카드 버전을 전달하고, 승인한 보완만 카드에 저장합니다. 수정·되돌리기를 기록합니다.", "owner":"web/app.js / LearningService.chat / StudyStore.edit_card"},
    {"name":"오늘 공부 퀴즈", "status":"implemented", "detail":"선택한 한 수업의 모든 카드를 한 세트로 모읍니다. 연습·점검 선택 없이 카드당 2문항을 한 세트로 펼쳐 풉니다. 진행 중 세트는 유지합니다.", "owner":"LearningService.daily_scope / start_quiz"},
    {"name":"오답을 반영한 다음 출제", "status":"partial", "detail":"저장된 오답 체크포인트와 미확인 요구사항을 다음 출제에 전달하고 선택 근거를 남깁니다. 기존 문항을 자동 수정하는 문항은행·난도 보정은 없습니다.", "owner":"LearningService._selection_trace / start_quiz"},
    {"name":"사진·텍스트 평가", "status":"implemented", "detail":"AI가 고정 체크포인트별 근거를 판독하고 Python이 원비율·필수 조건으로 판정합니다. 불명확한 판독은 보류합니다.", "owner":"LearningService._grade / assessment.py"},
    {"name":"학습 상태·평가 정정", "status":"implemented", "detail":"독립성·반복 노출을 구별하고 원답과 이전 판정을 보존합니다. 정정 후 관련 상태와 회상 일정을 재계산합니다.", "owner":"StudyStore.assess / _replay / correct_attempt"},
    {"name":"학습 이력·오답 정리", "status":"implemented", "detail":"답안별 고정 문항·AI 피드백·판정과 개인 정리 수정 이력을 분리해 보관하고 읽기 전용으로 조회합니다.", "owner":"StudyStore.attempts / attempt_feedback / attempt_notes"},
    {"name":"관리자 검사·합성 이력", "status":"implemented", "detail":"실제 DB의 원답·루브릭·선택 근거·이벤트·내부 상태를 읽기 전용으로 표시합니다. 요청된 합성 이력은 같은 학습실에 표시하되 synthetic 표시로 구별하며 실제 독립 실력 근거에는 넣지 않습니다.", "owner":"admin.py / demo.py / web/admin-v2.js"},
    {"name":"복습 일정", "status":"implemented", "detail":"당일 복습은 한 수업, 주차 복습은 두 수업으로 묶습니다. 두 수업 전체 평가가 끝난 다음 날 오전 9시에 주차 복습을 제안하고 오답과 피드백을 출제에 반영합니다.", "owner":"LearningService.complete_reading / calendar / StudyStore.record_recall"},
    {"name":"교정 피드백", "status":"partial", "detail":"오답 근거·다음 행동·해당 설명 복귀와 재시도를 지원합니다. 선수 개념 그래프를 통한 자동 결손 진단·교정 예제 최적화는 없습니다.", "owner":"LearningService._grade / submit"},
    {"name":"출제 검증", "status":"partial", "detail":"같은 모델로 조건·해답을 재검토합니다. 수학 계산·코드 실행 검증기와 전문가 수준의 독립 검증은 아직 연결하지 않았습니다.", "owner":"LearningService.start_quiz"},
    {"name":"학습 범위·평가 설계 편집", "status":"partial", "detail":"자료에서 요구사항을 만들지만 생성 내용은 원문 확인이 필요합니다. 시험 범위 확정·requirement 편집·대안 풀이 루브릭 버전 관리 UI는 아직 없습니다.", "owner":"LearningService.import_text"},
    {"name":"자료 자동 전사·OCR", "status":"missing", "detail":"음성·영상 전사, 스캔 PDF·사진 OCR, PPTX 분석, 수식 편집기는 미구현입니다. PDF 페이지 이미지와 수식 표시는 지원합니다.", "owner":"미구현"},
    {"name":"외부 학습 시스템 동기화", "status":"missing", "detail":"외부 자료 저장소와 학습 기록의 자동 양방향 동기화는 없습니다.", "owner":"미구현"},
    {"name":"운영·개인화", "status":"missing", "detail":"자동 백업·다중 사용자 인증·모바일 배포·알림·시간 예산·개인별 추정치 보정·운영 설정 편집 UI는 없습니다. 현재는 한 PC의 loopback 앱입니다.", "owner":"미구현"},
]

CODE_FILES = {
    "web/management.js": (APP / "web/management.js", "기록·캘린더·관리 탭 연결"),
    "web/job-status.js": (APP / "web/job-status.js", "작업 단계·경과·예상 시간 표시"),
    "server.py": (APP / "server.py", "HTTP 요청·자료 추출·live/demo 라우팅"),
    "learning.py": (APP / "learning.py", "질문·오늘 범위·출제·힌트·평가·정정"),
    "assessment.py": (APP / "assessment.py", "원비율과 필수 조건의 결정적 판정"),
    "core.py": (APP / "core.py", "JSON·SQLite 저장, 증거 재생, FSRS"),
    "ai_bridge.py": (APP / "ai_bridge.py", "Codex App Server 연결과 공통 AI 지침"),
    "demo.py": (APP / "demo.py", "분리된 합성 학습 기록 생성"),
    "admin.py": (APP / "admin.py", "관리자 검사 데이터와 기능 상태"),
    "web/app.js": (APP / "web/app.js", "학습자 화면 동작"),
    "web/review-document.js": (APP / "web/review-document.js", "상세 원문·수식·슬라이드와 읽는 위치"),
    "web/materials.js": (APP / "web/materials.js", "자료 목록과 과목·주차별 다중 파일 등록"),
    "web/quiz-sheet.js": (APP / "web/quiz-sheet.js", "모든 문제와 문항별 초안·사진·피드백"),
    "web/learning-records.js": (APP / "web/learning-records.js", "과거 풀이·오답 정리 읽기와 기록"),
    "web/study-calendar.js": (APP / "web/study-calendar.js", "읽기 완료·퀴즈 완료·복습 일정 달력"),
    "web/workspace-shell.js": (APP / "web/workspace-shell.js", "학습 화면의 공통 작업 공간 전환"),
    "web/workspace.css": (APP / "web/workspace.css", "공통 배치와 반응형 스타일"),
    "web/index.html": (APP / "web/index.html", "학습자 화면 구조"),
    "web/styles.css": (APP / "web/styles.css", "기본 화면 스타일"),
    "web/admin-v2.js": (APP / "web/admin-v2.js", "관리자 필터·통계·원본 데이터 보기"),
    "config/assessment.v1.json": (ROOT / "config/assessment.v1.json", "판정 규칙 명세; 현재 실행 상수는 Python 코드에 정의"),
    "launch.ps1": (ROOT / "launch.ps1", "실행 환경 확인과 서버 시작"),
}


def code_source(name):
    if name not in CODE_FILES:
        raise PermissionError("공개된 구현 파일만 열 수 있습니다.")
    path, purpose = CODE_FILES[name]
    return {"name":name, "path":str(path), "purpose":purpose, "source":path.read_text(encoding="utf-8-sig")}


def _table(store, table, json_columns=()):
    # Only internal fixed table names are ever passed here.
    rows=[]
    for row in store.conn.execute(f"SELECT * FROM {table}"):
        entry=dict(row)
        for key in json_columns:
            if entry.get(key) is not None:
                entry[key]=json.loads(entry[key])
        rows.append(entry)
    return rows


def snapshot(store, service, workspace_kind="live"):
    with store._lock:
        base=store.bootstrap()
        attempts=_table(store,"attempts",("payload","evaluation"))
        quizzes=[r["value"] for r in _table(store,"quizzes",("value",))]
        events=_table(store,"events",("payload",))
        states=_table(store,"learner_states",("value",))
        recall_events=_table(store,"recall_events")
        recall_cards=_table(store,"recall_cards",("card_json",))
        revisions=_table(store,"card_revisions",("old_json","new_json"))
        invalidations=_table(store,"attempt_requirement_invalidations")
        chats=_table(store,"chats",("proposal",))
        assets=_table(store,"assets")
        materials=_table(store,"materials",("slides",))
        notes=_table(store,"attempt_notes")
        note_revisions=_table(store,"attempt_note_revisions")
        feedback=_table(store,"attempt_feedback",("feedback","answer_snapshot"))
        review_plans=_table(store,"review_plans")
    attempts.sort(key=lambda x:x["observed_at"])
    quizzes.sort(key=lambda x:x.get("createdAt",""),reverse=True)
    events.sort(key=lambda x:x["created_at"])
    cards=base["cards"]
    daily=[]
    for course in base["courses"]:
        d=service.daily_scope(course["id"])
        daily.append({"courseId":course["id"],"title":course["title"],**d})
    dashboard={
        "courses":len(base["courses"]), "weeks":sum(len(c["weeks"]) for c in base["courses"]),
        "cards":len(cards), "requirements":sum(c["state"]["total"] for c in cards),
        "confirmedRequirements":sum(c["state"]["confirmed"] for c in cards),
        "cardStates":dict(Counter(c["state"]["label"] for c in cards)),
        "attempts":len(attempts), "independentAttempts":sum(bool(a["independent"] and a["fresh"] and not a["invalidated"]) for a in attempts),
        "incorrectAttempts":sum(a["evaluation"].get("passed") is False and not a["invalidated"] for a in attempts),
        "heldAttempts":sum(bool(a["evaluation"].get("held")) and not a["invalidated"] for a in attempts),
        "invalidatedAttempts":sum(bool(a["invalidated"]) for a in attempts),
        "quizzes":len(quizzes), "daily":daily,
        "dueRecall":sum(datetime.fromisoformat(r["card_json"]["due"])<=datetime.now(timezone.utc) for r in recall_cards),
    }
    loops=[]
    for card in cards:
        evidence=[a for a in attempts if a["card_id"]==card["id"] and a["course_id"]==card["courseId"]]
        if not evidence:
            continue
        selections=[{"quizId":q["id"],"createdAt":q.get("createdAt"),**trace} for q in quizzes for trace in q.get("selectionTrace",[]) if trace.get("cardId")==card["id"]]
        loops.append({"cardId":card["id"],"courseId":card["courseId"],"weekId":card["weekId"],"title":card["title"],"state":card["state"],"attemptIds":[a["id"] for a in evidence],"selections":selections,"eventIds":[e["id"] for e in events if e["payload"].get("cardId")==card["id"]]})
    config=json.loads((ROOT/"config/assessment.v1.json").read_text(encoding="utf-8"))
    return {
        "generatedAt":datetime.now(timezone.utc).isoformat(),
        "courses":base["courses"],"cards":cards,"quizzes":quizzes,"attempts":attempts,"events":events,
        "learnerStates":states,"recallEvents":recall_events,"recallCards":recall_cards,
        "cardRevisions":revisions,"invalidations":invalidations,"attemptNotes":notes,"attemptNoteRevisions":note_revisions,"attemptFeedback":feedback,"reviewPlans":review_plans,"chats":chats,"assets":assets,"materials":materials,
        "config":config,"dashboard":dashboard,"feedbackLoops":loops,
        "system":{
            "workspaceKind":workspace_kind,"synthetic":workspace_kind=="demo",
            "paths":{"workspace":str(store.root.resolve()),"app":str(APP),"web":str(APP/"web"),"database":str(store.db_path.resolve()),"courses":str((store.data/"courses").resolve()),"assets":str((store.data/"assets").resolve()),"launcher":str(ROOT/"launch.ps1")},
            "capabilities":CAPABILITIES,"aiBaseInstructions":BASE,
            "effectiveRules":{"passRaw":str(PASS),"recentWindow":3,"requiredSuccesses":2,"repeatHours":24,"thetaEta":0.5,"thetaRange":[-4,4],"note":"실행 상수는 assessment.py·core.py에 있습니다. 설정 파일은 현재 규칙 명세이며 수정만으로 실행 값이 바뀌지 않습니다."},
            "files":[{"name":name,"path":str(p),"purpose":purpose} for name,(p,purpose) in CODE_FILES.items()],
            "aiStatus":dict(service.ai.status),
            "demoNotice":"저장된 학습 기록과 평가 근거입니다.",
        },
    }

def graph(store, course_id, week_number=None):
    """Read-only provenance graph. Every edge has a stored identifier as evidence."""
    base=store.bootstrap()
    course=next((c for c in base["courses"] if c["id"]==course_id),None)
    if not course: raise KeyError("과목을 찾을 수 없습니다.")
    weeks=[w for w in course.get("weeks",[]) if week_number is None or w.get("weekNumber")==week_number]
    week_ids={w["id"] for w in weeks}; week_by_id={w["id"]:w for w in weeks}
    cards=[c for c in base["cards"] if c.get("courseId")==course_id and c.get("weekId") in week_ids]
    card_by_id={c["id"]:c for c in cards}; card_ids=set(card_by_id)
    nodes={}; edges=[]
    def node(identifier, kind, label, **details):
        nodes.setdefault(identifier,{"id":identifier,"type":kind,"label":label,"courseId":course_id,
          "weekId":details.pop("weekId",None),"weekNumber":details.pop("weekNumber",None),
          "lessonId":details.pop("lessonId",None),"synthetic":bool(details.pop("synthetic",False)),"details":details})
    def edge(source,target,kind,evidence):
        if source in nodes and target in nodes:
            edges.append({"id":f"{kind}:{source}:{target}","source":source,"target":target,"kind":kind,"evidence":evidence})
    def place(card):
        week=week_by_id[card["weekId"]]
        return {"weekId":card["weekId"],"weekNumber":week.get("weekNumber"),"lessonId":card["weekId"]}
    for card in cards:
        source=card.get("source") or {}; ref=source.get("assetId") or source.get("path")
        source_id=f"source:{ref}" if ref else None
        if source_id:
            label=source.get("label") or Path(str(source.get("path") or "연결 원문")).name or "연결 원문"
            node(source_id,"source",label,**place(card),materialIds=source.get("materialIds",[]),path=source.get("path"),assetId=source.get("assetId"),page=source.get("page"),locator=source.get("locator"),title=source.get("title"),summary=source.get("summary"))
        cid=f"card:{card['id']}"
        node(cid,"card",card.get("title","학습 카드"),**place(card),cardId=card["id"],version=card.get("version"),goal=card.get("goal"),core=card.get("core"),source=source)
        if source_id: edge(source_id,cid,"source_to_card",{"sourceRef":ref,"locator":source.get("locator")})
    quizzes=[r["value"] for r in _table(store,"quizzes",("value",)) if r["value"].get("courseId")==course_id]
    questions={}; question_candidates={}; quiz_question_ids={}; answer_question_nodes={}; attempts={}; attempt_rows={}
    for quiz in quizzes:
        owned=[]
        for question in quiz.get("questions",[]):
            if question.get("cardId") not in card_ids: continue
            card=card_by_id[question["cardId"]]; qid=f"question:{quiz['id']}:{question['id']}"; questions[(quiz["id"],question["id"])]=qid; question_candidates.setdefault(question["id"],[]).append(qid); owned.append(qid)
            answer=quiz.get("answers",{}).get(question["id"],{})
            if answer.get("attemptId"): answer_question_nodes[answer["attemptId"]]=qid
            trace=question.get("selectionTrace") or next((x for x in quiz.get("selectionTrace",[]) if x.get("cardId")==card["id"]),{})
            node(qid,"question",question.get("title") or card.get("title","문항"),**place(card),quizId=quiz["id"],questionId=question["id"],version=question.get("version"),prompt=question.get("prompt"),answer=question.get("answer"),checks=question.get("checks",[]),cardId=card["id"],parentAttemptIds=list(trace.get("parentAttemptIds") or trace.get("mistakeAttemptIds") or []),previousQuestionIds=list(trace.get("previousQuestionIds") or []),selectionTrace=trace,sourceVersion=trace.get("sourceVersion"),rubricVersion=trace.get("rubricVersion"))
            edge(f"card:{card['id']}",qid,"card_to_question",{"quizId":quiz["id"],"contentSnapshot":bool(question.get("contentSnapshot")),"sourceVersion":trace.get("sourceVersion")})
        quiz_question_ids[quiz["id"]]=owned
    feedback_by_attempt={r["attempt_id"]:r for r in _table(store,"attempt_feedback",("feedback","answer_snapshot"))}
    for row in _table(store,"attempts",("payload","evaluation")):
        if row.get("course_id")!=course_id or row.get("card_id") not in card_ids: continue
        payload=row["payload"]; card=card_by_id[row["card_id"]]
        candidates=question_candidates.get(payload.get("questionId"),[])
        qnode=answer_question_nodes.get(row["id"]) or (candidates[0] if len(candidates)==1 else None)
        link_diagnostic=None if qnode else ("문항 ID가 저장된 테스트에서 하나로 확인되지 않아 연결하지 않았습니다." if candidates else "고정 문항을 찾지 못한 이전 기록입니다.")
        aid=f"attempt:{row['id']}"; attempts[row["id"]]=aid; attempt_rows[row["id"]]=row
        feedback=(feedback_by_attempt.get(row["id"],{}).get("feedback") or row.get("evaluation") or {})
        synthetic=bool(payload.get("synthetic") or feedback.get("synthetic"))
        stamp=row.get("observed_at") or row.get("created_at") or ""
        label=f"{'정답' if feedback.get('passed') is True else ('보류' if feedback.get('held') else '오답')} · {stamp[:10] or '기록'}"
        node(aid,"attempt",label,**place(card),attemptId=row["id"],questionId=payload.get("questionId"),text=payload.get("text",""),passed=feedback.get("passed"),held=feedback.get("held"),invalidated=bool(row.get("invalidated")),independent=bool(row.get("independent")),fresh=bool(row.get("fresh")),familyId=row.get("family_id"),observedAt=stamp,quizId=nodes[qnode]["details"].get("quizId") if qnode else None,questionLinkDiagnostic=link_diagnostic,synthetic=synthetic)
        if qnode: edge(qnode,aid,"question_to_attempt",{"attemptId":row["id"],"observedAt":stamp})
        missing=feedback.get("missing") or feedback.get("missingLabels") or ""
        flabel=("피드백 · "+str(missing)[:52]) if missing else ("피드백 · 통과" if feedback.get("passed") else "피드백")
        fid=f"feedback:{row['id']}"
        node(fid,"feedback",flabel,**place(card),attemptId=row["id"],missing=missing,nextAction=feedback.get("nextAction",""),passed=feedback.get("passed"),held=feedback.get("held"),feedback=feedback,synthetic=synthetic)
        edge(aid,fid,"attempt_to_feedback",{"attemptId":row["id"],"feedbackSnapshot":row["id"] in feedback_by_attempt})
    # Existing stored plans are shown as plans, never as a fabricated question.
    for plan in _table(store,"review_plans"):
        if plan.get("course_id")!=course_id or plan.get("card_id") not in card_ids or plan.get("status")!="scheduled": continue
        card=card_by_id[plan["card_id"]]; pid=f"planned:{plan['id']}"
        node(pid,"planned","예정 복습",**place(card),planId=plan["id"],cardId=plan["card_id"],address=plan.get("address"),dueAt=plan.get("due_at"),reason=plan.get("reason"),quizId=plan.get("quiz_id"),status="scheduled",hasQuiz=False)
        edge(f"card:{card['id']}",pid,"card_to_planned",{"reviewPlanId":plan["id"],"dueAt":plan.get("due_at")})
    for quiz in quizzes:
        schedule=quiz.get("reviewSchedule") or {}
        if schedule and quiz.get("status")!="finished":
            owned=[qid for qid in quiz_question_ids.get(quiz["id"],[]) if qid in nodes]
            if owned:
                pid=f"planned:schedule:{quiz['id']}"; first=nodes[owned[0]]
                node(pid,"planned","예정 복습",weekId=first["weekId"],weekNumber=first["weekNumber"],lessonId=first["lessonId"],quizId=quiz["id"],schedule=schedule,status="scheduled",hasQuiz=False)
                for qid in owned: edge(qid,pid,"question_to_planned",{"reviewSchedule":schedule,"quizId":quiz["id"]})
    # A causal feedback edge needs an exact parent ID *and* must predate the target
    # quiz.  Synthetic seed traces can name an answer from the same quiz; preserve
    # that stored reference as a diagnostic but do not draw a false causal cycle.
    trace_diagnostic_count=0
    def parse_stamp(value):
        try: return datetime.fromisoformat(str(value).replace("Z","+00:00"))
        except (TypeError,ValueError): return None
    for quiz in quizzes:
        target_created=parse_stamp(quiz.get("createdAt"))
        for question in quiz.get("questions",[]):
            qnode=questions.get((quiz["id"],question.get("id")))
            if not qnode: continue
            trace=question.get("selectionTrace") or next((x for x in quiz.get("selectionTrace",[]) if x.get("cardId")==question.get("cardId")),{})
            diagnostics=nodes[qnode]["details"].setdefault("traceDiagnostics",[])
            for parent in trace.get("parentAttemptIds") or trace.get("mistakeAttemptIds") or trace.get("evidenceAttemptIds") or []:
                if parent not in attempts:
                    diagnostics.append({"parentAttemptId":parent,"reason":"저장된 답안 기록을 찾지 못해 인과 연결을 만들지 않았습니다."}); trace_diagnostic_count += 1; continue
                attempt_node=nodes[attempts[parent]]; parent_stamp=parse_stamp(attempt_node["details"].get("observedAt"))
                if attempt_node["details"].get("quizId")==quiz["id"] or not target_created or not parent_stamp or parent_stamp>=target_created:
                    diagnostics.append({"parentAttemptId":parent,"reason":"대상 문항보다 앞선 답안인지 확인되지 않아 인과 연결을 만들지 않았습니다."}); trace_diagnostic_count += 1; continue
                edge(f"feedback:{parent}",qnode,"feedback_to_question",{"parentAttemptId":parent,"selectionTrace":True,"previousQuestionIds":trace.get("previousQuestionIds",[])})
    events=store.history(course_id).get("events",[])
    read_weeks={e.get("weekId") for e in events if e.get("type")=="reading_completed"}
    lesson_done={e.get("weekId") for e in events if e.get("type")=="lesson_completed"}
    grouped={}
    for week in weeks:
        own=[c for c in cards if c["weekId"]==week["id"]]; own_ids={c["id"] for c in own}; own_attempt=[r for r in attempt_rows.values() if r.get("card_id") in own_ids]
        real_attempt=[r for r in own_attempt if not bool(r["payload"].get("synthetic"))]
        total=sum(c.get("state",{}).get("total",len([x for x in c.get("requirements",[]) if x.get("required",True)])) for c in own)
        confirmed=0
        for card in own:
            for req in card.get("requirements",[]):
                address=store._address(req)
                state=card.get("requirementStates",{}).get(req.get("id"),{})
                # State must be confirmed and backed by a real immutable attempt.
                if state.get("confirmed") and any(r.get("card_id")==card["id"] and not r.get("invalidated") and any(store._address(x)==address and x.get("passed") for x in r.get("evaluation",{}).get("requirements",[])) for r in real_attempt): confirmed+=1
        # Test/answer totals are recomputed once for the whole teaching week below.
        # A weekly set spans two lessons and must never be summed once per lesson.
        tests={"completed":0,"active":0,"passed":0,"wrong":0,"held":0,"invalidated":0}
        row={"weekNumber":week.get("weekNumber"),"lessonIds":[week["id"]],"lessonCount":1,"cardCount":len(own),"readingCompletedLessons":int(week["id"] in read_weeks),"evaluatedLessons":int(week["id"] in lesson_done),"confirmedRequirements":confirmed,"totalRequirements":total,"syntheticAttempts":len(own_attempt)-len(real_attempt),"realAttempts":len(real_attempt),"tests":tests}
        key=week.get("weekNumber"); target=grouped.setdefault(key,{**row,"lessonIds":[],"lessonCount":0,"cardCount":0,"readingCompletedLessons":0,"evaluatedLessons":0,"confirmedRequirements":0,"totalRequirements":0,"syntheticAttempts":0,"realAttempts":0,"tests":{"completed":0,"active":0,"passed":0,"wrong":0,"held":0,"invalidated":0}})
        target["lessonIds"].append(week["id"]); target["lessonCount"]+=1
        for name in ("cardCount","readingCompletedLessons","evaluatedLessons","confirmedRequirements","totalRequirements","syntheticAttempts","realAttempts"): target[name]+=row[name]
        for name,value in tests.items(): target["tests"][name]+=value
    # A quiz that covers both lessons belongs to this teaching week once.  Outcome
    # counts come from immutable attempts, not duplicated answer snapshots.
    for row in grouped.values():
        grouped_card_ids={card["id"] for card in cards if card.get("weekId") in set(row["lessonIds"])}
        week_quizzes={quiz["id"]:quiz for quiz in quizzes if any(question.get("cardId") in grouped_card_ids for question in quiz.get("questions",[]))}
        outcomes={"completed":sum(quiz.get("status")=="finished" for quiz in week_quizzes.values()),"active":sum(quiz.get("status")=="active" for quiz in week_quizzes.values()),"passed":0,"wrong":0,"held":0,"invalidated":0}
        for attempt in attempt_rows.values():
            if attempt.get("card_id") not in grouped_card_ids: continue
            if attempt.get("invalidated"):
                outcomes["invalidated"]+=1; continue
            feedback=feedback_by_attempt.get(attempt["id"],{}).get("feedback") or attempt.get("evaluation") or {}
            if feedback.get("held"): outcomes["held"]+=1
            elif feedback.get("passed") is True: outcomes["passed"]+=1
            elif feedback.get("passed") is False: outcomes["wrong"]+=1
        row["tests"]=outcomes
        row["masteryPercent"]=round(100*row["confirmedRequirements"]/row["totalRequirements"]) if row["totalRequirements"] else 0
        row["completionPercent"]=round(100*(row["readingCompletedLessons"]+row["evaluatedLessons"])/(2*row["lessonCount"])) if row["lessonCount"] else 0
    return {"nodes":list(nodes.values()),"edges":edges,"progress":{"weeks":list(grouped.values())},"diagnostics":{"skippedCausalLinks":trace_diagnostic_count,"meaning":"저장된 출제 근거는 보존하되, 시간상 앞선 답안으로 확인되지 않은 연결은 인과선으로 그리지 않습니다."},"legend":{"completionPercent":"수업별 읽기 완료와 모든 카드가 평가 완료된 수업 완료를 같은 비중으로 계산합니다.","masteryPercent":"합성 기록은 숙련 근거에서 제외합니다. 실제 비무효화 평가의 근거가 있는 confirmed requirement만 셉니다."}}

def rules():
    return {"sections":[
        {"id":"generation","title":"자료에서 문항까지","summary":"등록 원문과 상세 복습 문서를 근거로 카드와 고정 문항 버전을 만듭니다.","steps":["슬라이드의 학습 명제와 예제·수식을 빠뜨리지 않고 원문·보조자료·페이지 출처를 별도 보존","교수님 추가 발화는 파일·시각 근거가 있을 때만 따로 표시하고, 원문에 없는 추론과 구분","카드별 원문 연결과 요구사항을 고정하고, 새 문항에는 이전 오답·원문 버전·루브릭 버전의 출제 선택 근거를 보존"],"criteria":[{"label":"문항 범위","value":"카드당 2개","meaning":"회수와 적용을 서로 다른 문제유형으로 확인합니다."},{"label":"출제 실패","value":"저장 안 함","meaning":"범위 누락·중복 문제 유형·검증 실패면 새 세트를 만들지 않습니다."}],"sourceFiles":["app/learning.py","app/core.py"]},
        {"id":"assessment","title":"평가와 보류","summary":"고정 체크포인트를 채점한 뒤 Python 규칙으로 판정합니다.","steps":["각 체크포인트를 충족·미충족·판단 불가로 기록","필수 조건과 원비율을 함께 확인","판단 불가는 통과로 바꾸지 않고 보류"],"criteria":[{"label":"통과 비율","value":"80% 이상","meaning":"필수 조건도 모두 충족해야 합니다."},{"label":"보류","value":"판단 불가","meaning":"근거가 부족하면 숙련도로 올리지 않습니다."}],"sourceFiles":["app/assessment.py","app/core.py"]},
        {"id":"evidence","title":"숙련도와 재확인","summary":"독립적이고 새 조건의 근거를 누적해 확인 상태를 올립니다.","steps":["제출 당시 답안·평가 고정 기록으로 보존","같은 문제유형·출제조건 묶음의 반복은 독립 근거로 중복 계산하지 않음","정정·카드 수정은 관련 근거만 무효화하고 다시 계산"],"criteria":[{"label":"최근 근거","value":"3개 중 2개","meaning":"서로 다른 문제 유형의 성공이 필요합니다."},{"label":"반복 노출","value":"24시간","meaning":"가까운 반복은 fresh 근거가 아닐 수 있습니다."}],"sourceFiles":["app/core.py","app/assessment.py"]},
        {"id":"review","title":"복습 일정","summary":"읽기 완료와 실제 평가를 근거로 수업·주차 복습을 제안합니다.","steps":["수업 전체 카드 평가 완료 확인","두 수업이 모두 끝나면 다음 날 주차 복습 제안","실패·보류는 다음 확인 계획에 반영"],"criteria":[{"label":"수업 복습","value":"한 수업","meaning":"해당 수업의 모든 카드 범위입니다."},{"label":"주차 복습","value":"두 수업","meaning":"명시적 reviewGroupId를 우선합니다."}],"sourceFiles":["app/learning.py","app/core.py"]}
    ]}
