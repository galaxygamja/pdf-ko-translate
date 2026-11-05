# pdf-ko-translate

영어 논문 PDF를 원래 레이아웃 그대로 두고 글만 한국어로 바꿉니다. 그림, 표, 수식, 참고문헌은 원본을 유지합니다.
API 키 없이, 로그인한 내 계정의 구독 사용량으로 번역합니다. 번역 엔진은 둘 중 하나를 고릅니다.

- **Claude**: Claude Code CLI(`claude -p`) + claude.ai 계정
- **ChatGPT**: OpenAI Codex CLI(`codex exec`) + ChatGPT 계정

맥과 윈도우에서 동작합니다.

## 친구에게 받은 앱 설치하기 (맥·윈도우)

[Releases](../../releases) 페이지에서 내 컴퓨터에 맞는 파일을 받습니다.

| 컴퓨터 | 받을 파일 |
|---|---|
| 윈도우 10/11 | `PDF-Translator-Setup-<버전>.exe` |
| 맥 (M1 이후 Apple 칩) | `PDF-Translator-<버전>-mac-apple-silicon.dmg` |

Intel 맥은 지원하지 않습니다.

이 앱은 유료 개발자 인증서로 서명하지 않았기 때문에 처음 열 때 경고가 나옵니다.

- **윈도우**: "Windows의 PC 보호" 창이 뜨면 `추가 정보` → `실행`을 누릅니다. 설치 후 시작 메뉴에서 `PDF 번역기`를 엽니다.
- **맥**: dmg를 열어 `PDF 번역기`를 `Applications`로 끌어다 놓고 더블클릭합니다. 막히면 `시스템 설정 › 개인정보 보호 및 보안` 맨 아래의 `그래도 열기`를 누릅니다 (한 번만).
  "손상되었기 때문에 열 수 없습니다"라고 나오면 터미널에서 `xattr -dr com.apple.quarantine "/Applications/PDF 번역기.app"`를 실행한 뒤 다시 엽니다.

앱을 처음 열면 맨 위 **번역 엔진** 카드에서 쓸 계정을 고릅니다.

1. 쓰려는 엔진(Claude 또는 ChatGPT)을 누릅니다.
2. `설치하기`가 보이면 누릅니다. 터미널(윈도우는 PowerShell) 창이 열려 공식 설치 스크립트가 실행됩니다. 끝나면 그 창은 닫아도 됩니다.
3. `로그인하기`를 누르면 브라우저가 열립니다. 내 claude.ai 또는 ChatGPT 계정으로 로그인합니다.
4. 앱으로 돌아와 `다시 확인`을 누르면 `준비됨 ✓`이 표시됩니다.

ChatGPT는 Plus 이상 요금제여야 Codex를 쓸 수 있고, Claude는 Pro 이상이어야 Claude Code를 쓸 수 있습니다. 번역은 각자 계정의 사용량 한도 안에서 이뤄지고, 한도에 걸리면 저장 후 멈췄다가 다시 시작하면 이어서 합니다.

## 개발자용 설치 (명령어)

Claude Code 또는 Codex가 설치되어 있고 로그인되어 있어야 합니다.

Claude Code가 설치되어 있고 claude.ai 계정으로 로그인되어 있어야 합니다.

```bash
uv tool install /Users/cloudgamja/projects/papar_translation
```

이후 어느 폴더에서나 `pdf-ko-translate 논문.pdf`로 실행합니다. 코드를 고친 뒤에는 `--force`를 붙여 다시 설치합니다.

## 앱으로 쓰기 (버튼 조작)

`응용 프로그램(사용자) › PDF 번역기`를 더블클릭하면 앱 창이 열립니다. 브라우저나 서버 없이 앱 창 안에서 바로 조작합니다.
Finder에서 `Cmd+Shift+H`(홈 폴더) › `Applications` 폴더로 가면 있고, Dock으로 끌어다 두면 편합니다.

1. PDF를 창에 끌어다 놓거나, 눌러서 파일을 고릅니다.
2. 필요하면 `설정`에서 쪽 범위, 역주, 검증, 모델, 내 용어집을 고릅니다.
3. `번역 시작`을 누르면 단계별 진행 상황이 보이고, 끝나면 `번역본 열기`가 나타납니다.

