"""Deterministic public-demo tutor used when no private Codex session is available."""
from __future__ import annotations

import json
import re


class OfflineBridge:
    model = "offline-demo"
    effort = "rules"

    def __init__(self, workspace):
        self.workspace = workspace
        self.status = {
            "connected": True,
            "message": "공개 데모의 규칙 기반 학습 모드입니다.",
            "model": self.model,
            "chatAvailable": True,
            "imageSupported": False,
            "transport": "offline",
        }

    def connect(self):
        return dict(self.status)

    def close(self):
        return None

    @staticmethod
    def _payload(prompt):
        for line in reversed(str(prompt).splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    continue
        return {}

    @staticmethod
    def _tokens(text):
        return {word.lower() for word in re.findall(r"[0-9A-Za-z가-힣]{2,}", str(text)) if len(word) > 1}

    def ask(self, prompt, schema, image_path=None, cancel=None, progress=None, **_):
        properties = schema.get("properties", {})
        payload = self._payload(prompt)

        if "questions" in properties:
            cards = payload.get("cards", [])
            count = int(payload.get("count") or len(cards) or 1)
            per_card = max(1, count // max(1, len(cards)))
            questions = []
            for card in cards:
                requirements = card.get("requirements") or []
                for index in range(per_card):
                    requirement = requirements[index % len(requirements)] if requirements else {
                        "id": "core", "behavior": "핵심을 설명한다", "conditions": "수업 카드의 내용을 사용한다", "level": 2
                    }
                    apply_question = index % 2 == 1
                    prompt_text = (
                        f"{card['title']}의 내용을 새로운 개발 상황에 적용한다면 무엇을 먼저 확인해야 하나요?"
                        if apply_question else f"{card['title']}의 핵심을 이유와 함께 설명해 보세요."
                    )
                    questions.append({
                        "cardId": card["id"],
                        "requirementIds": [requirement["id"]],
                        "familyId": f"offline-{card['id']}-{'apply' if apply_question else 'recall'}",
                        "title": "적용 확인" if apply_question else "핵심 회상",
                        "prompt": prompt_text,
                        "answer": card.get("core", "수업 카드의 핵심 설명을 사용합니다."),
                        "explanation": "공개 데모는 저장된 수업 카드에서 문제와 기준을 구성합니다.",
                        "difficulty": min(5, max(1, int(requirement.get("level", 2)))),
                        "checks": [{
                            "id": f"check-{card['id']}-{index}",
                            "requirementId": requirement["id"],
                            "label": requirement.get("behavior", "핵심을 설명한다"),
                            "essential": True,
                        }],
                    })
            return {"questions": questions[:count]}

        if "valid" in properties:
            return {"valid": True, "issues": []}

        if "checks" in properties:
            question = payload.get("question", {})
            answer = str((payload.get("answer") or {}).get("text", "")).strip()
            expected = self._tokens(question.get("answer", ""))
            observed = self._tokens(answer)
            overlap = sorted(expected & observed)
            sufficient = bool(answer) and "모르" not in answer and (len(answer) >= 35 or bool(overlap))
            checks = [{
                "id": check["id"],
                "status": "met" if sufficient else "not_met",
                "evidence": (f"답안에서 연결된 핵심어를 확인했습니다: {', '.join(overlap[:4])}" if overlap else
                             "공개 데모는 답안의 길이와 저장된 핵심어 일치만 확인합니다."),
            } for check in question.get("checks", [])]
            return {
                "checks": checks,
                "confirmed": "핵심 조건을 자기 문장으로 설명했습니다." if sufficient else "답안을 제출했습니다.",
                "missing": "" if sufficient else "이유나 조건을 한 문장 더 적어야 합니다.",
                "nextAction": "강의 카드의 핵심 문장과 내 설명을 비교해 보세요.",
                "explanation": "이 결과는 공개 시연용 규칙 기반 피드백이며, 의미를 완전히 판정하는 AI 채점은 아닙니다.",
            }

        if "hint" in properties:
            return {"hint": "질문에서 요구한 대상과 조건을 먼저 나누고, 수업 카드의 핵심 용어 하나를 연결해 보세요."}

        if {"answer", "suggestion", "reason"}.issubset(properties):
            card = payload.get("card", {})
            core = card.get("core", "선택한 카드의 핵심 내용을 다시 확인해 보세요.")
            return {"answer": f"공개 데모의 저장된 설명은 다음과 같습니다. {core}", "suggestion": "", "reason": ""}

        if "overview" in properties:
            return {
                "overview": "추가한 자료를 공개 데모 규칙으로 정리했습니다.",
                "reviewMarkdown": "# 추가 자료\n\n공개 데모에서는 원문을 보관하고 핵심 내용을 직접 확인합니다.",
                "preview": {"prompt": "자료의 핵심 문제는 무엇인가요?", "solution": "원문에서 목적과 조건을 찾아 설명합니다."},
                "cards": [],
            }

        raise RuntimeError("공개 데모에서 지원하지 않는 학습 요청입니다.")
