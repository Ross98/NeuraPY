# Cross-platform smoke check for Windows (PowerShell equivalent of check_platform.sh).
# Fails non-zero if any forbidden pattern is found under web/.
# Forbidden: os.fork, signal.SIGWINCH, /proc/, fcntl. (Linux-only or POSIX-specific)
#
# Usage (PowerShell or pwsh):
#     pwsh scripts/check_platform.ps1
$ErrorActionPreference = 'Stop'

$patterns = @(
    'os\.fork',
    'signal\.SIGWINCH',
    '/proc/',
    'fcntl\.'
)
$dirs = @('web/')
$fail = $false

foreach ($d in $dirs) {
    if (-not (Test-Path -LiteralPath $d)) { continue }
    $files = @(Get-ChildItem -Recurse -Path $d -Filter '*.py' -ErrorAction SilentlyContinue | Select-Object -ExpandProperty FullName)
    if ($files.Count -eq 0) { continue }
    $hits = Select-String -Path $files -Pattern ($patterns -join '|') -SimpleMatch:$false -ErrorAction SilentlyContinue
    if ($hits) {
        Write-Host "FAIL: non-portable API found in $d" -ForegroundColor Red
        $hits | ForEach-Object { Write-Host ("  {0}:{1}  {2}" -f $_.Path, $_.LineNumber, $_.Line.Trim()) }
        $fail = $true
    }
}

if (-not $fail) {
    Write-Host "OK: no non-portable APIs in $($dirs -join ', ')" -ForegroundColor Green
}
exit [int]$fail