- 결과물은 `문서 › PDF 번역 › <파일 이름>` 폴더에 저장되고, `지난 번역` 목록에서 다시 열 수 있습니다.
- 창을 닫으면 앱이 종료됩니다. 번역 중이면 확인을 묻고, 멈춘 번역은 같은 파일로 다시 시작하면 이어서 합니다.
- 앱이 사라졌다면 `pdf-ko-translate-app --install-app`으로 다시 만들 수 있습니다.

## 명령어로 쓰기

```bash
pdf-ko-translate 논문.pdf
```

같은 폴더에 다음 파일이 생깁니다.

- `논문.ko.pdf`: 번역본. 원문 오류로 보이는 곳에는 `[역주 N]` 표시가 붙고, 맨 끝에 '역자 주' 쪽이 붙습니다.
- `논문.ko.json`: 진행 파일 (용어집, 역주, 번역문)
- `논문.ko.qa.txt`: 문단별 대조 검사 결과 (숫자, 그림·표 참조, 인용 번호가 빠진 곳)

| 옵션 | 설명 |
|---|---|
| `-o 경로` | 결과 PDF 경로 |
| `--pages 1-3,5` | 일부 쪽만 번역 (결과에도 그 쪽만 담김) |
| `--provider claude` | 번역 엔진: `claude`(기본) 또는 `codex`(ChatGPT 계정) |
| `--model sonnet` | 번역 모델. claude는 `sonnet`(기본)·`opus`·`haiku`, codex는 `모델이름@생각정도` (기본 `@medium` = Codex 기본 모델) |
| `--workers 4` | 동시 번역 호출 수 |
| `--overflow push` | 한국어가 넘칠 때 아래 내용을 밀어 내림 (기본). `shrink`는 글자를 줄여 칸에 맞춤 |
| `--glossary 내용어.json` | 내 용어집. 자동 용어집보다 우선합니다 (아래 예시) |
| `--no-review` | 원문 오류 검토(역주) 단계를 건너뜀 |
| `--review-model opus` | 원문 오류 검토에 쓰는 모델 (기본: claude는 opus, codex는 `@high`) |
| `--no-verify` | 번역 후 원문과 문장 단위로 대조하는 검증 단계를 건너뜀 |
| `--no-annotate` | 번역한 용어 첫 등장 때 `손실 함수(loss function)`처럼 영어를 붙이지 않음 |
| `--render-only` | 번역 호출 없이 `.ko.json`의 번역으로 PDF만 다시 만듦 |
| `--debug-layout` | 번역 대상(초록)과 제외 대상(빨강)을 표시한 `.ko.layout.pdf`도 만듦 |

## 내 용어집

같은 개념을 여러 영어 표현으로 쓰는 경우 `variants`에 모으면 모두 같은 한국어로 옮깁니다.

```json
[
  {"en": "biofilm", "action": "translate", "ko": "생물막", "variants": ["biofilms"]},
  {"en": "exopolymeric substances", "action": "translate", "ko": "세포외 고분자 물질", "variants": ["exopolymeric matrix", "EPS matrix"]},
  {"en": "fine-tuning", "action": "keep"}
]
```

## 이어 하기와 수정

- 사용량 한도나 오류로 멈추면 그때까지의 번역이 `.ko.json`에 저장됩니다. 같은 명령을 다시 실행하면 남은 문단만 번역합니다.
- `.ko.json`의 `glossary`(용어집)나 `translations`의 `ko`를 직접 고친 뒤 `--render-only`로 다시 만들 수 있습니다.
  번역 지침, 용어집, 모델이 바뀌면 다음 실행 때 자동으로 다시 번역합니다.
- 역주를 다시 뽑으려면 `.ko.json`의 `notes`를 `null`로 바꾸고 실행하세요.

## 동작 방식

1. **추출** (`extract.py`): 글자 좌표·폰트를 읽어 단 구분, 문단 나누기, 단·쪽을 넘어가는 문단 잇기를 합니다.
   그림 영역, 표, 머리말·꼬리말, 참고문헌은 번역 대상에서 뺍니다.
