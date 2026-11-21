# Inicia o servidor do PetAgenda em segundo plano (se ainda nao estiver rodando) e abre o navegador.
param([switch]$SemNavegador)
$ErrorActionPreference = 'Stop'
$Dir = Split-Path -Parent $MyInvocation.MyCommand.Path

try {
    $cfg = Get-Content (Join-Path $Dir 'instalacao.json') -Raw | ConvertFrom-Json
} catch {
    Add-Type -AssemblyName PresentationFramework
    [void][Windows.MessageBox]::Show('Instalacao danificada. Rode o INSTALAR do PetAgenda novamente.', 'PetAgenda', 'OK', 'Error')
    exit 1
}

# porta configurada no petagenda.env (padrao 8080)
$porta = 8080
$envArq = Join-Path $Dir 'petagenda.env'
if (Test-Path $envArq) {
    foreach ($l in Get-Content $envArq) {
        if ($l -match '^\s*PORT\s*=\s*(\d+)\s*$') { $porta = [int]$Matches[1] }
    }
}

function Servidor-Ativo {
    try {
        $c = New-Object Net.Sockets.TcpClient
        $r = $c.BeginConnect('127.0.0.1', $porta, $null, $null)
        $ok = $r.AsyncWaitHandle.WaitOne(400)
        if ($ok) { $c.EndConnect($r) }
        $c.Close()
        return $ok
    } catch { return $false }
}

if (-not (Servidor-Ativo)) {
    $logs = Join-Path $Dir 'logs'
    New-Item -ItemType Directory -Force -Path $logs | Out-Null
    $erroArq = Join-Path $logs 'erros.log'
    $env:PYTHONUNBUFFERED = '1'
    $env:PYTHONIOENCODING = 'utf-8'
    Start-Process -FilePath $cfg.python -ArgumentList 'server.py' -WorkingDirectory $Dir -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logs 'servidor.log') -RedirectStandardError $erroArq
    for ($i = 0; $i -lt 40 -and -not (Servidor-Ativo); $i++) { Start-Sleep -Milliseconds 250 }

    if (-not (Servidor-Ativo)) {
        $detalhe = ''
        if (Test-Path $erroArq) { $detalhe = (Get-Content $erroArq -Tail 6) -join "`n" }
        Add-Type -AssemblyName PresentationFramework
        [void][Windows.MessageBox]::Show(
            "O PetAgenda nao iniciou. A porta $porta pode estar em uso por outro programa (troque PORT em petagenda.env).`n`n$detalhe",
            'PetAgenda', 'OK', 'Error')
        exit 1
    }
}

if (-not $SemNavegador) { Start-Process "http://localhost:$porta/" }
