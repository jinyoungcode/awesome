@echo off
REM 자료팀 일일 수집 (윈도우). 저장소 루트에서 실행: teams\02-data\collectors\run_daily.bat
REM 1) S02 입력 파일 4종 (오늘 날짜)  2) 국내 1분봉 당일 적재
REM 데이터는 %USERPROFILE%\stockking-data 아래에만 저장 (저장소 커밋 금지)
setlocal
chcp 65001 >nul
set PYTHONUTF8=1
if "%STOCKKING_DATA_DIR%"=="" set STOCKKING_DATA_DIR=%USERPROFILE%\stockking-data
for /f %%d in ('python -c "import datetime;print(datetime.date.today())"') do set TODAY=%%d
set CONS=%STOCKKING_DATA_DIR%\constituents
if not exist "%STOCKKING_DATA_DIR%\logs" mkdir "%STOCKKING_DATA_DIR%\logs"

echo [%TIME%] S02 입력 파일 생성 시작 (%TODAY%)
python teams\02-data\collectors\collect.py s02-inputs --kospi200 "%CONS%\kospi200.csv" --kosdaq150 "%CONS%\kosdaq150.csv" --out "%STOCKKING_DATA_DIR%\s02_inputs\%TODAY%" >> "%STOCKKING_DATA_DIR%\logs\s02_%TODAY%.log" 2>&1
if errorlevel 1 (echo S02 입력 파일 생성 실패 - 로그 확인: %STOCKKING_DATA_DIR%\logs\s02_%TODAY%.log) else (echo S02 입력 파일 완료: %STOCKKING_DATA_DIR%\s02_inputs\%TODAY%)

echo [%TIME%] 국내 분봉 적재 시작
python teams\02-data\collectors\collect.py minute --kospi200 "%CONS%\kospi200.csv" --kosdaq150 "%CONS%\kosdaq150.csv" >> "%STOCKKING_DATA_DIR%\logs\minute_%TODAY%.log" 2>&1
if errorlevel 1 (echo 분봉 적재 실패 - 로그 확인) else (echo 분봉 적재 완료)
endlocal
