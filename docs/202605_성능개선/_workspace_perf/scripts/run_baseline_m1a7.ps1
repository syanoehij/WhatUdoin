#Requires -Version 5.1
<#
.SYNOPSIS
    M1a-7 통합 baseline 측정 runner

.DESCRIPTION
    plan §15 측정 절차 + advisor 권장 4종(sanity run / graceful shutdown /
    readiness wait / 환경 메타데이터)을 9 phase로 통합 실행.

    Phase 0: pre-flight 점검
    Phase 1: run 디렉터리 + 환경 메타데이터 캡처
    Phase 2: snapshot + seed
    Phase 3: 서버 시작 + readiness wait
    Phase 4: sanity run (1 VU × 30s)
    Phase 5: 본 측정 5단계 (1/5/10/25/50 VU)
    Phase 6: SSE keep-alive (50 VU 병렬)
    Phase 7: 서버 graceful shutdown
    Phase 8: cleanup + 검증
    Phase 9: 결과 요약 생성

.NOTES
    운영 코드 변경 0.
    측정 후 cleanup 실패 시:
        python _workspace/perf/scripts/restore_db.py --confirm-overwrite
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ─────────────────────────────────────────────────────────────────────────────
# 전역 상수
# ─────────────────────────────────────────────────────────────────────────────
$REPO_ROOT      = "D:\Github\WhatUdoin"
$PYTHON         = "D:\Program Files\Python\Python312\python.exe"
$LOCUST_CMD     = "locust"
$LOCUSTFILE     = "$REPO_ROOT\_workspace\perf\locust\locustfile.py"
$SEED_SCRIPT    = "$REPO_ROOT\_workspace\perf\fixtures\seed_users.py"
$CLEANUP_SCRIPT = "$REPO_ROOT\_workspace\perf\fixtures\cleanup.py"
$SNAPSHOT_SCRIPT= "$REPO_ROOT\_workspace\perf\scripts\snapshot_db.py"
$SSE_SCRIPT     = "$REPO_ROOT\_workspace\perf\scripts\sse_keepalive.py"
$DB_PATH        = "$REPO_ROOT\whatudoin.db"
$BASELINE_DIR   = "$REPO_ROOT\_workspace\perf\baseline_2026-05-09"
$COOKIES_JSON   = "$REPO_ROOT\_workspace\perf\fixtures\session_cookies.json"
$HTTPS_HOST     = "https://localhost:8443"

# ─────────────────────────────────────────────────────────────────────────────
# 상태 플래그
# ─────────────────────────────────────────────────────────────────────────────
$script:serverStarted = $false
$script:seedDone      = $false
$script:proc          = $null   # uvicorn process
$script:sseProc       = $null   # sse_keepalive process
$script:runDir        = $null

# ─────────────────────────────────────────────────────────────────────────────
# 유틸리티
# ─────────────────────────────────────────────────────────────────────────────
function Write-Phase {
    param([string]$Msg)
    Write-Host ""
    Write-Host "═══════════════════════════════════════════════" -ForegroundColor Cyan
    Write-Host "  $Msg" -ForegroundColor Cyan
    Write-Host "═══════════════════════════════════════════════" -ForegroundColor Cyan
}

function Write-OK    { param([string]$Msg) Write-Host "[OK]   $Msg" -ForegroundColor Green }
function Write-WARN  { param([string]$Msg) Write-Host "[WARN] $Msg" -ForegroundColor Yellow }
function Write-INFO  { param([string]$Msg) Write-Host "[INFO] $Msg" }
function Write-ABORT {
    param([string]$Msg)
    Write-Host "[ABORT] $Msg" -ForegroundColor Red
    throw "ABORT: $Msg"
}


# ─────────────────────────────────────────────────────────────────────────────
# Phase 7: 서버 graceful shutdown (finally에서 호출)
# ─────────────────────────────────────────────────────────────────────────────
function Invoke-ServerShutdown {
    Write-Phase "Phase 7: 서버 graceful shutdown"

    if ($script:sseProc -and -not $script:sseProc.HasExited) {
        Write-INFO "SSE 프로세스 종료 대기 (최대 90초)..."
        $script:sseProc.WaitForExit(90000) | Out-Null
        if (-not $script:sseProc.HasExited) {
            Write-WARN "SSE 프로세스 강제 종료"
            Stop-Process -Id $script:sseProc.Id -Force -ErrorAction SilentlyContinue
        } else {
            Write-OK "SSE 프로세스 정상 종료"
        }
    }

    if ($script:proc -eq $null -or $script:proc.HasExited) {
        Write-INFO "서버 프로세스가 이미 종료됨"
        return
    }

    Write-INFO "uvicorn 종료 시도 (PID: $($script:proc.Id))..."
    # Windows 콘솔 앱은 CloseMainWindow가 no-op. Stop-Process (WM_CLOSE) → Force 시퀀스 사용.
    Stop-Process -Id $script:proc.Id -ErrorAction SilentlyContinue
    $script:proc.WaitForExit(3000) | Out-Null

    if (-not $script:proc.HasExited) {
        Write-WARN "3초 후에도 살아있음 → Force 종료"
        Stop-Process -Id $script:proc.Id -Force -ErrorAction SilentlyContinue
    }

    Start-Sleep -Milliseconds 1000

    # WAL 파일 잔존 확인
    $walPath = "$DB_PATH-wal"
    if (Test-Path $walPath) {
        Write-WARN "whatudoin.db-wal 잔존. 5초 추가 대기..."
        Start-Sleep -Seconds 5
        if (Test-Path $walPath) {
            Write-WARN "WAL 파일이 여전히 존재합니다. DB 정합성을 확인하세요."
        } else {
            Write-OK "WAL 파일 소멸 확인"
        }
    } else {
        Write-OK "WAL 파일 없음 — 서버 정상 종료"
    }

    # server_stderr.log 마지막 20줄 출력 (진단용)
    if ($script:runDir -and (Test-Path "$($script:runDir)\server_stderr.log")) {
        Write-INFO "server_stderr.log (마지막 20줄):"
        Get-Content "$($script:runDir)\server_stderr.log" -Tail 20 | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }
    }
}

