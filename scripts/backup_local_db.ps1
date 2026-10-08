# Local PostgreSQL backup for excursion_ai (Windows PowerShell).
# Does not print the password. Writes dump under backups/ (.gitignore).

param(
    [string]$OutputDir = ""
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

if (-not $OutputDir) {
    $OutputDir = Join-Path $Root "backups"
}
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$dumpName = "excursion_ai_$stamp.dump"
$dumpPath = Join-Path $OutputDir $dumpName
$metaPath = Join-Path $OutputDir "excursion_ai_$stamp.meta.txt"

$py = Join-Path $Root ".venv\Scripts\python.exe"
$json = & $py -c @"
from excursion_ai.config import load_settings
s = load_settings(require_db=True)
print(s.db_host)
print(s.db_port)
print(s.db_name)
print(s.db_user)
print(s.db_password)
"@
$lines = @($json -split "`r?`n" | Where-Object { $_ -ne "" })
if ($lines.Count -lt 5) {
    throw "Failed to read DB settings from .env"
}
$dbHost = $lines[0]
$dbPort = $lines[1]
$dbName = $lines[2]
$dbUser = $lines[3]
$dbPassword = $lines[4]

$dockerHost = if ($dbHost -eq "127.0.0.1" -or $dbHost -eq "localhost") {
    "host.docker.internal"
} else {
    $dbHost
}

Write-Host "Creating dump: $dumpPath"
$env:PGPASSWORD = $dbPassword
try {
    docker run --rm `
        --add-host=host.docker.internal:host-gateway `
        -e PGPASSWORD `
        -v "${OutputDir}:/backup" `
        postgres:18-alpine `
        pg_dump `
        -h $dockerHost `
        -p $dbPort `
        -U $dbUser `
        -d $dbName `
        -Fc `
        --no-owner `
        --no-acl `
        -f "/backup/$dumpName"
    if ($LASTEXITCODE -ne 0) {
        throw "pg_dump failed with exit code $LASTEXITCODE"
    }
}
finally {
    Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
}

@(
    "created=$stamp"
    "db_name=$dbName"
    "db_user=$dbUser"
    "db_host=$dbHost"
    "db_port=$dbPort"
    "format=custom(-Fc)"
    "file=$dumpName"
) | Set-Content -Encoding UTF8 $metaPath

Write-Host "OK backup ready"
Write-Host "  dump: $dumpPath"
Write-Host "  meta: $metaPath"
