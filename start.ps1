param([ValidateSet('all','backend','frontend','test')][string]$Mode = 'all')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$projectPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $projectPython)) {
    throw 'Create the virtual environment and install requirements first. See README.md.'
}
switch ($Mode) {
    'all' {
        $projectLogs = Join-Path $PSScriptRoot 'data\logs'
        New-Item -ItemType Directory -Path $projectLogs -Force | Out-Null
        function Test-ProjectService([string]$Address) {
            try {
                $response = Invoke-WebRequest -Uri $Address -UseBasicParsing -TimeoutSec 2
                return $response.StatusCode -eq 200
            } catch { return $false }
        }
        $services = @(
            @{Name='backend'; Health='http://127.0.0.1:8011/health'; Args=@('-m','uvicorn','backend.main:app','--host','127.0.0.1','--port','8011','--workers','1')},
            @{Name='frontend'; Health='http://127.0.0.1:8511/_stcore/health'; Args=@('-m','streamlit','run','frontend/app.py','--server.address','127.0.0.1','--server.port','8511','--server.headless','true','--browser.gatherUsageStats','false')}
        )
        foreach ($service in $services) {
            if (-not (Test-ProjectService $service.Health)) {
                $process = Start-Process -FilePath $projectPython -ArgumentList $service.Args -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $projectLogs ($service.Name + '.out.log')) -RedirectStandardError (Join-Path $projectLogs ($service.Name + '.err.log'))
                $ready = $false
                for ($attempt = 0; $attempt -lt 40; $attempt++) {
                    if (Test-ProjectService $service.Health) { $ready = $true; break }
                    if ($process.HasExited) { break }
                    Start-Sleep -Milliseconds 500
                }
                if (-not $ready) {
                    throw ($service.Name + ' startup failed. Check logs: ' + $projectLogs)
                }
            }
        }
        Write-Host 'AfterSales Copilot: http://127.0.0.1:8511'
        Write-Host ('Logs: ' + $projectLogs)
    }
    'backend' { & $projectPython -m uvicorn backend.main:app --host 127.0.0.1 --port 8011 --workers 1 }
    'frontend' { & $projectPython -m streamlit run frontend/app.py --server.address 127.0.0.1 --server.port 8511 --server.headless true }
    'test' { & $projectPython -m pytest -q }
}