2. **용어집** (`translate.py`): 문서 전체에서 전문용어를 뽑아 영어로 둘지, 한국어로 옮길지 정합니다.
3. **번역**: 문단 묶음 단위로 `claude -p`(또는 `codex exec`)를 병렬 호출합니다 (`providers.py`). 기울임·위첨자·인용 색 같은 서식은 태그로 보존합니다.
   숫자, 그림·표 참조, 인용 번호가 빠지거나 용어집과 다른 번역어를 쓴 문단은 문제를 알려 주고 한 번 더 번역합니다 (`qa.py`).
   자동 용어집이 틀린 번역어를 정했다면 `--glossary`로 덮어쓰세요 (`.ko.qa.txt`에 용어집 위반이 남습니다).
4. **원문 검토**: 본문·표·그림 이미지와 참고문헌 목록을 따로 살펴, 확실한 오류·불일치만 역주로 붙입니다.
   역주 번호는 본문에 나오는 순서대로 매기고, 같은 문제가 여러 곳에 있으면 모든 위치에 같은 번호를 답니다.
   번역문은 원문대로 옮기고(고치지 않음), 해당 문장 바로 뒤에 `[역주 N]`을 답니다. 역주는 "~로 보이나 원저자 확인이 필요하다"처럼 단정하지 않습니다.
5. **검증**: 번역문을 원문과 문장 단위로 대조해 빠지거나 뜻이 바뀐 문단을 다시 번역하고, 결과를 `.ko.qa.txt`에 남깁니다.
6. **렌더링** (`render.py`, `reflow.py`): 원문 글자만 지우고(이미지·도형은 그대로) 같은 자리에 한국어를 조판합니다.
   넘치면 글자를 최대 6%까지 줄여 보고, 그래도 넘치면 같은 단의 아래 내용(문단·그림·표)을 밀어 내립니다.
   쪽 아래 끝을 넘으면 그 쪽만 세로로 늘어납니다. 텍스트 레이어는 읽기 순서와 띄어쓰기를 지켜 복사·검색이 됩니다.

## 폰트

기본으로 시스템 폰트를 씁니다. 맥은 본문 AppleMyungjo(세리프 원문) 또는 Apple SD Gothic Neo, 굵은 글씨는 Apple SD Gothic Neo Bold입니다.
윈도우는 본문 바탕(세리프 원문) 또는 맑은 고딕, 굵은 글씨는 맑은 고딕 Bold입니다. 영문은 Times/Helvetica입니다.
같은 번역이라도 맥과 윈도우에서 만든 PDF는 한글 글꼴이 다릅니다.
다른 폰트를 쓰려면 환경 변수로 지정합니다.

```bash
PDFKO_SERIF=~/Library/Fonts/NotoSerifKR-Regular.otf pdf-ko-translate 논문.pdf
```

`PDFKO_SANS`, `PDFKO_SANS_BOLD`도 같은 방식입니다.

## 아직 안 되는 것 (다음 단계)

- 다시 만든 쪽(밀어 내린 쪽)에서는 원문 PDF의 링크 클릭 영역이 사라짐
- 그림 안 글자·표 칸 번역, 스캔본 PDF(OCR)

## 배포판 만들기 (GitHub Actions)

`v`로 시작하는 태그를 올리면 `.github/workflows/release.yml`이 윈도우 설치 파일과 맥 dmg(Apple Silicon)를 만들어 GitHub Release에 올립니다.

```bash
git tag v0.2.0 && git push origin v0.2.0
```

이 맥에서 직접 `.app`만 만들어 보려면:

```bash
uv run --group build pyinstaller packaging/pdf-ko-translate.spec --noconfirm --distpath build/dist --workpath build/work
```

- `packaging/pdf-ko-translate.spec`: PyInstaller 설정. 같은 실행 파일이 `--run-cli` 인자로 명령어 버전도 됩니다 (윈도우는 `pdf-ko-translate-cli.exe`를 따로 둠).
- `packaging/installer.iss`: 윈도우 설치 파일(Inno Setup). 관리자 권한 없이 사용자 폴더에 설치합니다.