# ─────────────────────────────────────────────────────────────────────────────
# Phase 8: cleanup + 검증 (finally에서 호출)
# ─────────────────────────────────────────────────────────────────────────────
function Invoke-Cleanup {
    Write-Phase "Phase 8: cleanup + 검증"

    try {
        $env:WHATUDOIN_PERF_FIXTURE = "allow"
        $env:WHATUDOIN_DB_PATH      = $DB_PATH
        $output = & $PYTHON $CLEANUP_SCRIPT 2>&1
        Write-Host ($output -join "`n")
        $cleanExit = $LASTEXITCODE
        Remove-Item Env:WHATUDOIN_PERF_FIXTURE -ErrorAction SilentlyContinue
        Remove-Item Env:WHATUDOIN_DB_PATH      -ErrorAction SilentlyContinue

        if ($cleanExit -ne 0) {
            throw "cleanup.py 비정상 종료 (exit $cleanExit)"
        }
    } catch {
        Write-WARN "cleanup 실패: $_"
        Write-WARN "복원 절차: python _workspace/perf/scripts/restore_db.py --confirm-overwrite"
        return
    }

    # 검증 SELECT 3종
    $tmpPy_cleanup = Join-Path $script:runDir "phase8_cleanup_verify_tmp.py"
    $code = @"
import sqlite3
c = sqlite3.connect('$($DB_PATH -replace '\\','/')')
u  = c.execute("SELECT COUNT(*) FROM users    WHERE name  LIKE 'test_perf_%'").fetchone()[0]
s  = c.execute("SELECT COUNT(*) FROM sessions WHERE user_id IN (SELECT id FROM users WHERE name LIKE 'test_perf_%')").fetchone()[0]
ev = c.execute("SELECT COUNT(*) FROM events   WHERE title LIKE 'test_perf_evt_%'").fetchone()[0]
c.close()
print(f'{u},{s},{ev}')
"@
    Set-Content -Path $tmpPy_cleanup -Value $code -Encoding utf8
    $counts = (& $PYTHON $tmpPy_cleanup 2>&1).Trim()
    if ($LASTEXITCODE -ne 0) {
        Write-WARN "검증 SELECT 실패. 수동 확인 필요."
        return
    }

    $parts = $counts -split ","
    $uCnt  = [int]$parts[0]
    $sCnt  = [int]$parts[1]
    $evCnt = [int]$parts[2]

    Write-INFO "검증 결과 — users: $uCnt / sessions: $sCnt / events: $evCnt"

    if ($uCnt -eq 0 -and $sCnt -eq 0 -and $evCnt -eq 0) {
        Write-OK "cleanup 검증 3종 통과 (모두 0)"
    } else {
        Write-WARN "cleanup 잔존 데이터 발견 (users=$uCnt, sessions=$sCnt, events=$evCnt)"
        Write-WARN "복원 절차: python _workspace/perf/scripts/restore_db.py --confirm-overwrite"
    }
}

# ─────────────────────────────────────────────────────────────────────────────
# CSV p95/실패율 추출 헬퍼
# ─────────────────────────────────────────────────────────────────────────────
function Get-LocustStats {
    param([string]$CsvPrefix)
    $statsFile = "${CsvPrefix}_stats.csv"
    if (-not (Test-Path $statsFile)) {
        return @{ found = $false; p95 = "N/A"; p99 = "N/A"; failRate = "N/A"; rps = "N/A" }
    }
    $rows = Import-Csv $statsFile
    # Aggregated 행 (Name = "Aggregated") 기준
    $agg = $rows | Where-Object { $_.Name -eq "Aggregated" }
    if (-not $agg) { $agg = $rows[-1] }

    $p95  = if ($agg.'95%')          { $agg.'95%' }          else { "N/A" }
    $p99  = if ($agg.'99%')          { $agg.'99%' }          else { "N/A" }
    $rps  = if ($agg.'Requests/s')   { $agg.'Requests/s' }   else { "N/A" }

    $reqCnt  = [double]($agg.'Request Count'  -replace '[^0-9.]','')
    $failCnt = [double]($agg.'Failure Count'  -replace '[^0-9.]','')
    $failRate = if ($reqCnt -gt 0) { "$([math]::Round($failCnt / $reqCnt * 100, 1))%" } else { "N/A" }

    return @{ found = $true; p95 = $p95; p99 = $p99; failRate = $failRate; rps = $rps }
}

