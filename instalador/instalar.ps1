# ======================================================================
#  Instalador do PetAgenda (Windows 10/11) - nao precisa de administrador
#  Instala em %LOCALAPPDATA%\PetAgenda, cria atalhos e a entrada em
#  "Aplicativos instalados". Rodar de novo ATUALIZA sem perder seus dados.
# ======================================================================
$ErrorActionPreference = 'Stop'
$Versao = '1.1'
$Origem = Split-Path -Parent $MyInvocation.MyCommand.Path       # pasta "instalador"
$Raiz   = Split-Path -Parent $Origem                            # pasta com server.py
$Dest   = Join-Path $env:LOCALAPPDATA 'PetAgenda'
$Chave  = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\PetAgenda'
$PS     = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'

function Titulo($t) { Write-Host ''; Write-Host "== $t" -ForegroundColor Green }
function Falha($t)  { Write-Host ''; Write-Host "ERRO: $t" -ForegroundColor Red; exit 1 }

Clear-Host
Write-Host ''
Write-Host '  PetAgenda - instalador' -ForegroundColor Green
Write-Host '  Agenda de banho e tosa com mensagens automaticas no WhatsApp'
Write-Host ''

# --- arquivos do programa ------------------------------------------------
foreach ($f in 'server.py', 'index.html', 'agendar.html') {
    if (-not (Test-Path (Join-Path $Raiz $f))) {
        Falha "Nao encontrei '$f'. Extraia o ZIP inteiro antes de instalar (nao rode de dentro do ZIP)."
    }
}

# --- Python ----------------------------------------------------------------
function Testar-Python([string]$exe, [string[]]$pre) {
    try {
        $saida = & $exe @pre -c "import sys; print(sys.executable); print(sys.version_info[0]*100+sys.version_info[1])" 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $saida) { return $null }
        $l = @($saida)
        if ($l.Count -lt 2) { return $null }
        if ([int]$l[1] -lt 309) { return $null }          # precisa de Python 3.9 ou mais novo
        if (-not (Test-Path $l[0])) { return $null }
        return [string]$l[0]
    } catch { return $null }
}

function Achar-Python {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $p = Testar-Python 'py' @('-3'); if ($p) { return $p }
    }
    if (Get-Command python -ErrorAction SilentlyContinue) {
        $p = Testar-Python 'python' @(); if ($p) { return $p }
    }
    $pastas = @("$env:LOCALAPPDATA\Programs\Python", "$env:ProgramFiles\Python*", "${env:ProgramFiles(x86)}\Python*")
    foreach ($pasta in $pastas) {
        foreach ($exe in (Get-ChildItem -Path $pasta -Filter python.exe -Recurse -Depth 2 -ErrorAction SilentlyContinue)) {
            $p = Testar-Python $exe.FullName @(); if ($p) { return $p }
        }
    }
    return $null
}

Titulo '1/5  Procurando o Python'
$Python = Achar-Python
if (-not $Python) {
    Write-Host 'Python 3.9+ nao encontrado. Tentando instalar pelo winget...'
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        try {
            & winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements
        } catch { }
        $Python = Achar-Python
    }
}
if (-not $Python) {
    Start-Process 'https://www.python.org/downloads/windows/'
    Falha ("Nao consegui instalar o Python sozinho.`n" +
           "Baixe e instale em https://www.python.org/downloads/windows/ (a pagina foi aberta),`n" +
           "marque 'Add python.exe to PATH' no instalador e rode este INSTALAR de novo.")
}
Write-Host "OK: $Python"

# --- parar versao em execucao (atualizacao) --------------------------------
Titulo '2/5  Copiando os arquivos'
function Parar-Servidor([string]$pasta) {
    # encerra o Python que esta escutando na porta do PetAgenda (nao mexe em outros programas)
    $porta = 8080
    $e = Join-Path $pasta 'petagenda.env'
    if (Test-Path $e) {
        foreach ($l in Get-Content $e) { if ($l -match '^\s*PORT\s*=\s*(\d+)') { $porta = [int]$Matches[1] } }
    }
    $con = Get-NetTCPConnection -LocalPort $porta -State Listen -ErrorAction SilentlyContinue
    foreach ($c in @($con)) {
        $p = Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue
        if ($p -and $p.ProcessName -like 'python*') { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue }
    }
}
Parar-Servidor $Dest
Start-Sleep -Milliseconds 600

$atualizando = Test-Path (Join-Path $Dest 'server.py')
New-Item -ItemType Directory -Force -Path $Dest | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $Dest 'logs') | Out-Null
foreach ($f in 'server.py', 'index.html', 'agendar.html', 'README.md', 'LICENSE') {
    $src = Join-Path $Raiz $f
    if (Test-Path $src) { Copy-Item $src (Join-Path $Dest $f) -Force }
}
foreach ($f in 'abrir.ps1', 'desinstalar.ps1', 'petagenda.ico') {
    Copy-Item (Join-Path $Origem $f) (Join-Path $Dest $f) -Force
}
@{ python = $Python; versao = $Versao } | ConvertTo-Json | Set-Content -Path (Join-Path $Dest 'instalacao.json') -Encoding UTF8
Write-Host "OK: $Dest"

