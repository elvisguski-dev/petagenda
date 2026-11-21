# Desinstalador do PetAgenda. Chamado por "Aplicativos instalados" do Windows.
$ErrorActionPreference = 'Continue'
$Dest  = Split-Path -Parent $MyInvocation.MyCommand.Path
$Chave = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\PetAgenda'

Add-Type -AssemblyName PresentationFramework
$r = [Windows.MessageBox]::Show('Remover o PetAgenda deste computador?', 'Desinstalar PetAgenda', 'YesNo', 'Question')
if ($r -ne 'Yes') { exit 0 }

# 1) parar o servidor
$porta = 8080
$e = Join-Path $Dest 'petagenda.env'
if (Test-Path $e) { foreach ($l in Get-Content $e) { if ($l -match '^\s*PORT\s*=\s*(\d+)') { $porta = [int]$Matches[1] } } }
foreach ($c in @(Get-NetTCPConnection -LocalPort $porta -State Listen -ErrorAction SilentlyContinue)) {
    $p = Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue
    if ($p -and $p.ProcessName -like 'python*') { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue }
}
Start-Sleep -Seconds 1

# 2) dados dos clientes: manter uma copia?
$guardar = [Windows.MessageBox]::Show(
    "Guardar uma copia dos seus dados (clientes, pets e agenda) em Documentos\PetAgenda-backup?`n`nSim = guarda a copia   |   Nao = apaga tudo",
    'Desinstalar PetAgenda', 'YesNo', 'Question')
if ($guardar -eq 'Yes') {
    $bk = Join-Path ([Environment]::GetFolderPath('MyDocuments')) ('PetAgenda-backup\' + (Get-Date -Format 'yyyy-MM-dd_HH-mm'))
    New-Item -ItemType Directory -Force -Path $bk | Out-Null
    foreach ($f in 'petagenda.db', 'petagenda.db-wal', 'petagenda.db-shm', 'petagenda.env') {
        $o = Join-Path $Dest $f
        if (Test-Path $o) { Copy-Item $o $bk -Force }
    }
}

# 3) atalhos e registro
$lnks = @(
    (Join-Path ([Environment]::GetFolderPath('Desktop')) 'PetAgenda.lnk'),
    (Join-Path ([Environment]::GetFolderPath('Programs')) 'PetAgenda.lnk'),
    (Join-Path ([Environment]::GetFolderPath('Startup')) 'PetAgenda (servidor).lnk')
)
foreach ($l in $lnks) { if (Test-Path $l) { Remove-Item $l -Force -ErrorAction SilentlyContinue } }
if (Test-Path $Chave) { Remove-Item $Chave -Recurse -Force -ErrorAction SilentlyContinue }

# 4) apagar a pasta (depois que este script terminar, pois ele mesmo esta dentro dela)
Start-Process cmd.exe -ArgumentList "/c ping -n 3 127.0.0.1 >nul & rmdir /s /q `"$Dest`"" -WindowStyle Hidden
$msg = 'PetAgenda removido.'
if ($guardar -eq 'Yes') { $msg += "`nSua copia dos dados esta em Documentos\PetAgenda-backup." }
[void][Windows.MessageBox]::Show($msg, 'PetAgenda', 'OK', 'Information')
