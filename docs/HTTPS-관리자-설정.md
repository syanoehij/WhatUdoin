# WhatUdoin HTTPS 설정 — 관리자 가이드

이 문서는 WhatUdoin 서버 관리자를 위한 1회 설정 가이드입니다.
설정 후에는 사용자들이 OS 알림(브라우저 토스트)을 받을 수 있게 됩니다.

---

## 누가 읽나요?

WhatUdoin.exe를 관리·운영하는 담당자 1명.
사용자(일반 직원)는 이 문서 대신 앱 내 **프로필 → 알람 설정** 페이지를 보면 됩니다.

---

## 준비물

- **mkcert** Windows 바이너리
  다운로드: https://github.com/FiloSottile/mkcert/releases
  (`mkcert-v*-windows-amd64.exe` → `mkcert.exe`로 이름 변경 후 PATH 추가 또는 CMD에서 직접 경로 사용)
- **관리자 권한 명령 프롬프트 (CMD)** — 트러스트 스토어 등록에 필요

---

## 1회 설정 절차

**Step 1** — 로컬 루트 CA 생성 및 Windows 트러스트 스토어 등록
```
mkcert -install
```
> 이 명령은 현재 PC(서버)의 Windows 인증서 저장소에 로컬 루트 CA를 등록합니다.
> 서버에서 1번만 실행하면 됩니다.

**Step 2** — 서버 인증서 발급 (`WhatUdoin.exe` 옆 디렉토리에서 실행)
```
mkcert -cert-file whatudoin-cert.pem -key-file whatudoin-key.pem 192.168.0.10 localhost 127.0.0.1
```
> `192.168.0.10` 부분을 **실제 서버 IP 주소**로 바꿔주세요.
> IP가 여러 개라면 공백으로 구분해 모두 나열할 수 있습니다.
> 예: `mkcert -cert-file whatudoin-cert.pem -key-file whatudoin-key.pem 192.168.1.5 192.168.1.6 localhost`

**Step 3** — 루트 CA 파일 복사 (`WhatUdoin.exe` 옆 디렉토리에서 실행)
```
copy "%LOCALAPPDATA%\mkcert\rootCA.pem" whatudoin-rootCA.pem
```
> 사용자들이 앱에서 다운로드할 루트 CA 파일입니다.

**Step 4** — WhatUdoin 재시작
```
WhatUdoin.exe 종료 후 다시 실행
```
> 시작 콘솔에 `HTTPS : https://서버IP:8443` 줄이 표시되면 성공입니다.

---

## 생성되는 파일 3개

| 파일명 | 역할 |
|---|---|
| `whatudoin-cert.pem` | 서버 TLS 인증서 (공개키) |
| `whatudoin-key.pem` | 서버 개인키 — **절대 외부 노출 금지** |
| `whatudoin-rootCA.pem` | 루트 CA — 사용자들이 다운로드해 설치 |

---

## 사용자 안내

설정 완료 후 사용자들에게 아래를 안내하세요:

1. `http://서버IP:8000`으로 접속
2. 프로필(우상단) → **알람 설정** 클릭
3. **인증서 다운로드** 버튼 클릭 → 설치 안내에 따라 설치
4. Chrome/Edge 완전 재시작 후 `https://서버IP:8443`으로 자동 이동됨

---

## 재발급 (서버 IP 변경 시)

서버 IP가 바뀐 경우에만 Step 2를 새 IP로 다시 실행하면 됩니다.
루트 CA(`rootCA.pem`)는 그대로 유지되므로 **사용자가 인증서를 재설치할 필요 없습니다.**

```
mkcert -cert-file whatudoin-cert.pem -key-file whatudoin-key.pem 새IP localhost 127.0.0.1
```

---

## 문제 해결

**포트 8443 접속 불가**
Windows Defender 방화벽 인바운드 규칙에서 TCP 8443 허용이 필요합니다.
```
netsh advfirewall firewall add rule name="WhatUdoin HTTPS" protocol=TCP dir=in localport=8443 action=allow
```

**브라우저에서 인증서 경고가 계속 뜨는 경우**
- 사용자 PC에서 `WhatUdoin-인증서.crt`를 재설치했는지 확인
- 설치 시 저장소를 **"신뢰할 수 있는 루트 인증 기관"** 으로 선택했는지 확인
- Chrome/Edge를 완전 종료 후 재시작 (작업 표시줄 트레이까지 종료)

**콘솔에 `HTTPS : 미적용` 이 뜨는 경우**
`whatudoin-cert.pem`과 `whatudoin-key.pem` 두 파일이 `WhatUdoin.exe`와 같은 폴더에 있는지 확인하세요.

**`mkcert -install` 실패**
관리자 권한 CMD에서 실행했는지 확인하세요.
