# AI Workspace

AI 직원을 고용하고 관리하는 워크스페이스입니다. 지금은 문자로 고객 응대를 맡는 AI 고객센터 한 자리가 있고, 다음 직원은 빈 자리에 한 명씩 앉히면 됩니다.

## 지금 하는 일

AI 고객센터는 수신 문자를 모아 답합니다.

- 지식베이스에 있는 내용으로 문의에 답합니다.
- 견적에 필요한 정보를 받은 뒤 접수하고, 금액은 관리자가 정합니다.
- 빈 시간을 찾아 상담 일정을 잡고, 변경과 취소를 처리합니다.
- 불만, 환불, 가격 협상, 모르는 질문은 관리자에게 넘깁니다.
- 부재중 전화가 오면 안내 문자를 보내고, 고객이 고른 상담 종류를 접수 메모로 남깁니다.

관리 화면에서는 대화, 견적, 일정, 접수 메모, 지식베이스, 계정, 운영 설정을 봅니다.

## 구성

| 경로 | 역할 |
| --- | --- |
| `/` | 워크스페이스. 출근한 AI 직원과 빈 자리 |
| `/ui/admin.html` | AI 고객센터 데스크 |
| `/webhooks/macrodroid/sms` | 휴대폰 문자 수신 |
| `/webhooks/macrodroid/missed-call` | 부재중 전화 수신 |
| `/health` | 서버 상태 |

문자 수신은 MacroDroid가 기본입니다. `SMS_PROVIDER=smsgate`로 바꾸면 SMS Gateway for Android를 씁니다. 고객 정보, 문의, 견적, 일정, 지식베이스는 구글 시트에 두고, 일정은 구글 캘린더와 맞춥니다. 관리자 알림은 텔레그램으로 보냅니다.

## 실행

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

`.env`에 Claude API 키와 웹훅 토큰을 넣습니다. 구글 시트와 캘린더 없이 화면만 보려면 `MOCK_MODE=true`로 둡니다. 이 값은 재시작하면 메모리 내용이 사라집니다.

구글 연동을 쓸 때는 서비스 계정 JSON을 `credentials/service-account.json`에 두고, 시트와 캘린더를 그 계정에 공유한 뒤 아래를 실행합니다.

```bash
python -m scripts.check_google --init
```

테스트는 `pytest`로 실행합니다.

## 올리지 않는 파일

`.env`, `credentials/`, `data/`는 저장소에 포함되지 않습니다. API 키, 서비스 계정, 대화 기록이 들어 있기 때문입니다.

첫 실행 때 로컬 관리자 계정이 하나 만들어집니다. 공개된 환경에서 쓰기 전에 `app/main.py`의 기본 계정을 바꾸세요. 관리 화면과 관리 API는 이 컴퓨터에서만 열리고, Cloudflare 터널로 들어온 요청은 웹훅만 통과합니다.