# sanity run 그룹별 실패율 계산
function Test-SanityGate {
    param([string]$CsvPrefix)
    $statsFile = "${CsvPrefix}_stats.csv"
    if (-not (Test-Path $statsFile)) {
        Write-ABORT "sanity CSV 없음: $statsFile"
    }

    $rows = Import-Csv $statsFile

    # view_pages 그룹 = 조회 엔드포인트
    $vpNames = @("/", "/check", "/project-manage", "/trash", "/api/events", "/api/kanban", "/calendar", "/api/checklists")
    $vpRows  = $rows | Where-Object { $_.Name -in $vpNames }

    # event_crud 그룹 = events POST/PUT/DELETE
    $ecRows  = $rows | Where-Object { $_.Name -match "/api/events" -and $_.Name -match "\[" }

    function Get-GroupFailRate {
        param($GroupRows, [string]$GroupName)
        $totalReq  = ($GroupRows | ForEach-Object { [double]($_.'Request Count' -replace '[^0-9.]','') } | Measure-Object -Sum).Sum
        $totalFail = ($GroupRows | ForEach-Object { [double]($_.'Failure Count' -replace '[^0-9.]','') } | Measure-Object -Sum).Sum
        if ($totalReq -eq 0) {
            Write-WARN "$GroupName 그룹 요청 0건 — sanity 판정 보류"
            return 0.0
        }
        $rate = $totalFail / $totalReq * 100
        Write-INFO "$GroupName 실패율: $([math]::Round($rate, 1))% (요청 $totalReq / 실패 $totalFail)"
        return $rate
    }

    $vpFailRate = Get-GroupFailRate $vpRows  "view_pages"
    $ecFailRate = Get-GroupFailRate $ecRows  "event_crud"

    # upload_file / ai_parse: 실패 예상 → 경고만
    $upRows = $rows | Where-Object { $_.Name -match "upload" }
    $aiRows = $rows | Where-Object { $_.Name -match "ai/parse" }
    if ($upRows) {
        $uReq  = ($upRows | ForEach-Object { [double]($_.'Request Count' -replace '[^0-9.]','') } | Measure-Object -Sum).Sum
        $uFail = ($upRows | ForEach-Object { [double]($_.'Failure Count' -replace '[^0-9.]','') } | Measure-Object -Sum).Sum
        if ($uReq -gt 0) { Write-INFO "upload_file 실패율: $([math]::Round($uFail/$uReq*100,1))% (PIL.verify 한계로 100% 예상됨)" }
    }
    if ($aiRows) {
        $aReq  = ($aiRows | ForEach-Object { [double]($_.'Request Count' -replace '[^0-9.]','') } | Measure-Object -Sum).Sum
        $aFail = ($aiRows | ForEach-Object { [double]($_.'Failure Count' -replace '[^0-9.]','') } | Measure-Object -Sum).Sum
        if ($aReq -gt 0) { Write-INFO "ai_parse 실패율: $([math]::Round($aFail/$aReq*100,1))% (Ollama 미응답 시 높을 수 있음)" }
    }

    # 통과 기준: view_pages < 50%, event_crud < 50%
    if ($vpFailRate -ge 50) {
        Write-ABORT "sanity 실패 — view_pages 실패율 $([math]::Round($vpFailRate,1))% >= 50%"
    }
    if ($ecFailRate -ge 50) {
        Write-ABORT "sanity 실패 — event_crud 실패율 $([math]::Round($ecFailRate,1))% >= 50%"
    }

    # connection refused / SSL 에러 확인
    if ($script:runDir -and (Test-Path "$($script:runDir)\server_stderr.log")) {
        $sslErrors = Select-String -Path "$($script:runDir)\server_stderr.log" `
            -Pattern "(connection refused|ssl handshake|SSLError)" -CaseSensitive:$false
        if ($sslErrors -and $sslErrors.Count -gt 10) {
            Write-ABORT "sanity 실패 — server_stderr에 SSL/연결 오류 $($sslErrors.Count)건 감지"
        }
    }

    Write-OK "sanity run 통과 (view_pages $([math]::Round($vpFailRate,1))% / event_crud $([math]::Round($ecFailRate,1))%)"
}

# ─────────────────────────────────────────────────────────────────────────────
# 메인 실행
# ─────────────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "╔═══════════════════════════════════════════════════════╗" -ForegroundColor Magenta
Write-Host "║  WhatUdoin M1a-7 baseline 측정 runner                ║" -ForegroundColor Magenta
Write-Host "║  $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')                          ║" -ForegroundColor Magenta
Write-Host "╚═══════════════════════════════════════════════════════╝" -ForegroundColor Magenta

# 환경변수 자체 설정
$env:WHATUDOIN_PERF_FIXTURE = "allow"

try {
    # ─────────────────────────────────────────────────────────────────────────
    # Phase 0: pre-flight 점검
    # ─────────────────────────────────────────────────────────────────────────
    Write-Phase "Phase 0: pre-flight 점검"

    # 포트 8443, 8000 미사용 확인
    $listeningPorts = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
        Where-Object { $_.LocalPort -in @(8443, 8000) }
    if ($listeningPorts) {
        $portsInUse = ($listeningPorts | ForEach-Object { $_.LocalPort }) -join ", "
        Write-ABORT "포트 $portsInUse 이미 사용 중. 실행 중인 서버를 먼저 종료하세요."
    }
    Write-OK "8443/8000 포트 미사용 확인"

    # Ollama 11434 확인 (경고만)
    $ollamaConn = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
        Where-Object { $_.LocalPort -eq 11434 }
    if ($ollamaConn) {
        Write-OK "Ollama 11434 포트 listen 확인"
    } else {
        Write-WARN "Ollama 11434 포트 없음. ai_parse task 실패 예상 — 다른 phase는 계속 진행"
    }

    # locust + httpx 설치 확인
    $locustCheck = & $PYTHON -m pip show locust 2>&1
    if ($LASTEXITCODE -ne 0) { Write-ABORT "locust 미설치. pip install locust" }
    Write-OK "locust 설치 확인"

    $httpxCheck = & $PYTHON -m pip show httpx 2>&1
    if ($LASTEXITCODE -ne 0) { Write-ABORT "httpx 미설치. pip install httpx" }
    Write-OK "httpx 설치 확인"

    # 필수 파일 존재 확인
    $requiredFiles = @(
        "$REPO_ROOT\whatudoin-cert.pem",
        "$REPO_ROOT\whatudoin-key.pem",
        "$REPO_ROOT\credentials.json",
        $DB_PATH
    )
    foreach ($f in $requiredFiles) {
        if (-not (Test-Path $f)) { Write-ABORT "필수 파일 없음: $f" }
    }
    Write-OK "필수 파일 존재 확인 (cert/key/credentials/db)"

    # WAL/SHM 파일 존재 시 abort (서버 종료 확인)
    $walFile = "$DB_PATH-wal"
    $shmFile = "$DB_PATH-shm"
    if ((Test-Path $walFile) -or (Test-Path $shmFile)) {
        Write-ABORT "WAL/SHM 파일 존재 — WhatUdoin 서버가 실행 중입니다. 종료 후 재실행하세요."
    }
    Write-OK "WAL/SHM 파일 없음 (서버 종료 확인)"

    Write-OK "Phase 0 통과"

    # ─────────────────────────────────────────────────────────────────────────
    # Phase 1: run 디렉터리 + 환경 메타데이터 캡처
    # ─────────────────────────────────────────────────────────────────────────
    Write-Phase "Phase 1: run 디렉터리 생성 + 환경 메타데이터 캡처"

    $runTimestamp = Get-Date -Format "HHmmss"
    $script:runDir = "$BASELINE_DIR\run_$runTimestamp"
    New-Item -ItemType Directory -Path $script:runDir -Force | Out-Null
    Write-OK "run 디렉터리 생성: $($script:runDir)"

    # 환경 메타데이터 캡처
    $metaFile = "$($script:runDir)\environment_metadata.md"

    $osInfo  = Get-CimInstance Win32_OperatingSystem
    $cpuInfo = Get-CimInstance Win32_Processor | Select-Object -First 1
    $ramInfo = Get-CimInstance Win32_ComputerSystem

    $ramTotalGB = [math]::Round($ramInfo.TotalPhysicalMemory / 1GB, 1)

    $pyVersion   = (& $PYTHON --version 2>&1).ToString().Trim()
    $nodeVersion = "N/A"
    try { $nodeVersion = (& node --version 2>&1).ToString().Trim() } catch {}

    $locustVer = ((& $PYTHON -m pip show locust 2>&1 | Select-String "^Version:").ToString() -replace "Version:\s*","").Trim()
    $httpxVer  = ((& $PYTHON -m pip show httpx  2>&1 | Select-String "^Version:").ToString() -replace "Version:\s*","").Trim()

    $dbSizeKB = [math]::Round((Get-Item $DB_PATH).Length / 1KB, 1)

    # DB row 수 (서버 꺼진 상태 — 직접 SELECT)
    $tmpPy_dbcount = Join-Path $script:runDir "phase1_dbcount_tmp.py"
    $dbCountCode = @"
import sqlite3
c = sqlite3.connect('$($DB_PATH -replace '\\','/')')
tables = ['users','events','checklists','notifications']
for t in tables:
    try:
        n = c.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
        print(f'{t}={n}')
    except:
        print(f'{t}=N/A')
c.close()
"@
    Set-Content -Path $tmpPy_dbcount -Value $dbCountCode -Encoding utf8
    $dbCounts = & $PYTHON $tmpPy_dbcount 2>&1

    # meetings/ 사용량
    $meetingsDir = "$REPO_ROOT\meetings"
    $meetingsFiles = 0
    $meetingsSizeKB = 0
    if (Test-Path $meetingsDir) {
        $meetingsItems = Get-ChildItem $meetingsDir -Recurse -File -ErrorAction SilentlyContinue
        $meetingsFiles = $meetingsItems.Count
        $meetingsSizeKB = [math]::Round(($meetingsItems | Measure-Object Length -Sum).Sum / 1KB, 1)
    }

    $startTime = Get-Date -Format "yyyy-MM-dd HH:mm:ss"

    $metaContent = @"
# 환경 메타데이터 — M1a-7 baseline

측정 시작: $startTime
run 디렉터리: $($script:runDir)

## 시스템

| 항목 | 값 |
|------|-----|
| OS | $($osInfo.Caption) $($osInfo.Version) |
| CPU | $($cpuInfo.Name) |
| RAM | $ramTotalGB GB |
| Python | $pyVersion |
| Node | $nodeVersion |
| locust | $locustVer |
| httpx | $httpxVer |

## DB 상태 (측정 시작 전)

| 항목 | 값 |
|------|-----|
| whatudoin.db 크기 | $dbSizeKB KB |
$($dbCounts | ForEach-Object { "| $_ |" } | Out-String)

## 첨부 디렉터리 (meetings/)

| 항목 | 값 |
|------|-----|
| 파일 수 | $meetingsFiles |
| 사용량 | $meetingsSizeKB KB |

## 측정 환경 정책

| 항목 | 상태 |
|------|------|
| server-locust 동거 | 동일 호스트 (localhost). 서버 CPU/메모리 경합 포함됨 |
| locust host | $HTTPS_HOST (자체 서명 TLS) |
| SSE 분리 측정 | Phase 6에서 50 VU와 병렬 (M1a-6 sse_keepalive.py) |
| sanity run | 1 VU × 30s, WU_PERF_RESTRICT_HEAVY=true |
| 본 측정 단계 | 1/5/10 VU (RESTRICT_HEAVY=true) → 25/50 VU (해제) |
"@

    $metaContent | Out-File -FilePath $metaFile -Encoding utf8
    Write-OK "환경 메타데이터 기록: $metaFile"

    # ─────────────────────────────────────────────────────────────────────────
    # Phase 2: snapshot + seed
    # ─────────────────────────────────────────────────────────────────────────
    Write-Phase "Phase 2: DB snapshot + seed"

    # snapshot 실행 (WHATUDOIN_PERF_BASELINE_DIR으로 run 디렉터리에 저장)
    $env:WHATUDOIN_PERF_FIXTURE     = "allow"
    $env:WHATUDOIN_DB_PATH          = $DB_PATH
    $env:WHATUDOIN_PERF_BASELINE_DIR= $script:runDir
    $snapOutput = & $PYTHON $SNAPSHOT_SCRIPT 2>&1
    $snapExit   = $LASTEXITCODE
    Remove-Item Env:WHATUDOIN_PERF_BASELINE_DIR -ErrorAction SilentlyContinue
    Write-Host ($snapOutput -join "`n")
    if ($snapExit -ne 0) { Write-ABORT "snapshot_db.py 실패 (exit $snapExit)" }
    Write-OK "snapshot 완료"

    # snapshot 해시 검증 (source vs 복사본)
    $snapDir    = Get-ChildItem $script:runDir -Directory | Where-Object { $_.Name -like "db_snapshot*" } | Select-Object -First 1
    if ($snapDir) {
        $snapDbFile = Join-Path $snapDir.FullName "whatudoin.db"
        if (Test-Path $snapDbFile) {
            $srcHash  = (Get-FileHash $DB_PATH     -Algorithm SHA256).Hash
            $dstHash  = (Get-FileHash $snapDbFile  -Algorithm SHA256).Hash
            if ($srcHash -eq $dstHash) {
                Write-OK "snapshot SHA256 해시 일치: $srcHash"
            } else {
                Write-WARN "snapshot 해시 불일치 (src: $srcHash / dst: $dstHash). 계속 진행하나 주의 필요."
            }
            # 메타데이터에 해시 기록
            "| snapshot SHA256 | $dstHash |" | Add-Content $metaFile -Encoding utf8
        }
    } else {
        Write-WARN "snapshot 디렉터리를 찾을 수 없음. 해시 검증 생략."
    }

    # seed 실행
    $seedOutput = & $PYTHON $SEED_SCRIPT 2>&1
    $seedExit   = $LASTEXITCODE
    Write-Host ($seedOutput -join "`n")
    if ($seedExit -ne 0) { Write-ABORT "seed_users.py 실패 (exit $seedExit)" }

    # seed 검증: users, sessions = 50, session_cookies.json key 수 = 50
    $tmpPy_seedverify = Join-Path $script:runDir "phase2_seed_verify_tmp.py"
    $seedVerifyCode = @"
import sqlite3, json
from pathlib import Path
c = sqlite3.connect('$($DB_PATH -replace '\\','/')')
u = c.execute("SELECT COUNT(*) FROM users    WHERE name  LIKE 'test_perf_%'").fetchone()[0]
s = c.execute("SELECT COUNT(*) FROM sessions WHERE user_id IN (SELECT id FROM users WHERE name LIKE 'test_perf_%')").fetchone()[0]
c.close()
cookies_path = Path('$($COOKIES_JSON -replace '\\','/')')
ck = len(json.load(open(cookies_path))) if cookies_path.exists() else -1
print(f'{u},{s},{ck}')
"@
    Set-Content -Path $tmpPy_seedverify -Value $seedVerifyCode -Encoding utf8
    $seedVerify = (& $PYTHON $tmpPy_seedverify 2>&1).Trim()
    if ($LASTEXITCODE -eq 0) {
        $sv = $seedVerify -split ","
        $uCount  = [int]$sv[0]
        $sCount  = [int]$sv[1]
        $ckCount = [int]$sv[2]
        Write-INFO "seed 검증 — users: $uCount / sessions: $sCount / cookies.json keys: $ckCount"
        if ($uCount -ne 50)  { Write-ABORT "seed 검증 실패 — test_perf_ users = $uCount (기대값 50)" }
        if ($sCount -ne 50)  { Write-ABORT "seed 검증 실패 — test_perf_ sessions = $sCount (기대값 50)" }
        if ($ckCount -ne 50) { Write-ABORT "seed 검증 실패 — session_cookies.json keys = $ckCount (기대값 50)" }
        Write-OK "seed 검증 통과 (users=50, sessions=50, cookies=50)"
    } else {
        Write-ABORT "seed 검증 SELECT 실패"
    }

    $script:seedDone = $true

    # ─────────────────────────────────────────────────────────────────────────
    # Phase 3: 서버 시작 + readiness wait
    # ─────────────────────────────────────────────────────────────────────────
    Write-Phase "Phase 3: uvicorn 서버 시작 + readiness wait"

    $stdoutLog = "$($script:runDir)\server_stdout.log"
    $stderrLog = "$($script:runDir)\server_stderr.log"

    $procArgs = @(
        "-m", "uvicorn", "app:app",
        "--host", "0.0.0.0",
        "--port", "8443",
        "--ssl-certfile", "whatudoin-cert.pem",
        "--ssl-keyfile", "whatudoin-key.pem",
        "--log-level", "warning"
    )

    Write-INFO "uvicorn 시작: $PYTHON $($procArgs -join ' ')"
    $script:proc = Start-Process `
        -FilePath $PYTHON `
        -ArgumentList $procArgs `
        -WorkingDirectory $REPO_ROOT `
        -RedirectStandardOutput $stdoutLog `
        -RedirectStandardError  $stderrLog `
        -PassThru

    Write-INFO "서버 PID: $($script:proc.Id)"
    $script:serverStarted = $true

    # readiness wait — 30초 폴링, 2초 간격
    # PS 5.1은 Invoke-WebRequest -SkipCertificateCheck 미지원. httpx Python 프로브 사용.
    Write-INFO "readiness wait 시작 (최대 30초)..."
    $ready      = $false
    $waitStart  = Get-Date
    $maxWaitSec = 30

    # 프로브 스크립트 루프 밖에서 한 번만 생성
    $probeScript = [System.IO.Path]::GetTempFileName() -replace '\.tmp$', '.py'
    @'
import httpx, sys
try:
    r = httpx.get("https://localhost:8443/api/notifications/count", verify=False, timeout=3.0)
    sys.exit(0 if r.status_code == 200 else 1)
except Exception:
    sys.exit(1)
'@ | Out-File -FilePath $probeScript -Encoding utf8

    try {
        while (((Get-Date) - $waitStart).TotalSeconds -lt $maxWaitSec) {
            Start-Sleep -Seconds 2

            # 서버 프로세스 조기 종료 감지
            if ($script:proc.HasExited) {
                Write-ABORT "uvicorn 프로세스가 예기치 않게 종료됨 (exit $($script:proc.ExitCode)). server_stderr.log 확인: $stderrLog"
            }

            $null = & $PYTHON $probeScript
            if ($LASTEXITCODE -eq 0) {
                $ready = $true
                break
            }
        }
    } finally {
        Remove-Item $probeScript -Force -ErrorAction SilentlyContinue
    }

    if (-not $ready) {
        Write-ABORT "서버 readiness timeout (${maxWaitSec}초). server_stderr.log 확인: $stderrLog"
    }

    $elapsedSec = [math]::Round(((Get-Date) - $waitStart).TotalSeconds, 1)
    Write-OK "서버 준비 완료 (${elapsedSec}초 소요)"

    # ─────────────────────────────────────────────────────────────────────────
    # Phase 4: sanity run
    # ─────────────────────────────────────────────────────────────────────────
    Write-Phase "Phase 4: sanity run (1 VU × 30s)"

    $env:WU_PERF_RESTRICT_HEAVY = "true"

    $sanityPrefix = "$($script:runDir)\sanity_locust"
    Write-INFO "locust sanity run 시작..."
    & $LOCUST_CMD `
        -f $LOCUSTFILE `
        --host $HTTPS_HOST `
        --headless `
        --users 1 `
        --spawn-rate 1 `
        -t 30s `
        --csv $sanityPrefix `
        --csv-full-history 2>&1 | Write-Host

    if ($LASTEXITCODE -ne 0) {
        Write-WARN "locust sanity run 비정상 종료 (exit $LASTEXITCODE). CSV 결과로 판정 시도..."
    }

    Test-SanityGate -CsvPrefix $sanityPrefix
    Write-OK "Phase 4 sanity 통과 — 본 측정 진입"

    # ─────────────────────────────────────────────────────────────────────────
    # Phase 5: 본 측정 5단계
    # ─────────────────────────────────────────────────────────────────────────
    Write-Phase "Phase 5: 본 측정 5단계 (1/5/10/25/50 VU)"

    $stages = @(
        @{ vu = 1;  rate = 1;  heavy = $true;  label = "vu1"  },
        @{ vu = 5;  rate = 5;  heavy = $true;  label = "vu5"  },
        @{ vu = 10; rate = 10; heavy = $true;  label = "vu10" },
        @{ vu = 25; rate = 25; heavy = $false; label = "vu25" },
        @{ vu = 50; rate = 50; heavy = $false; label = "vu50" }
    )

    foreach ($stage in $stages) {
        $isLast = ($stage.label -eq "vu50")

        if ($stage.heavy) {
            $env:WU_PERF_RESTRICT_HEAVY = "true"
        } else {
            Remove-Item Env:WU_PERF_RESTRICT_HEAVY -ErrorAction SilentlyContinue
        }

        $csvPrefix = "$($script:runDir)\locust_$($stage.label)"
        Write-INFO "단계 $($stage.label): $($stage.vu) VU, spawn-rate $($stage.rate), 60s, RESTRICT_HEAVY=$($stage.heavy)"

        if ($isLast) {
            # Phase 6: 50 VU 직전에 SSE keep-alive 병렬 시작
            Write-Phase "Phase 6: SSE keep-alive 병렬 시작 (50 VU와 동시)"
            # SSE duration을 65s로 설정 (locust 60s + 5s 버퍼 — 프로세스 시작 오버헤드 흡수)
            # --output-dir로 run 디렉터리 지정 → sse_keepalive_<HHMMSS>.csv 자동 생성
            $script:sseProc = Start-Process `
                -FilePath $PYTHON `
                -ArgumentList @(
                    $SSE_SCRIPT,
                    "--n", "50",
                    "--host", $HTTPS_HOST,
                    "--duration", "65",
                    "--output-dir", $script:runDir
                ) `
                -WorkingDirectory $REPO_ROOT `
                -PassThru
            Write-INFO "SSE keep-alive PID: $($script:sseProc.Id)"
        }

        & $LOCUST_CMD `
            -f $LOCUSTFILE `
            --host $HTTPS_HOST `
            --headless `
            --users $stage.vu `
            --spawn-rate $stage.rate `
            -t 60s `
            --csv $csvPrefix `
            --csv-full-history 2>&1 | Write-Host

        $locustExit = $LASTEXITCODE
        if ($locustExit -ne 0) {
            Write-WARN "locust $($stage.label) 비정상 종료 (exit $locustExit). 계속 진행."
        }

        $stageStats = Get-LocustStats -CsvPrefix $csvPrefix
        Write-INFO "  [$($stage.label)] p95=$($stageStats.p95)ms / p99=$($stageStats.p99)ms / 실패율=$($stageStats.failRate) / RPS=$($stageStats.rps)"

        if (-not $isLast) {
            Write-INFO "단계 간 안정화 5초 대기..."
            Start-Sleep -Seconds 5
        }
    }

    # SSE 프로세스 대기 (최대 90초)
    if ($script:sseProc -and -not $script:sseProc.HasExited) {
        Write-INFO "SSE 프로세스 종료 대기 (최대 90초)..."
        $script:sseProc.WaitForExit(90000) | Out-Null
        if (-not $script:sseProc.HasExited) {
            Write-WARN "SSE 프로세스 강제 종료 (90초 초과)"
            Stop-Process -Id $script:sseProc.Id -Force -ErrorAction SilentlyContinue
        } else {
            Write-OK "SSE keep-alive 정상 종료"
        }
    }

} finally {
    # ─────────────────────────────────────────────────────────────────────────
    # Phase 7 + Phase 8: 항상 실행 (try/finally)
    # ─────────────────────────────────────────────────────────────────────────
    if ($script:serverStarted) {
        Invoke-ServerShutdown
    }

    Remove-Item Env:WU_PERF_RESTRICT_HEAVY -ErrorAction SilentlyContinue
    Remove-Item Env:WHATUDOIN_PERF_FIXTURE  -ErrorAction SilentlyContinue
    Remove-Item Env:WHATUDOIN_DB_PATH       -ErrorAction SilentlyContinue

    if ($script:seedDone) {
        Invoke-Cleanup
    }
}

# ─────────────────────────────────────────────────────────────────────────────
# Phase 9: 결과 요약 생성
# ─────────────────────────────────────────────────────────────────────────────
Write-Phase "Phase 9: 결과 요약 생성"

if (-not $script:runDir -or -not (Test-Path $script:runDir)) {
    Write-WARN "run 디렉터리 없음 — 요약 생략"
    exit 0
}

$summaryFile = "$($script:runDir)\summary.md"

# 각 단계 지표 수집
$stageLabels = @("vu1","vu5","vu10","vu25","vu50")
$stageTable  = @()
foreach ($lbl in $stageLabels) {
    $prefix = "$($script:runDir)\locust_$lbl"
    $st     = Get-LocustStats -CsvPrefix $prefix
    $stageTable += "| $lbl | $($st.p95) | $($st.p99) | $($st.failRate) | $($st.rps) |"
}

# sanity 결과
$sanityStats = Get-LocustStats -CsvPrefix "$($script:runDir)\sanity_locust"

# SSE 지표 (sse_keepalive_<HHMMSS>.csv 패턴으로 탐색)
$sseOutCsv  = $null
$sseCsvFiles = Get-ChildItem $script:runDir -Filter "sse_keepalive_*.csv" -ErrorAction SilentlyContinue
if ($sseCsvFiles) { $sseOutCsv = $sseCsvFiles | Sort-Object LastWriteTime | Select-Object -Last 1 -ExpandProperty FullName }
$sseSummary = "N/A (측정 데이터 없음)"
if ($sseOutCsv -and (Test-Path $sseOutCsv)) {
    $sseRows = Import-Csv $sseOutCsv
    $totalSse  = $sseRows.Count
    $okSse     = ($sseRows | Where-Object { $_.connected -eq "True" -and $_.disconnected_early -eq "False" }).Count
    $earlyDisc = ($sseRows | Where-Object { $_.disconnected_early -eq "True" }).Count
    $allIaP95  = ($sseRows | Where-Object { $_.ia_p95_ms -ne "" -and $_.ia_p95_ms -ne "0" } |
        ForEach-Object { [double]$_.ia_p95_ms } | Sort-Object |
        Select-Object -Last 1)
    $sseSummary = "연결 성공 $okSse/$totalSse | 조기 끊김 $earlyDisc | inter-arrival p95 $allIaP95 ms"
}

# snapshot 해시
$snapHash = "N/A"
if ($metaFile -and (Test-Path $metaFile)) {
    $hashLine = Select-String -Path $metaFile -Pattern "snapshot SHA256" | Select-Object -First 1
    if ($hashLine) { $snapHash = $hashLine.Line }
}

$summaryContent = @"
# M1a-7 baseline 측정 요약

생성 일시: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')
run 디렉터리: $($script:runDir)

## advisor 가드 검증 결과

| 항목 | 결과 |
|------|------|
| snapshot SHA256 | $snapHash |
| seed users/sessions | 50 / 50 |
| cleanup 검증 | Phase 8 로그 참조 |
| sanity 통과 | p95=$($sanityStats.p95)ms / failRate=$($sanityStats.failRate) |

## 단계별 지표

| 단계 | p95 (ms) | p99 (ms) | 실패율 | RPS |
|------|---------|---------|--------|-----|
$($stageTable -join "`n")

## SSE 지표 3종 (Phase 6)

$sseSummary

> [한계] broker.py server-side timestamp 없음. inter-arrival 값의 대부분은 ~25s(ping 주기).
> 실제 이벤트 latency는 M1c-10 QueueFull 카운터 도입 후 정확 측정 가능.

## 연결 파일

- 환경 메타데이터: [environment_metadata.md](environment_metadata.md)
- 서버 로그: [server_stderr.log](server_stderr.log)
- sanity CSV: sanity_locust_stats.csv

## 비고

- upload_file 실패율은 PIL.verify 한계로 높을 수 있음 (예상된 실패)
- ai_parse 실패율은 Ollama 응답 시간에 따라 가변
- server-locust 동거 환경 → p95에 측정 서버 자체 부하 포함
"@

$summaryContent | Out-File -FilePath $summaryFile -Encoding utf8
Write-OK "결과 요약 생성: $summaryFile"

Write-Host ""
Write-Host "╔═══════════════════════════════════════════════════════╗" -ForegroundColor Green
Write-Host "║  M1a-7 baseline 측정 완료                             ║" -ForegroundColor Green
Write-Host "║  결과: $($script:runDir)" -ForegroundColor Green
Write-Host "╚═══════════════════════════════════════════════════════╝" -ForegroundColor Green
Write-Host ""
Write-Host "다음 단계:" -ForegroundColor Cyan
Write-Host "  1. $summaryFile 검토" -ForegroundColor Cyan
Write-Host "  2. 단계별 locust_vu*.csv p95/실패율 분석" -ForegroundColor Cyan
Write-Host "  3. sse_keepalive.csv SSE 지표 3종 확인" -ForegroundColor Cyan
