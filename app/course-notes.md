# 오픈소스SW개발방법및도구 학습 노트

이 문서는 강의 슬라이드와 수업 전사본을 학습용으로 다시 정리한 요약입니다. 원문 파일과 전사 전문은 공개 저장소에 포함하지 않았습니다.

# Lecture 1

Linux는 PC 화면에서 보이는 운영체제만이 아니다. 서버와 슈퍼컴퓨터, 스마트폰 기반, IoT, 로봇, 차량, 게임, 데이터 과학 등 여러 영역에서 핵심 기반으로 쓰인다. Linux kernel과 Ubuntu 같은 배포판의 역할을 구분하고, Linux Foundation이 오픈소스 생태계에서 맡는 역할을 함께 살펴본다.

# Lecture 2

개발은 기술을 고르는 일보다 문제를 정의하는 일에서 시작한다. 사용자와 이해관계자의 요구를 지연 시간, 정확도, 비용, 지원 환경처럼 확인 가능한 지표로 바꿔야 한다. 서버, edge, browser 등 실행 환경이 달라지면 적합한 언어와 framework도 달라진다.

# Lecture 3

모든 문제에 가장 좋은 언어나 운영체제는 없다. 하드웨어 제어, web, data, cloud처럼 문제 영역이 요구하는 특성과 팀의 경험, library 생태계, 배포 제약을 함께 비교한다. 실시간 제어와 자원 제약이 크면 범용 Linux 외에 VxWorks나 Zephyr 같은 선택지도 검토한다.

# Lecture 4

Waterfall은 단계를 순서대로 관리해 계획과 문서가 분명하지만, 늦게 발견한 변경의 비용이 크다. Agile은 짧은 반복으로 동작하는 결과를 확인한다. Scrum은 backlog와 sprint로 작업을 운영하고, DevOps는 개발과 운영을 CI/CD로 연결한다.

# Lecture 5

Diagram은 질문에 맞춰 선택한다. Use case diagram은 사용자와 기능, class diagram은 구조, block diagram은 구성 요소, sequence diagram은 시간 순서, state diagram은 상태와 전이를 보여 준다. 좋은 그림은 독자가 확인할 관계를 남기고 불필요한 세부를 덜어낸다.

# Lecture 6

AI 개발 도구는 코드 완성에서 여러 단계를 수행하는 agent로 발전했다. LLM은 context를 바탕으로 text를 생성하고, harness는 파일 검색과 편집, 실행, test 같은 도구를 연결한다. 결과의 정확성, 보안, 요구사항 충족 여부는 개발자가 확인한다.

# Lecture 7

Agent에게 큰 작업을 맡길 때는 목표, 허용 범위, 완료 조건을 먼저 정한다. AGENTS.md는 저장소 규칙을, skill은 특정 작업 절차를 제공한다. MCP와 도구로 외부 시스템을 연결할 때는 필요한 권한만 허용하고, test와 Git checkpoint로 변경을 검증한다.

# Lecture 8

TDD는 red, green, refactor의 짧은 cycle을 반복한다. unittest는 test case와 suite를 자동 실행하며, mock은 외부 의존성을 통제한다. Profiler는 실행 시간과 호출 빈도를 측정해 최적화 지점을 찾고, coding guideline은 팀이 읽고 유지하기 쉬운 일관성을 만든다.