# --- configuracao (so na primeira instalacao) ------------------------------
Titulo '3/5  Configuracao'
$EnvArq = Join-Path $Dest 'petagenda.env'
if (Test-Path $EnvArq) {
    Write-Host 'Configuracao existente mantida (senha, porta e dados dos clientes preservados).'
} else {
    $porta = 8080
    $resp = Read-Host 'Porta do sistema [Enter = 8080]'
    if ($resp -match '^\d{2,5}$' -and [int]$resp -ge 1024 -and [int]$resp -le 65535) { $porta = [int]$resp }

    Write-Host ''
    Write-Host 'Defina uma SENHA para o painel (usuario: qualquer nome).'
    $sen = Read-Host 'Senha [Enter = sem senha]' -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sen)
    $senha = [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)

    $linhas = @('# Configuracao do PetAgenda (edite e reinicie o programa)',
                "PORT=$porta",
                '# 127.0.0.1 = so este computador. Use 0.0.0.0 para acessar de outros aparelhos da rede.',
                'HOST=127.0.0.1')
    if ($senha) { $linhas += "PAINEL_SENHA=$senha" }
    # UTF-8 sem BOM: o servidor tambem aceita BOM, mas assim fica mais limpo
    [IO.File]::WriteAllLines($EnvArq, $linhas, (New-Object Text.UTF8Encoding($false)))
    Write-Host 'OK: configuracao salva.'
}

# --- atalhos -----------------------------------------------------------------
Titulo '4/5  Criando atalhos'
$sh = New-Object -ComObject WScript.Shell
$icone = Join-Path $Dest 'petagenda.ico'
function Criar-Atalho([string]$arquivo, [string]$argumentos, [string]$desc) {
    $a = $sh.CreateShortcut($arquivo)
    $a.TargetPath = $PS
    $a.Arguments = $argumentos
    $a.WorkingDirectory = $Dest
    $a.IconLocation = $icone
    $a.Description = $desc
    $a.WindowStyle = 7
    $a.Save()
}
$argsAbrir = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$(Join-Path $Dest 'abrir.ps1')`""
Criar-Atalho (Join-Path ([Environment]::GetFolderPath('Desktop')) 'PetAgenda.lnk') $argsAbrir 'Abrir o PetAgenda'
Criar-Atalho (Join-Path ([Environment]::GetFolderPath('Programs')) 'PetAgenda.lnk') $argsAbrir 'Abrir o PetAgenda'
Write-Host 'OK: atalho na Area de Trabalho e no Menu Iniciar.'

Write-Host ''
Write-Host 'O PetAgenda so envia lembretes e respostas enquanto estiver rodando.' -ForegroundColor Yellow
$auto = Read-Host 'Iniciar junto com o Windows (recomendado)? [S/n]'
$arqInicio = Join-Path ([Environment]::GetFolderPath('Startup')) 'PetAgenda (servidor).lnk'
if ($auto -notmatch '^[nN]') {
    Criar-Atalho $arqInicio ($argsAbrir + ' -SemNavegador') 'PetAgenda - servidor em segundo plano'
    Write-Host 'OK: vai iniciar com o Windows.'
} elseif (Test-Path $arqInicio) {
    Remove-Item $arqInicio -Force
}

# --- entrada em Aplicativos instalados ----------------------------------------
New-Item -Path $Chave -Force | Out-Null
$desinst = "`"$PS`" -NoProfile -ExecutionPolicy Bypass -File `"$(Join-Path $Dest 'desinstalar.ps1')`""
Set-ItemProperty $Chave -Name DisplayName     -Value 'PetAgenda'
Set-ItemProperty $Chave -Name DisplayVersion  -Value $Versao
Set-ItemProperty $Chave -Name Publisher       -Value 'Elvis Guski'
Set-ItemProperty $Chave -Name InstallLocation -Value $Dest
Set-ItemProperty $Chave -Name DisplayIcon     -Value $icone
Set-ItemProperty $Chave -Name UninstallString -Value $desinst
New-ItemProperty -Path $Chave -Name NoModify -Value 1 -PropertyType DWord -Force | Out-Null
New-ItemProperty -Path $Chave -Name NoRepair -Value 1 -PropertyType DWord -Force | Out-Null

# --- iniciar ----------------------------------------------------------------------
Titulo '5/5  Pronto!'
if ($atualizando) { Write-Host 'PetAgenda atualizado. Seus dados foram mantidos.' } else { Write-Host 'PetAgenda instalado.' }
Write-Host ''
Write-Host 'Dicas:'
Write-Host '  - Abra pelo atalho "PetAgenda" na Area de Trabalho.'
Write-Host '  - Em Configuracoes, conecte seu WhatsApp (o modo inicial e SIMULADO e nao envia nada).'
Write-Host '  - Para remover: Configuracoes do Windows > Aplicativos > PetAgenda > Desinstalar.'
Write-Host ''
$abrir = Read-Host 'Abrir o PetAgenda agora? [S/n]'
if ($abrir -notmatch '^[nN]') {
    Start-Process $PS -ArgumentList $argsAbrir -WindowStyle Hidden
}
exit 0
