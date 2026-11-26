#Requires -Version 5.1
<#
  나는야 주식왕 — 3일 모의투자 하루 일정 실행기 (모의투자 전용)
  실행 예:
      powershell -ExecutionPolicy Bypass -File scripts\paper_day.ps1 -Step check     # 연결 점검(주문 없음)
      powershell -ExecutionPolicy Bypass -File scripts\paper_day.ps1 -Step open      # 08:25 장 시작 전
      powershell -ExecutionPolicy Bypass -File scripts\paper_day.ps1 -Step drill     # 1일차 장중 훈련
      powershell -ExecutionPolicy Bypass -File scripts\paper_day.ps1 -Step cancel    # 15:15
      powershell -ExecutionPolicy Bypass -File scripts\paper_day.ps1 -Step close     # 15:45 장 마감 후
      powershell -ExecutionPolicy Bypass -File scripts\paper_day.ps1 -Step plan      # 18:30 (자료팀 파일 필요)
  모의 운용 금액: 기본 auto(D-046). 직접 정하려면 어느 단계든  -OperatingCapital 1000000  (모의 예수금 이하, D-044)
  기록: var\logs\paper_day_<날짜>_<단계>.log
#>
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('check', 'open', 'drill', 'cancel', 'close', 'plan')]
    [string]$Step,
    [string]$Operator = '대표',
    [string]$DataDir,
    [string]$SignalDate,
    [string]$ExecDate,
    [string]$OperatingCapital = 'auto',   # D-046 기본 auto(1일차 주문 가능 금액 전액 고정). 숫자로 직접 지정 가능(D-044)
    [switch]$PromptKeys
)
$ErrorActionPreference = 'Continue'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
$env:PYTHONUTF8 = '1'
if ($OperatingCapital) { $env:STOCKKING_OPERATING_CAPITAL = $OperatingCapital }
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot
$vpy = Join-Path $RepoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $vpy)) {
    Write-Host '가상환경이 없습니다. 먼저 scripts\setup_windows.ps1 을 실행하세요.'
    exit 2
}
New-Item -ItemType Directory -Force -Path 'var\logs', 'var\s02' | Out-Null
$today = Get-Date -Format 'yyyyMMdd'
$log = "var\logs\paper_day_{0}_{1}.log" -f $today, $Step
Start-Transcript -Path $log -Append | Out-Null
$script:Failures = New-Object System.Collections.ArrayList
$lastPlanFile = 'var\s02\last_plan.json'

function Invoke-Step([string]$Title, [string[]]$PyArgs, [switch]$Kis, [switch]$StopOnFail) {
    $argList = @('-m') + $PyArgs
    if ($Kis -and $PromptKeys) { $argList += '--prompt-keys' }
    Write-Host ''
    Write-Host ("=== {0} ===" -f $Title)
    & $vpy @argList
    $code = $LASTEXITCODE
    if ($code -ne 0) {
        [void]$script:Failures.Add(("{0} (종료코드 {1})" -f $Title, $code))
        if ($StopOnFail) {
            Write-Host ("중단: '{0}' 실패 — 이후 단계를 실행하지 않습니다. 원인 확인 후 다시 실행하세요." -f $Title)
            Finish 1
        }
    }
}

function Finish([int]$Code) {
    Write-Host ''
    if ($script:Failures.Count -eq 0 -and $Code -eq 0) {
        Write-Host ("== [{0}] 모두 정상 완료 ==  기록: {1}" -f $Step, $log)
    } else {
        Write-Host ("== [{0}] 확인 필요 {1}건 ==  기록: {2}" -f $Step, $script:Failures.Count, $log)
        foreach ($f in $script:Failures) { Write-Host ("  - {0}" -f $f) }
        if ($Code -eq 0) { $Code = 1 }
    }
    Stop-Transcript | Out-Null
    exit $Code
}

function Get-LastPlan {
    if (Test-Path $lastPlanFile) { return (Get-Content $lastPlanFile -Raw | ConvertFrom-Json) }
    return $null
}

function Get-InputArgs($Plan) {
    return @('--bars', (Join-Path $Plan.data_dir 'bars.csv'), '--universe', (Join-Path $Plan.data_dir 'universe.txt'),
             '--markets', (Join-Path $Plan.data_dir 'markets.csv'), '--index', (Join-Path $Plan.data_dir 'index.csv'),
             '--signal-date', $Plan.signal_date, '--exec-date', $Plan.exec_date)
}

