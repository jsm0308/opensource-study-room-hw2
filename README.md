# 오픈소스SW 학습실

오픈소스SW개발방법및도구 수업만 담은 웹 학습 서비스입니다. 강의별 핵심 카드를 읽고 확인 문제를 풀며, 학습 기록과 복습 일정을 한 화면에서 관리합니다.

## 이 저장소에 담긴 내용

- Lecture 1~8 핵심 카드 8개
- 강의별 미리보기와 복습용 확인 문제
- 규칙 기반 힌트와 공개 데모 피드백
- 학습 기록, 달력, 카드 관계도
- GitHub Actions 테스트와 Render 자동 배포 설정

강의 슬라이드와 수업 전사 전문은 저작권과 개인정보를 고려해 공개하지 않았습니다. `app/course-notes.md`와 `app/seed.json`에는 두 자료를 바탕으로 다시 쓴 요약만 있습니다.

## 로컬 실행

Python 3.12 환경에서 다음 명령을 실행합니다.

```bash
python -m venv .venv
python -m pip install -r requirements.txt
```

Windows PowerShell:

```powershell
$env:STUDY_DEMO_MODE="1"
.venv\Scripts\python.exe app\server.py --no-browser
```

브라우저에서 `http://127.0.0.1:8765`를 엽니다.

## 테스트

```bash
python -m unittest discover -s tests -p "test_*.py" -v
```

테스트는 공개 seed가 한 과목만 포함하는지, 원본 PDF가 섞이지 않았는지, 규칙 기반 문제 생성과 초기 구동이 정상인지 확인합니다. 같은 명령을 GitHub Actions에서도 실행합니다.

## 배포 구조

Render는 `main` branch의 push를 감지해 build와 배포를 다시 실행합니다. 서버는 Render가 지정한 `PORT`와 `0.0.0.0`을 사용하며, 외부 Host와 Origin을 허용 목록으로 검사합니다.

공개 배포판은 개인 Codex 로그인이나 API key를 사용하지 않습니다. 문제와 피드백은 저장된 카드에서 규칙으로 만듭니다. Render 무료 인스턴스의 저장소는 임시이므로 배포가 다시 시작되면 사용자가 만든 학습 기록이 초기화될 수 있습니다.

## 기술 구성

- Backend: Python 표준 라이브러리 HTTP server, SQLite
- Frontend: Vanilla JavaScript, HTML, CSS
- Scheduling: FSRS
- CI: GitHub Actions
- Deployment: Render