switch ($Step) {
    'check' {
        Invoke-Step '연결 점검 (토큰·현재가·잔고, 주문 없음)' @('stockking.ops.connect_check') -Kis -StopOnFail
    }
    'open' {
        Invoke-Step '장부 시작점 저장' @('stockking.ops.ledger_close', '--open') -Kis -StopOnFail
        Invoke-Step '시작 전 점검 (킬스위치·미확인 주문)' @('stockking.ops.startup_check') -StopOnFail
        Invoke-Step '연결 점검' @('stockking.ops.connect_check') -Kis -StopOnFail
        $plan = Get-LastPlan
        if ($plan) {
            Invoke-Step ("S02 주문계획 제출 (신호일 {0})" -f $plan.signal_date) @('stockking.ops.s02_runner', 'submit', '--operator', $Operator, '--signal-date', $plan.signal_date) -Kis
        } else {
            Write-Host '주문계획이 없습니다(전날 -Step plan 미실행) — 제출 단계를 건너뜁니다.'
        }
    }
    'drill' {
        Invoke-Step '킬스위치 훈련 (실제 모의 계좌, 주문 0건)' @('stockking.ops.killswitch_drill', '--operator', $Operator, '--kis') -Kis
        Invoke-Step '1주 주문·취소 점검 (장중)' @('stockking.ops.connect_check', '--order') -Kis
        Invoke-Step '재시작 복구 훈련 (오프라인 + 기록)' @('stockking.ops.restart_drill', '--operator', $Operator)
        Write-Host ''
        Write-Host '※ 실제 재시작 실습은 PAPER_RUNBOOK §2 를 따라 한 번 더 해 주세요.'
    }
    'cancel' {
        Invoke-Step '지정가(LDAY) 잔량 취소' @('stockking.ops.s02_runner', 'cancel', '--operator', $Operator) -Kis
    }
    'close' {
        Invoke-Step '체결 → 가상 장부 반영' @('stockking.ops.s02_runner', 'sync') -Kis
        Invoke-Step '회계 장부 작성·잔고 대사 (C1)' @('stockking.ops.ledger_close', '--close') -Kis
        Invoke-Step '주문 내역 대사·중복 (C3)' @('stockking.ops.daily_reconcile') -Kis
        $plan = Get-LastPlan
        if ($plan) {
            Invoke-Step '신호 일치 (C2)' (@('stockking.ops.s02_runner', 'verify') + (Get-InputArgs $plan))
        }
        Invoke-Step '3일 점검표 C1~C6' @('stockking.ops.paper_report')
    }
    'plan' {
        if (-not $SignalDate) { $SignalDate = Get-Date -Format 'yyyy-MM-dd' }
        if (-not $DataDir) { $DataDir = Join-Path 'data' ($SignalDate -replace '-', '') }
        if (-not $ExecDate) {
            $d = [datetime]::ParseExact($SignalDate, 'yyyy-MM-dd', $null).AddDays(1)
            while ($d.DayOfWeek -eq 'Saturday' -or $d.DayOfWeek -eq 'Sunday') { $d = $d.AddDays(1) }
            $ExecDate = $d.ToString('yyyy-MM-dd')
            Write-Host ("다음 거래일을 {0} 로 잡았습니다(공휴일이면 -ExecDate 로 지정)." -f $ExecDate)
        }
        $missing = @('bars.csv', 'universe.txt', 'markets.csv', 'index.csv') | Where-Object { -not (Test-Path (Join-Path $DataDir $_)) }
        if ($missing) {
            Write-Host ("자료팀 입력 파일이 없습니다: {0} 안의 {1}" -f $DataDir, ($missing -join ', '))
            [void]$script:Failures.Add('입력 파일 없음')
            Finish 1
        }
        $plan = [pscustomobject]@{ signal_date = $SignalDate; exec_date = $ExecDate; data_dir = $DataDir }
        Invoke-Step ("S02 주문계획 (신호일 {0} → 실행일 {1})" -f $SignalDate, $ExecDate) (@('stockking.ops.s02_runner', 'plan') + (Get-InputArgs $plan)) -StopOnFail
        $plan | ConvertTo-Json | Set-Content -Path $lastPlanFile -Encoding UTF8
    }
}
Finish 0
